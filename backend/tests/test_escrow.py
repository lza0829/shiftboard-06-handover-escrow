from pathlib import Path
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402
from app.db import connect  # noqa: E402


@pytest.fixture(scope="module")
def client():
    data_dir = ROOT / "backend" / "data_test_escrow"
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["FIXTURES_DIR"] = str(ROOT / "fixtures")
    db_path = data_dir / "app.db"
    if db_path.exists():
        db_path.unlink()
    with TestClient(app) as c:
        yield c


def _inflight(client, shift_id):
    rows = client.get("/api/batches", params={"owner_shift_id": shift_id}).json()
    return [b for b in rows if b["status"] == "在途"]


def test_start_moves_batches_into_escrow_not_either_side(client):
    # 初始：交班方(1) 4 个在途，接班方(2) 0 个在途
    assert len(_inflight(client, 1)) == 4
    assert len(_inflight(client, 2)) == 0

    r = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 2})
    assert r.status_code == 200, r.text
    hid = r.json()["id"]

    # 两侧在途都不含托管批次
    assert _inflight(client, 1) == []
    assert _inflight(client, 2) == []

    # 批次状态机切到托管态，归属仍挂交班方（禁止发起时直接改到接班方）
    batches = client.get("/api/batches").json()
    hosted = [b for b in batches if b["escrow_handover_id"] == hid]
    assert len(hosted) == 4
    assert all(b["status"] == "托管中" for b in hosted)
    assert all(b["owner_shift_id"] == 1 for b in hosted)

    # 托管集合独立可查，挂交班单 id，与在途物理隔离
    escrow = client.get("/api/handovers/escrow", params={"handover_id": hid}).json()
    assert {e["batch_id"] for e in escrow} == {b["id"] for b in hosted}
    assert all(e["handover_id"] == hid for e in escrow)

    # 总览：在途数不含托管，托管单列
    dash = client.get("/api/dashboard").json()
    assert dash["在途批次数"] == 0
    assert dash["托管批次数"] == 4
    assert dash["进行中交班"] == 1


def test_escrow_blocks_settle_transfer_and_reentry(client):
    hid = client.get("/api/handovers").json()[0]["id"]
    escrow = client.get("/api/handovers/escrow", params={"handover_id": hid}).json()
    bid = escrow[0]["batch_id"]

    # 托管期禁止结清：冲突写明托管交班单号
    r = client.post(f"/api/batches/{bid}/settle")
    assert r.status_code == 409
    assert f"#{hid}" in r.text

    # 托管期禁止改归属
    r = client.post(f"/api/batches/{bid}/transfer", json={"to_shift_id": 3})
    assert r.status_code == 409
    assert f"#{hid}" in r.text

    # 禁止再次进入别的交班清单（全局并行交班互斥 + 批次已不在在途）
    r = client.post("/api/handovers/start", json={"from_shift_id": 1, "to_shift_id": 3})
    assert r.status_code == 409


def test_complete_exits_escrow_to_taking_shift_atomically(client):
    hid = client.get("/api/handovers").json()[0]["id"]
    r = client.post(f"/api/handovers/{hid}/complete")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "已完成"

    # 托管清零，清单批次整体在接班方在途
    assert client.get("/api/handovers/escrow").json() == []
    assert _inflight(client, 1) == []
    landed = _inflight(client, 2)
    assert len(landed) == 4

    dash = client.get("/api/dashboard").json()
    assert dash["在途批次数"] == 4
    assert dash["托管批次数"] == 0

    # 对账：发起报告 + 完成报告同源，均一致
    reports = client.get("/api/reconcile").json()
    kinds = {rep["detail"]["时点"] for rep in reports if rep["handover_id"] == hid}
    assert {"发起交班", "完成交班"} <= kinds
    assert all(rep["一致"] for rep in reports if rep["handover_id"] == hid)
    start_rep = next(rep for rep in reports if rep["detail"]["时点"] == "发起交班")
    assert start_rep["escrow_count"] == 4
    assert start_rep["after_from_count"] == 0
    done_rep = next(rep for rep in reports if rep["detail"]["时点"] == "完成交班")
    assert done_rep["after_to_count"] == 4
    assert done_rep["detail"]["出口后托管残留"] == 0


def test_cancel_returns_escrow_to_from_shift(client):
    # 接班方(2) 现持 4 个在途，向 3 发起后取消
    r = client.post("/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3})
    assert r.status_code == 200, r.text
    hid = r.json()["id"]
    assert client.get("/api/dashboard").json()["托管批次数"] == 4

    r = client.post(f"/api/handovers/{hid}/cancel", json={"reason": "接班方暂不接"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "已取消"

    # 整单回到交班方在途，无托管残留
    assert client.get("/api/handovers/escrow").json() == []
    assert len(_inflight(client, 2)) == 4
    assert _inflight(client, 3) == []
    rep = client.get("/api/reconcile").json()[0]
    assert rep["detail"]["时点"] == "取消交班"
    assert rep["一致"] is True


def test_fail_returns_escrow_to_from_shift(client):
    r = client.post("/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3})
    assert r.status_code == 200, r.text
    hid = r.json()["id"]

    r = client.post(f"/api/handovers/{hid}/fail", json={"reason": "核验异常"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "失败"
    assert r.json()["reason"] == "核验异常"

    assert client.get("/api/handovers/escrow").json() == []
    assert len(_inflight(client, 2)) == 4
    rep = client.get("/api/reconcile").json()[0]
    assert rep["detail"]["时点"] == "交班失败"
    assert rep["一致"] is True


def test_exit_rolls_back_when_escrow_set_diverges(client):
    # 直接破坏托管集合（模拟部分出托管/残留），出口必须整单拒绝且状态不变
    r = client.post("/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3})
    assert r.status_code == 200, r.text
    hid = r.json()["id"]
    escrow = client.get("/api/handovers/escrow", params={"handover_id": hid}).json()
    assert len(escrow) == 4

    with connect() as conn:
        conn.execute(
            "DELETE FROM escrow_batches WHERE handover_id = ? AND batch_id = ?",
            (hid, escrow[0]["batch_id"]),
        )
        conn.commit()

    r = client.post(f"/api/handovers/{hid}/complete")
    assert r.status_code == 409
    # 交班单仍进行中，批次仍托管态，未发生部分过户
    h = client.get(f"/api/handovers/{hid}").json()
    assert h["status"] == "进行中"
    b0 = next(b for b in client.get("/api/batches").json() if b["id"] == escrow[0]["batch_id"])
    assert b0["status"] == "托管中"
    assert b0["owner_shift_id"] == 2

    # 修复托管集合后出口可正常完成
    with connect() as conn:
        conn.execute(
            "INSERT INTO escrow_batches(handover_id, batch_id, from_shift_id, escrowed_at) VALUES (?, ?, ?, ?)",
            (hid, escrow[0]["batch_id"], 2, escrow[0]["escrowed_at"]),
        )
        conn.commit()
    r = client.post(f"/api/handovers/{hid}/fail", json={"reason": "先回退"})
    assert r.status_code == 200, r.text
    assert len(_inflight(client, 2)) == 4
    assert client.get("/api/handovers/escrow").json() == []


def test_reports_are_same_source_as_escrow_and_inflight(client):
    reports = client.get("/api/reconcile").json()
    for rep in reports:
        detail = rep["detail"]
        # 数字字段与 detail 同源（同一事务写入）
        assert rep["before_count"] == detail.get(
            "发起时交班方在途", detail.get("清单批次数")
        )
        assert isinstance(rep["escrow_count"], int)
        assert json.dumps(detail, ensure_ascii=False)
