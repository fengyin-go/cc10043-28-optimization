"""偏移量提交台账：保证提交记录与分区进度数据入链一致。

领域语义（对应需求）：

1. 每个消费组 + Topic + 分区各自维护一条推进链和独立的提交编号（seq），
   两个分区互不串号。
2. 同一次确认（group/topic/partition/offset/source 相同）重复送达只认第一次：
   首次提交生成正式提交回执并入链；重复提交不再产生新的链上记录，而是把旧回执
   标记为重复并归档到 duplicate_of，提交方重新进入时凭幂等键即可知道哪一次已生效。
3. 相同偏移量但来源（source）不同的确认互不冲突，各自有独立归属与回执。
4. 进度签收（ack）按推进链顺序生效：只有链上前序提交都已交接，当前提交才能成为
   可见提交，已可见的偏移不能跳过前面尚未签收的进度。
5. 对账不会因为迟到签收或重复提交而退回：签收只允许把状态向「已签收」推进，
   重复提交与迟到签收都不会回滚已生效的进度。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.store import store

RECEIPT_TABLE = "offset_commit_receipts"
PROGRESS_TABLE = "offset_partition_progress"

# 回执生命周期
STATUS_PENDING = "待签收"      # 已入链，等待进度签收
STATUS_ACKED = "已签收"        # 签收已生效（可能已被可见进度覆盖，仍是链上有效记录）
STATUS_DUPLICATE = "重复归档"  # 重复提交，归档到首次回执，不再独立推进进度


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _partition_key(group: str, topic: str, partition: int) -> tuple[str, str, int]:
    return group, topic, partition


class OffsetCommitLedger:
    """内存态提交台账；所有变更都在同一仓库内同步更新回执与分区进度。"""

    # ------------------------------------------------------------------
    # 分区进度
    # ------------------------------------------------------------------
    def _get_progress(self, group: str, topic: str, partition: int) -> dict[str, Any]:
        rows = store.rows(PROGRESS_TABLE)
        for row in rows:
            if (
                row["消费组"] == group
                and row["Topic/Queue"] == topic
                and int(row["分区/分片"]) == partition
            ):
                return row
        progress = {
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "消费组": group,
            "Topic/Queue": topic,
            "分区/分片": partition,
            # 已收偏移：该分区链上最近一次提交的偏移（不要求连续签收）。
            "received_offset": -1,
            # 可见偏移：链上按顺序连续签收后，对外可见的消费位置。
            "visible_offset": -1,
            "next_seq": 1,
            "updated_at": _now(),
        }
        rows.append(progress)
        return progress

    def list_progress(self, group: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(PROGRESS_TABLE)
        if group is None:
            return [dict(row) for row in rows]
        return [dict(row) for row in rows if row["消费组"] == group]

    # ------------------------------------------------------------------
    # 回执查询
    # ------------------------------------------------------------------
    def _find_receipt(
        self, group: str, topic: str, partition: int, offset: int, source: str
    ) -> dict[str, Any] | None:
        for row in store.rows(RECEIPT_TABLE):
            if (
                row["消费组"] == group
                and row["Topic/Queue"] == topic
                and int(row["分区/分片"]) == partition
                and int(row["offset"]) == offset
                and row["source"] == source
            ):
                return row
        return None

    def _find_by_id(self, receipt_id: int) -> dict[str, Any] | None:
        for row in store.rows(RECEIPT_TABLE):
            if int(row["id"]) == receipt_id:
                return row
        return None

    def list_receipts(
        self, group: str | None = None, partition: int | None = None
    ) -> list[dict[str, Any]]:
        rows = store.rows(RECEIPT_TABLE)
        result = []
        for row in rows:
            if group is not None and row["消费组"] != group:
                continue
            if partition is not None and int(row["分区/分片"]) != partition:
                continue
            result.append(dict(row))
        return result

    # ------------------------------------------------------------------
    # 提交入链
    # ------------------------------------------------------------------
    def submit(
        self, group: str, topic: str, partition: int, offset: int, source: str
    ) -> dict[str, Any]:
        """提交一次偏移量推进；重复提交幂等返回首次回执并归档本次请求。"""
        progress = self._get_progress(group, topic, partition)
        existing = self._find_receipt(group, topic, partition, offset, source)

        if existing is not None:
            # 同一确认重复送达：只认第一次。本次不新增链上记录，归档到首次回执，
            # 提交方重新进入时凭回执即可知道哪一次已经生效，进度不重复入账。
            archived = self._archive_duplicate(existing, progress)
            return {
                "ok": True,
                "duplicate": True,
                "effective": False,
                "message": (
                    f"重复提交已归档：分区{partition} 偏移{offset} 来源{source} "
                    f"首次生效于回执#{existing['id']}（提交编号{existing['seq']}）"
                ),
                "receipt": dict(existing),
                "archived_attempt": archived,
                "progress": self._progress_view(progress),
            }

        # 全新提交：分配该分区自己的提交编号，入链到分区推进链尾部。
        seq = int(progress["next_seq"])
        receipt_id = max(
            (int(r.get("id", 0)) for r in store.rows(RECEIPT_TABLE)), default=0
        ) + 1
        receipt = {
            "id": receipt_id,
            "消费组": group,
            "Topic/Queue": topic,
            "分区/分片": partition,
            "seq": seq,
            "offset": offset,
            "source": source,
            "status": STATUS_PENDING,
            "acked": False,
            "duplicate_count": 0,
            "duplicate_of": None,
            "visible": False,
            "idempotency_key": self._idempotency_key(
                group, topic, partition, offset, source
            ),
            "created_at": _now(),
            "acked_at": None,
            "archived_attempts": [],
        }
        store.rows(RECEIPT_TABLE).append(receipt)
        progress["next_seq"] = seq + 1
        progress["received_offset"] = max(int(progress["received_offset"]), offset)
        progress["updated_at"] = _now()
        return {
            "ok": True,
            "duplicate": False,
            "effective": True,
            "message": (
                f"提交已入链：分区{partition} 提交编号{seq} 偏移{offset} 来源{source}"
            ),
            "receipt": dict(receipt),
            "progress": self._progress_view(progress),
        }

    def _archive_duplicate(
        self, original: dict[str, Any], progress: dict[str, Any]
    ) -> dict[str, Any]:
        attempt = {"at": _now(), "archived_to_receipt": int(original["id"])}
        original.setdefault("archived_attempts", []).append(attempt)
        original["duplicate_count"] = int(original.get("duplicate_count", 0)) + 1
        # 重复提交本身不改动 received/visible，进度只由首次回执及其签收驱动。
        progress["updated_at"] = _now()
        return attempt

    # ------------------------------------------------------------------
    # 进度签收
    # ------------------------------------------------------------------
    def acknowledge(
        self,
        group: str,
        topic: str,
        partition: int,
        offset: int,
        source: str,
    ) -> dict[str, Any]:
        """对一次提交做进度签收；重复签收幂等，绝不回退已生效进度。"""
        progress = self._get_progress(group, topic, partition)
        receipt = self._find_receipt(group, topic, partition, offset, source)
        if receipt is None:
            return {
                "ok": False,
                "message": (
                    f"签收失败：分区{partition} 偏移{offset} 来源{source} 无对应提交回执"
                ),
                "progress": self._progress_view(progress),
            }

        already = bool(receipt["acked"])
        if not already:
            receipt["acked"] = True
            receipt["status"] = STATUS_ACKED
            receipt["acked_at"] = _now()
        # 无论是否重复签收，都重新结算连续可见偏移，保证迟到签收能补账。
        promoted = self._promote_visible(progress)
        return {
            "ok": True,
            "duplicate_ack": already,
            "message": (
                f"签收{'重复送达已忽略' if already else '已生效'}："
                f"分区{partition} 提交编号{receipt['seq']} 偏移{offset}"
            ),
            "receipt": dict(receipt),
            "promoted": promoted,
            "progress": self._progress_view(progress),
        }

    def _promote_visible(self, progress: dict[str, Any]) -> list[dict[str, Any]]:
        """按提交编号顺序结算连续签收的提交，更新可见偏移。

        只有链上前序提交都已签收，当前提交才能转为可见；未签收的提交会挡住后面
        所有提交（即使后面的已签收），从而保证可见提交不跳过尚未交接的进度。
        """
        group, topic, partition = _partition_key(
            progress["消费组"], progress["Topic/Queue"], int(progress["分区/分片"])
        )
        chain = sorted(
            (
                row
                for row in store.rows(RECEIPT_TABLE)
                if row["消费组"] == group
                and row["Topic/Queue"] == topic
                and int(row["分区/分片"]) == partition
                and row["status"] != STATUS_DUPLICATE
            ),
            key=lambda row: int(row["seq"]),
        )
        promoted: list[dict[str, Any]] = []
        visible = int(progress["visible_offset"])
        for row in chain:
            if not row["acked"]:
                # 遇到第一个未签收提交即停止，后续提交保持不可见。
                break
            if not row["visible"]:
                row["visible"] = True
                promoted.append(
                    {"receipt_id": row["id"], "seq": row["seq"], "offset": row["offset"]}
                )
            # 可见偏移沿链单调推进，绝不回退。
            visible = max(visible, int(row["offset"]))
        progress["visible_offset"] = visible
        progress["updated_at"] = _now()
        return promoted

    # ------------------------------------------------------------------
    # 重新进入 / 恢复：提交方查询哪一次已经生效
    # ------------------------------------------------------------------
    def lookup(
        self, group: str, topic: str, partition: int, offset: int, source: str
    ) -> dict[str, Any]:
        receipt = self._find_receipt(group, topic, partition, offset, source)
        progress = self._get_progress(group, topic, partition)
        if receipt is None:
            return {
                "ok": True,
                "known": False,
                "message": "该提交从未入链，需要重新提交",
                "progress": self._progress_view(progress),
            }
        return {
            "ok": True,
            "known": True,
            "effective": receipt["status"] != STATUS_DUPLICATE,
            "duplicate_archived": receipt["status"] == STATUS_DUPLICATE,
            "message": (
                f"提交编号{receipt['seq']} 已生效，状态{receipt['status']}，"
                f"重复送达{receipt['duplicate_count']}次"
            ),
            "receipt": dict(receipt),
            "progress": self._progress_view(progress),
        }

    def chain_snapshot(self, group: str, topic: str, partition: int) -> dict[str, Any]:
        """输出一个分区的完整推进链，供恢复后核对顺序与可见推进数。"""
        progress = self._get_progress(group, topic, partition)
        chain = [
            dict(row)
            for row in sorted(
                (
                    row
                    for row in store.rows(RECEIPT_TABLE)
                    if row["消费组"] == group
                    and row["Topic/Queue"] == topic
                    and int(row["分区/分片"]) == partition
                ),
                key=lambda row: int(row["seq"]),
            )
        ]
        view = self._progress_view(progress)
        view["chain"] = chain
        view["visible_advance_count"] = sum(1 for row in chain if row["visible"])
        view["acked_count"] = sum(1 for row in chain if row["acked"])
        view["pending_count"] = sum(
            1 for row in chain if row["status"] == STATUS_PENDING
        )
        return view

    # ------------------------------------------------------------------
    # 测试/场景辅助
    # ------------------------------------------------------------------
    def reset(self) -> None:
        store.rows(RECEIPT_TABLE).clear()
        store.rows(PROGRESS_TABLE).clear()

    @staticmethod
    def _idempotency_key(
        group: str, topic: str, partition: int, offset: int, source: str
    ) -> str:
        return f"{group}|{topic}|{partition}|{offset}|{source}"

    @staticmethod
    def _progress_view(progress: dict[str, Any]) -> dict[str, Any]:
        return {
            "消费组": progress["消费组"],
            "Topic/Queue": progress["Topic/Queue"],
            "分区/分片": progress["分区/分片"],
            "received_offset": progress["received_offset"],
            "visible_offset": progress["visible_offset"],
            "next_seq": progress["next_seq"],
        }


offset_commit_ledger = OffsetCommitLedger()
