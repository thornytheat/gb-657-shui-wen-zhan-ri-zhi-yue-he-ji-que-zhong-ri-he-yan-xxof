"""重跑与续跑：同输入命中批次键直接复用结果，不追加一行。

幂等不是「结果看起来一样」，而是行数、合计、隔离条数逐项不变，
且返回的 batch_id 与首次一致。
"""
from __future__ import annotations

from ... import errors, reasons


def replay_payload(batch: dict, store) -> dict:
    stages = store.load_stages(batch["batch_id"])
    totals = store.load_totals(batch["batch_id"])
    return {
        "status": batch["status"],
        "batch": batch,
        "idempotent_replay": True,
        "replay_of": batch["batch_id"],
        "stages": stages,
        "totals": totals,
        "counts": store.count_rows(batch["batch_id"]),
    }


def find_replay(store, batch_key: str) -> dict | None:
    return store.find_done_by_key(batch_key)


def require_batch(store, batch_id: str) -> dict:
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_BATCH_NOT_FOUND")
    return batch


def retry_plan(store, batch_id: str) -> dict:
    batch = require_batch(store, batch_id)
    stages = store.load_stages(batch_id)
    failed = [item for item in stages if item["status"] == "failed"]
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "can_retry": True,
        "failed_stage": failed[0]["stage"] if failed else None,
        "sample": batch["sample"],
        "note": reasons.label("E_HEADER_INVALID") if batch["status"] == "failed" else "",
    }
