"""运行清单：run_id、输入 sha256、规则版本、参数、阶段计数，可复算。

输入摘要覆盖「读数文件 + 表计契约 + 电价表 + 规则版本」，同四样东西必然同摘要。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import uuid

from . import config


def new_run_id(prefix: str = "MB") -> str:
    stamp = dt.datetime.now(config.UTC).strftime("%Y%m%d%H%M%S")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:4]}"


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_parts(parts: list[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def input_digest(sample_name: str, sample_bytes: bytes, meters_bytes: bytes,
                 prices_bytes: bytes, rule_version: str) -> str:
    return digest_parts([
        rule_version,
        sample_name,
        digest_bytes(sample_bytes),
        digest_bytes(meters_bytes),
        digest_bytes(prices_bytes),
    ])


def batch_key(sample_name: str, digest: str) -> str:
    """同一份输入 + 同一规则版本 → 同一个批次键，重跑命中不新增行。"""
    return digest_parts([sample_name, digest])[:16]


def build(*, run_id: str, batch_id: str, batch_key_value: str, settings, sample: str,
          digest: str, inputs: list[dict], params: dict, started_at: str,
          finished_at: str, elapsed_ms: int, stages: list[dict], counters: dict,
          reconcile: dict) -> dict:
    return {
        "run_id": run_id,
        "batch_id": batch_id,
        "batch_key": batch_key_value,
        "sample": sample,
        "rule_version": settings.rule_version,
        "input_digest": digest,
        "inputs": inputs,
        "params": params,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_ms": elapsed_ms,
        "decimals": {"usage": "kWh / 0.001", "money": "元 / 0.01"},
        "timezone": "入库 UTC，月桶与展示 Asia/Shanghai(+08:00)",
        "stages": stages,
        "counters": counters,
        "accounts": [
            {"key": a["key"], "label": a["label"], "left": str(a["left"]),
             "right": str(a["right"]), "diff": str(a["diff"]),
             "balanced": a["balanced"], "note": a.get("note", "")}
            for a in reconcile.get("accounts", [])
        ],
        "all_balanced": reconcile.get("all_balanced"),
        "replayable": "同输入重跑返回同一批次号，行数与合计不变",
    }
