from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts

# 批次状态机：在途 → 托管中 →（完成）在途[归属接班方] /（取消、失败）在途[归属交班方]；在途 → 已结清
ST_INFLIGHT = "在途"
ST_ESCROW = "托管中"
ST_SETTLED = "已结清"

# 交班单状态机：进行中 → 已完成 / 已取消 / 失败
H_ACTIVE = "进行中"
H_DONE = "已完成"
H_CANCELLED = "已取消"
H_FAILED = "失败"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _escrow_blocker(conn, batch_id: int) -> dict | None:
    """返回占用该批次的托管记录（含交班单号），无则 None。"""
    return conn.execute(
        """
        SELECT eb.handover_id
        FROM escrow_batches eb
        JOIN handovers h ON h.id = eb.handover_id
        WHERE eb.batch_id = ? AND h.status = ?
        """,
        (batch_id, H_ACTIVE),
    ).fetchone()


def _require_not_escrow(conn, batch_id: int, action: str) -> None:
    blocker = _escrow_blocker(conn, batch_id)
    if blocker:
        raise HTTPException(
            409,
            f"批次#{batch_id} 处于托管中，禁止{action}（托管交班单#{blocker['handover_id']}）",
        )


# ---------------- 查询 ----------------

def list_shifts() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())


def list_batches(owner_shift_id: int | None = None) -> list[dict]:
    with connect() as conn:
        where = "WHERE b.owner_shift_id = ?" if owner_shift_id is not None else ""
        params = (owner_shift_id,) if owner_shift_id is not None else ()
        rows = conn.execute(
            f"""
            SELECT b.*, s.name AS owner_shift_name,
                   eb.handover_id AS escrow_handover_id
            FROM batches b
            LEFT JOIN shifts s ON s.id = b.owner_shift_id
            LEFT JOIN escrow_batches eb ON eb.batch_id = b.id
            {where}
            ORDER BY b.id
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def list_escrow(handover_id: int | None = None) -> list[dict]:
    """托管集合独立查询入口：与在途物理隔离，可按交班单过滤。"""
    with connect() as conn:
        where = "WHERE eb.handover_id = ?" if handover_id is not None else ""
        params = (handover_id,) if handover_id is not None else ()
        rows = conn.execute(
            f"""
            SELECT eb.handover_id, eb.batch_id, eb.from_shift_id, eb.escrowed_at,
                   b.biz_key, b.title, b.status AS batch_status,
                   h.status AS handover_status,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name
            FROM escrow_batches eb
            JOIN batches b ON b.id = eb.batch_id
            JOIN handovers h ON h.id = eb.handover_id
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            {where}
            ORDER BY eb.handover_id, eb.batch_id
            """,
            params,
        ).fetchall()
        return rows_to_dicts(rows)


def list_handovers() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name,
                   (SELECT COUNT(*) FROM escrow_batches eb WHERE eb.handover_id = h.id) AS escrow_count
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            ORDER BY h.id DESC
            """
        ).fetchall()
        return rows_to_dicts(rows)


def get_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name,
                   (SELECT COUNT(*) FROM escrow_batches eb WHERE eb.handover_id = h.id) AS escrow_count
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            WHERE h.id = ?
            """,
            (handover_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        items = conn.execute(
            """
            SELECT b.*, eb.escrowed_at, eb.handover_id AS escrow_handover_id
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            LEFT JOIN escrow_batches eb ON eb.batch_id = b.id AND eb.handover_id = hi.handover_id
            WHERE hi.handover_id = ?
            ORDER BY b.id
            """,
            (handover_id,),
        ).fetchall()
        out = dict(h)
        out["items"] = rows_to_dicts(items)
        return out


def dashboard() -> dict:
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        inflight = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = ?", (ST_INFLIGHT,)
        ).fetchone()["c"]
        escrow = conn.execute("SELECT COUNT(*) AS c FROM escrow_batches").fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = ?", (H_ACTIVE,)
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT * FROM handovers ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "班次数": len(shifts),
            "在途批次数": inflight,
            "托管批次数": escrow,
            "进行中交班": active,
            "最近交班": dict(last) if last else None,
            "班次": shifts,
        }


# ---------------- 批次操作（托管拦截） ----------------

def settle_batch(batch_id: int) -> dict:
    """结清：托管期内禁止。"""
    with connect() as conn:
        b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "批次不存在")
        _require_not_escrow(conn, batch_id, "结清")
        if b["status"] == ST_SETTLED:
            raise HTTPException(400, "批次已结清")
        conn.execute(
            "UPDATE batches SET status = ? WHERE id = ?", (ST_SETTLED, batch_id)
        )
        conn.commit()
    return {"id": batch_id, "status": ST_SETTLED}


def transfer_batch(batch_id: int, to_shift_id: int) -> dict:
    """改归属：托管期内禁止，冲突时写明托管交班单号。"""
    with connect() as conn:
        b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "批次不存在")
        ts = conn.execute("SELECT id FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not ts:
            raise HTTPException(404, "目标班次不存在")
        _require_not_escrow(conn, batch_id, "改归属")
        if b["status"] != ST_INFLIGHT:
            raise HTTPException(400, f"仅在途批次可改归属，当前状态：{b['status']}")
        if b["owner_shift_id"] == to_shift_id:
            raise HTTPException(400, "目标班次与当前归属相同")
        conn.execute(
            "UPDATE batches SET owner_shift_id = ? WHERE id = ?",
            (to_shift_id, batch_id),
        )
        row = conn.execute(
            """
            SELECT b.*, s.name AS owner_shift_name
            FROM batches b LEFT JOIN shifts s ON s.id = b.owner_shift_id
            WHERE b.id = ?
            """,
            (batch_id,),
        ).fetchone()
        result = dict(row)
        conn.commit()
    return result


# ---------------- 发起交班：进入托管 ----------------

def start_handover(from_shift_id: int, to_shift_id: int) -> dict:
    if from_shift_id == to_shift_id:
        raise HTTPException(400, "交班双方不能是同一班次")
    with connect() as conn:
        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            raise HTTPException(404, "班次不存在")
        active = conn.execute(
            "SELECT id FROM handovers WHERE status = ? LIMIT 1", (H_ACTIVE,)
        ).fetchone()
        if active:
            raise HTTPException(409, f"已有进行中的交班单#{active['id']}，禁止并行交班")
        # 只选取交班方名下的在途批次；托管中的批次不在任何一方在途，天然不会被再次选入
        batches = conn.execute(
            "SELECT id FROM batches WHERE owner_shift_id = ? AND status = ? ORDER BY id",
            (from_shift_id, ST_INFLIGHT),
        ).fetchall()
        if not batches:
            raise HTTPException(400, "交班方没有在途批次可交接")
        # 双保险：候选批次不得已在别的托管集合内
        for b in batches:
            blocker = _escrow_blocker(conn, b["id"])
            if blocker:
                raise HTTPException(
                    409,
                    f"批次#{b['id']} 已在托管交班单#{blocker['handover_id']} 中，禁止再次进入交班清单",
                )

        from_before = len(batches)
        to_before = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
            (to_shift_id, ST_INFLIGHT),
        ).fetchone()["c"]
        now = _now()
        cur = conn.execute(
            """
            INSERT INTO handovers(from_shift_id, to_shift_id, status, batch_count, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (from_shift_id, to_shift_id, H_ACTIVE, from_before, now),
        )
        hid = cur.lastrowid
        for b in batches:
            conn.execute(
                "INSERT INTO handover_items(handover_id, batch_id) VALUES (?, ?)",
                (hid, b["id"]),
            )
            # 进入托管：独立托管表挂交班单 id + 批次状态机切到托管态
            conn.execute(
                """
                INSERT INTO escrow_batches(handover_id, batch_id, from_shift_id, escrowed_at)
                VALUES (?, ?, ?, ?)
                """,
                (hid, b["id"], from_shift_id, now),
            )
            changed = conn.execute(
                "UPDATE batches SET status = ? WHERE id = ? AND status = ?",
                (ST_ESCROW, b["id"], ST_INFLIGHT),
            ).rowcount
            if changed != 1:
                # 整单回滚：禁止部分进入托管
                raise HTTPException(500, f"批次#{b['id']} 进入托管失败，整单回滚")

        # 发起时点对账快照：在途减少、托管承接、两侧在途均不含清单批次
        from_after = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
            (from_shift_id, ST_INFLIGHT),
        ).fetchone()["c"]
        to_after = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
            (to_shift_id, ST_INFLIGHT),
        ).fetchone()["c"]
        escrow_n = conn.execute(
            "SELECT COUNT(*) AS c FROM escrow_batches WHERE handover_id = ?", (hid,)
        ).fetchone()["c"]
        start_consistent = (
            escrow_n == from_before
            and from_after == 0
            and to_after == to_before
        )
        if not start_consistent:
            raise HTTPException(500, "托管进入校验失败，整单回滚")
        detail = {
            "时点": "发起交班",
            "交班单号": hid,
            "发起时交班方在途": from_before,
            "发起时接班方在途": to_before,
            "进入托管": escrow_n,
            "发起后交班方在途": from_after,
            "发起后接班方在途": to_after,
            "托管集合与清单同源": escrow_n == from_before,
            "解释": "发起时在途减少：清单批次整体进入托管，两侧在途均不含托管批次",
        }
        conn.execute(
            """
            INSERT INTO reconcile_reports(
              handover_id, exit_type, before_count, escrow_count,
              after_from_count, after_to_count, consistent, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hid, "发起", from_before, escrow_n,
                from_after, to_after, 1 if start_consistent else 0,
                json.dumps(detail, ensure_ascii=False), now,
            ),
        )

        conn.execute("UPDATE shifts SET status = '交班中' WHERE id = ?", (from_shift_id,))
        conn.execute("UPDATE shifts SET status = '接班中' WHERE id = ?", (to_shift_id,))
        conn.commit()
    return get_handover(hid)


# ---------------- 出口：完成 / 取消 / 失败，整单一致 ----------------

def _exit_handover(conn, handover_id: int, exit_status: str, reason: str) -> dict:
    h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
    if not h:
        raise HTTPException(404, "交班单不存在")
    if h["status"] != H_ACTIVE:
        raise HTTPException(400, f"只能处理进行中的交班单，当前状态：{h['status']}")

    # 出口草稿校验：清单必须与托管集合完全一致，且批次均处于托管态。
    # 任一不符直接抛错，外层事务回滚，杜绝部分出托管、部分残留。
    item_ids = [
        r["batch_id"]
        for r in conn.execute(
            "SELECT batch_id FROM handover_items WHERE handover_id = ? ORDER BY batch_id",
            (handover_id,),
        ).fetchall()
    ]
    escrow_rows = conn.execute(
        "SELECT batch_id FROM escrow_batches WHERE handover_id = ? ORDER BY batch_id",
        (handover_id,),
    ).fetchall()
    escrow_ids = [r["batch_id"] for r in escrow_rows]
    if not item_ids or item_ids != escrow_ids:
        raise HTTPException(
            409,
            f"交班单#{handover_id} 清单与托管集合不一致，禁止出口（清单{len(item_ids)}批/托管{len(escrow_ids)}批）",
        )
    placeholders = ",".join("?" for _ in escrow_ids)
    bad = conn.execute(
        f"SELECT id FROM batches WHERE id IN ({placeholders}) AND status != ?",
        (*escrow_ids, ST_ESCROW),
    ).fetchall()
    if bad:
        raise HTTPException(
            409,
            f"交班单#{handover_id} 存在非托管态批次#{','.join(str(r['id']) for r in bad)}，禁止出口",
        )

    from_sid, to_sid = h["from_shift_id"], h["to_shift_id"]
    n = len(escrow_ids)
    now = _now()

    if exit_status == H_DONE:
        # 完成：出托管并归属接班方
        changed = conn.execute(
            f"""
            UPDATE batches SET status = ?, owner_shift_id = ?
            WHERE id IN ({placeholders}) AND status = ?
            """,
            (ST_INFLIGHT, to_sid, *escrow_ids, ST_ESCROW),
        ).rowcount
        if changed != n:
            raise HTTPException(500, "完成切换行数不符，整单回滚")
        shift_from, shift_to = "已交班", "值班中"
    else:
        # 取消 / 失败：出托管并回到交班方在途（owner 本就未变，显式复位以防外部改动）
        changed = conn.execute(
            f"""
            UPDATE batches SET status = ?, owner_shift_id = ?
            WHERE id IN ({placeholders}) AND status = ?
            """,
            (ST_INFLIGHT, from_sid, *escrow_ids, ST_ESCROW),
        ).rowcount
        if changed != n:
            raise HTTPException(500, "回退切换行数不符，整单回滚")
        shift_from, shift_to = "值班中", "空闲"

    deleted = conn.execute(
        f"DELETE FROM escrow_batches WHERE handover_id = ? AND batch_id IN ({placeholders})",
        (handover_id, *escrow_ids),
    ).rowcount
    if deleted != n:
        raise HTTPException(500, "托管解除行数不符，整单回滚")

    # 出口后同源核对：托管清零、清单批次全部回到在途且归属正确
    left_escrow = conn.execute(
        "SELECT COUNT(*) AS c FROM escrow_batches WHERE handover_id = ?", (handover_id,)
    ).fetchone()["c"]
    from_after = conn.execute(
        "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
        (from_sid, ST_INFLIGHT),
    ).fetchone()["c"]
    to_after = conn.execute(
        "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = ?",
        (to_sid, ST_INFLIGHT),
    ).fetchone()["c"]
    if exit_status == H_DONE:
        consistent = left_escrow == 0 and to_after >= n and from_after >= 0
        # 更严格：清单批次必须全部在接班方名下在途
        landed = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM batches
            WHERE id IN ({placeholders}) AND owner_shift_id = ? AND status = ?
            """,
            (*escrow_ids, to_sid, ST_INFLIGHT),
        ).fetchone()["c"]
        consistent = consistent and landed == n
    else:
        restored = conn.execute(
            f"""
            SELECT COUNT(*) AS c FROM batches
            WHERE id IN ({placeholders}) AND owner_shift_id = ? AND status = ?
            """,
            (*escrow_ids, from_sid, ST_INFLIGHT),
        ).fetchone()["c"]
        consistent = left_escrow == 0 and restored == n

    conn.execute(
        "UPDATE handovers SET status = ?, finished_at = ?, batch_count = ?, reason = ? WHERE id = ?",
        (exit_status, now, n, reason, handover_id),
    )
    conn.execute("UPDATE shifts SET status = ? WHERE id = ?", (shift_from, from_sid))
    conn.execute("UPDATE shifts SET status = ? WHERE id = ?", (shift_to, to_sid))

    exit_cn = {"已完成": "完成交班", "已取消": "取消交班", "失败": "交班失败"}[exit_status]
    explain = {
        "已完成": "完成时接班方增加：托管批次整体出托管并过户接班方，交班方在途不回补",
        "已取消": "失败/取消时交班方恢复：托管批次整体出托管并回到交班方在途",
        "失败": "失败/取消时交班方恢复：托管批次整体出托管并回到交班方在途",
    }[exit_status]
    detail = {
        "时点": exit_cn,
        "交班单号": handover_id,
        "出口类型": exit_status,
        "清单批次数": n,
        "出口前托管数": n,
        "出口后托管残留": left_escrow,
        "出口后交班方在途": from_after,
        "出口后接班方在途": to_after,
        "整单一致": bool(consistent),
        "解释": explain,
        "原因": reason,
    }
    conn.execute(
        """
        INSERT INTO reconcile_reports(
          handover_id, exit_type, before_count, escrow_count,
          after_from_count, after_to_count, consistent, detail_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            handover_id, exit_cn, n, n,
            from_after, to_after, 1 if consistent else 0,
            json.dumps(detail, ensure_ascii=False), now,
        ),
    )
    if not consistent:
        # 校验不过不允许留下半切换状态：整单回滚
        raise HTTPException(500, f"交班单#{handover_id} 出口一致性校验失败，整单回滚")


def complete_handover(handover_id: int) -> dict:
    with connect() as conn:
        _exit_handover(conn, handover_id, H_DONE, "")
        conn.commit()
    return get_handover(handover_id)


def cancel_handover(handover_id: int, reason: str = "人工取消交班") -> dict:
    with connect() as conn:
        _exit_handover(conn, handover_id, H_CANCELLED, reason)
        conn.commit()
    return get_handover(handover_id)


def fail_handover(handover_id: int, reason: str = "交班失败，批次退回交班方") -> dict:
    with connect() as conn:
        _exit_handover(conn, handover_id, H_FAILED, reason)
        conn.commit()
    return get_handover(handover_id)


# ---------------- 对账 ----------------

def list_reconcile() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reconcile_reports ORDER BY id DESC"
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            d["一致"] = bool(d.pop("consistent"))
            out.append(d)
        return out
