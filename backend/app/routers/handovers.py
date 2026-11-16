from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(prefix="/handovers", tags=["交班"])


class StartBody(BaseModel):
    from_shift_id: int = Field(..., description="交班班次")
    to_shift_id: int = Field(..., description="接班班次")


class FailBody(BaseModel):
    reason: str = Field(default="交班失败，批次退回交班方")


class CancelBody(BaseModel):
    reason: str = Field(default="人工取消交班")


@router.get("")
def get_handovers():
    return svc.list_handovers()


@router.get("/escrow")
def get_escrow(handover_id: int | None = None):
    """托管集合独立查询：与在途物理隔离，挂交班单 id 可查。"""
    return svc.list_escrow(handover_id)


@router.get("/{handover_id}")
def get_one(handover_id: int):
    return svc.get_handover(handover_id)


@router.post("/start")
def start(body: StartBody):
    return svc.start_handover(body.from_shift_id, body.to_shift_id)


@router.post("/{handover_id}/complete")
def complete(handover_id: int):
    return svc.complete_handover(handover_id)


@router.post("/{handover_id}/cancel")
def cancel(handover_id: int, body: CancelBody):
    return svc.cancel_handover(handover_id, body.reason)


@router.post("/{handover_id}/fail")
def fail(handover_id: int, body: FailBody):
    return svc.fail_handover(handover_id, body.reason)
