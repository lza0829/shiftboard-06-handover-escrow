from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.services import handover as svc

router = APIRouter(prefix="/batches", tags=["在途批次"])


class TransferBody(BaseModel):
    to_shift_id: int = Field(..., description="新归属班次")


@router.get("")
def get_batches(owner_shift_id: int | None = Query(default=None)):
    return svc.list_batches(owner_shift_id)


@router.post("/{batch_id}/settle")
def settle(batch_id: int):
    """结清批次；托管期内禁止，冲突返回托管交班单号。"""
    return svc.settle_batch(batch_id)


@router.post("/{batch_id}/transfer")
def transfer(batch_id: int, body: TransferBody):
    """改归属；托管期内禁止，冲突返回托管交班单号。"""
    return svc.transfer_batch(batch_id, body.to_shift_id)
