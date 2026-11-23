"""偏移量提交台账路由：提交入链、进度签收、重新进入查询与推进链核对。

固定路径前缀 /api/offset-commits，与通用资源路由 /api/{module} 区分开。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.offset_commit_ledger import offset_commit_ledger

router = APIRouter(prefix="/api/offset-commits", tags=["偏移量提交台账"])


class CommitPayload(BaseModel):
    group: str = Field(..., description="消费组")
    topic: str = Field(..., description="Topic/Queue")
    partition: int = Field(..., ge=0, description="分区/分片")
    offset: int = Field(..., ge=0, description="提交的偏移量")
    source: str = Field(..., description="提交来源，相同偏移不同来源各有归属")


class LookupPayload(BaseModel):
    group: str
    topic: str
    partition: int = Field(..., ge=0)
    offset: int = Field(..., ge=0)
    source: str


@router.post("/submit")
def submit_commit(payload: CommitPayload) -> dict[str, Any]:
    """提交一次偏移量推进；重复提交幂等归档，返回首次生效回执。"""
    return offset_commit_ledger.submit(
        payload.group, payload.topic, payload.partition, payload.offset, payload.source
    )


@router.post("/acknowledge")
def acknowledge_commit(payload: CommitPayload) -> dict[str, Any]:
    """进度签收；重复签收幂等，按链顺序结算可见偏移，不回退已生效进度。"""
    return offset_commit_ledger.acknowledge(
        payload.group, payload.topic, payload.partition, payload.offset, payload.source
    )


@router.post("/lookup")
def lookup_commit(payload: LookupPayload) -> dict[str, Any]:
    """提交方重新进入时查询某次提交是否已经生效。"""
    return offset_commit_ledger.lookup(
        payload.group, payload.topic, payload.partition, payload.offset, payload.source
    )


@router.get("/progress")
def list_progress(group: str | None = None) -> dict[str, Any]:
    """查看各分区的已收偏移、可见偏移与下一个提交编号。"""
    items = offset_commit_ledger.list_progress(group)
    return {"ok": True, "total": len(items), "items": items}


@router.get("/receipts")
def list_receipts(group: str | None = None, partition: int | None = None) -> dict[str, Any]:
    """查看提交回执（含重复归档计数与签收/可见状态）。"""
    items = offset_commit_ledger.list_receipts(group, partition)
    return {"ok": True, "total": len(items), "items": items}


@router.get("/chain")
def chain_snapshot(group: str, topic: str, partition: int) -> dict[str, Any]:
    """核对单个分区的推进链顺序、签收情况与可见推进数。"""
    return offset_commit_ledger.chain_snapshot(group, topic, partition)


@router.post("/reset")
def reset_ledger() -> dict[str, Any]:
    """清空台账，供场景演练/测试重新开始。"""
    offset_commit_ledger.reset()
    return {"ok": True, "message": "偏移量提交台账已重置"}
