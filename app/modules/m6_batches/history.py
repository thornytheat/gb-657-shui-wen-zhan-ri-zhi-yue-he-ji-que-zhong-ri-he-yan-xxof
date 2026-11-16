"""批次记录与批次键：同一份输入 + 同一规则版本 → 同一批次号。

批次号由批次键派生，不随机，重跑必然命中同一个批次；run_id 每次运行新生成，
用于在清单里区分「这一次执行」与「这一批数据」。
"""
from __future__ import annotations

import datetime as dt
import json

from ... import manifest


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat()


def batch_id_for(batch_key: str) -> str:
    return "mb-" + batch_key[:12]


def build_batch(*, batch_key: str, run_id: str, sample: str, status: str,
                digest: str, rule_version: str, params: dict,
                created_at: str, finished_at: str | None = None,
                updated_at: str | None = None, note: str = "") -> dict:
    return {
        "batch_id": batch_id_for(batch_key),
        "batch_key": batch_key,
        "run_id": run_id,
        "sample": sample,
        "status": status,
        "created_at": created_at,
        "finished_at": finished_at,
        "updated_at": updated_at or created_at,
        "input_digest": digest,
        "rule_version": rule_version,
        "params": params,
        "note": note,
    }


def params_json(batch: dict) -> dict:
    return json.loads(batch.get("params_json") or "{}")


def counters(*, valid: int, estimate: int, quarantine: int, blank: int,
             intervals: int, allocations: int, bills: int) -> dict:
    return {"valid": valid, "estimate": estimate, "quarantine": quarantine,
            "blank": blank, "intervals": intervals, "allocations": allocations,
            "bills": bills}


def make_run_id() -> str:
    return manifest.new_run_id("MB")
