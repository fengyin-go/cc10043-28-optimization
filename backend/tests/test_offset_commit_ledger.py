"""偏移量提交台账场景演练：覆盖题述的迟到签收、重复提交与双分区推进链。

运行（在 backend 目录）：python3 -I tests/test_offset_commit_ledger.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.offset_commit_ledger import (  # noqa: E402
    STATUS_ACKED,
    STATUS_PENDING,
    offset_commit_ledger as ledger,
)

GROUP = "cg-demo"
TOPIC = "orders"
P0 = 0
P1 = 1
SRC_A = "producer-a"
SRC_B = "producer-b"

failures: list[str] = []


def check(name: str, condition: bool, detail: object = None) -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" -> {detail}" if detail is not None else ""))
    if not condition:
        failures.append(name)


def main() -> int:
    ledger.reset()

    # ---- 分区0：已收偏移 41（已签收，作为基线） ----
    r41 = ledger.submit(GROUP, TOPIC, P0, 41, SRC_A)
    check("p0 41 入链 seq=1", r41["receipt"]["seq"] == 1, r41["receipt"]["seq"])
    a41 = ledger.acknowledge(GROUP, TOPIC, P0, 41, SRC_A)
    check("p0 41 签收后可见偏移=41", a41["progress"]["visible_offset"] == 41, a41["progress"])

    # ---- 42 入链，签收迟到；43 随后到达 ----
    r42 = ledger.submit(GROUP, TOPIC, P0, 42, SRC_A)
    r43 = ledger.submit(GROUP, TOPIC, P0, 43, SRC_A)
    check("p0 提交编号各自递增 42->2, 43->3",
          r42["receipt"]["seq"] == 2 and r43["receipt"]["seq"] == 3)
    check("p0 已收偏移=43（入链即可见已收）",
          r43["progress"]["received_offset"] == 43, r43["progress"])
    check("p0 42 未签收时可见偏移仍停在41，43不能跳过42",
          r43["progress"]["visible_offset"] == 41, r43["progress"])
    check("p0 43 回执已发放但不可见", r43["receipt"]["visible"] is False)

    # ---- 重复提交 42：不能对账退回，也不能漏记 ----
    dup42 = ledger.submit(GROUP, TOPIC, P0, 42, SRC_A)
    check("重复提交42 被识别为 duplicate", dup42["duplicate"] is True)
    check("重复提交42 指向首次回执#2", dup42["receipt"]["id"] == r42["receipt"]["id"])
    check("重复提交42 归档计数+1", dup42["receipt"]["duplicate_count"] == 1,
          dup42["receipt"]["duplicate_count"])
    check("重复提交42 不产生新链上记录",
          len(ledger.list_receipts(GROUP, P0)) == 3)
    check("重复提交42 进度不回退、不虚增",
          dup42["progress"]["visible_offset"] == 41
          and dup42["progress"]["received_offset"] == 43, dup42["progress"])

    # ---- 相同偏移 42、来源不同：各有归属，独立回执 ----
    r42b = ledger.submit(GROUP, TOPIC, P0, 42, SRC_B)
    check("不同来源的同偏移42 独立入链 seq=4",
          r42b["effective"] is True and r42b["receipt"]["seq"] == 4, r42b["receipt"])
    check("不同来源的同偏移42 幂等键不同",
          r42b["receipt"]["idempotency_key"] != r42["receipt"]["idempotency_key"])

    # ---- 让 43 先拿到传输回执（已入链），再找回 42 的迟到签收 ----
    check("43 先于42签收也不能越级可见",
          ledger.acknowledge(GROUP, TOPIC, P0, 43, SRC_A)["progress"]["visible_offset"] == 41)
    ack42 = ledger.acknowledge(GROUP, TOPIC, P0, 42, SRC_A)
    check("42 迟到签收后连续补账，可见偏移推进到43",
          ack42["progress"]["visible_offset"] == 43, ack42["progress"])
    promoted_ids = [item["receipt_id"] for item in ack42["promoted"]]
    check("补账顺序 42 在前 43 在后",
          promoted_ids == [r42["receipt"]["id"], r43["receipt"]["id"]], promoted_ids)

    # 重复签收：第一次生效，再次送达只认第一次
    ack42_again = ledger.acknowledge(GROUP, TOPIC, P0, 42, SRC_A)
    check("重复签收42 幂等忽略且不回退",
          ack42_again["duplicate_ack"] is True
          and ack42_again["progress"]["visible_offset"] == 43)

    # ---- 分区1：独立提交编号与推进链，提交自己的 42 ----
    rp1_42 = ledger.submit(GROUP, TOPIC, P1, 42, SRC_A)
    check("p1 提交编号从1重新开始（与p0独立）",
          rp1_42["receipt"]["seq"] == 1, rp1_42["receipt"])
    check("p1 初始可见偏移=-1，不与p0串通",
          rp1_42["progress"]["visible_offset"] == -1, rp1_42["progress"])
    ap1 = ledger.acknowledge(GROUP, TOPIC, P1, 42, SRC_A)
    check("p1 签收后可见偏移=42", ap1["progress"]["visible_offset"] == 42, ap1["progress"])

    # ---- 核对两个分区的消费位置顺序 ----
    progress = {item["分区/分片"]: item for item in ledger.list_progress(GROUP)}
    check("双分区消费位置 p0=43 / p1=42",
          progress[P0]["visible_offset"] == 43 and progress[P1]["visible_offset"] == 42,
          progress)

    # ---- 恢复后同一提交的回执：重新进入凭幂等键知道哪次已生效 ----
    look0 = ledger.lookup(GROUP, TOPIC, P0, 42, SRC_A)
    check("重新进入 p0/42/A 查到首次回执且已签收",
          look0["known"] is True and look0["receipt"]["id"] == r42["receipt"]["id"]
          and look0["receipt"]["status"] == STATUS_ACKED, look0.get("receipt"))
    look_unknown = ledger.lookup(GROUP, TOPIC, P1, 99, SRC_A)
    check("重新进入 未入链提交 提示需重新提交", look_unknown["known"] is False)

    # ---- 可见推进数与链顺序核对 ----
    snap0 = ledger.chain_snapshot(GROUP, TOPIC, P0)
    seqs0 = [row["seq"] for row in snap0["chain"]]
    check("p0 推进链按提交编号有序", seqs0 == sorted(seqs0), seqs0)
    check("p0 可见推进数=3（41/42/43，来源B的42未签收不可见）",
          snap0["visible_advance_count"] == 3, snap0["visible_advance_count"])
    check("p0 待签收只剩来源B的42",
          snap0["pending_count"] == 1
          and snap0["chain"][-1]["status"] == STATUS_PENDING, snap0["chain"])
    snap1 = ledger.chain_snapshot(GROUP, TOPIC, P1)
    check("p1 可见推进数=1 且链上只有自己的提交",
          snap1["visible_advance_count"] == 1 and len(snap1["chain"]) == 1, snap1)

    print("\n归档回执明细（重复提交留痕）：")
    for row in ledger.list_receipts(GROUP, P0):
        print(
            f"  回执#{row['id']} seq={row['seq']} offset={row['offset']} "
            f"source={row['source']} status={row['status']} "
            f"visible={row['visible']} dup={row['duplicate_count']}"
        )

    if failures:
        print(f"\n{len(failures)} 项失败：{failures}")
        return 1
    print("\n全部场景断言通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
