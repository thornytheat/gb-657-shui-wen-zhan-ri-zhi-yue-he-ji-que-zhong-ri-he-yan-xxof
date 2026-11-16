"""运行清单导出：run_id、输入 sha256、参数、阶段计数、四账，可离线复算。"""
from __future__ import annotations

import json

from ... import reasons


def build(store, batch: dict, manifest_json: dict | None) -> dict:
    payload = {
        "batch_id": batch["batch_id"],
        "run_id": batch["run_id"],
        "batch_key": batch["batch_key"],
        "sample": batch["sample"],
        "status": batch["status"],
        "rule_version": batch["rule_version"],
        "input_digest": batch["input_digest"],
        "created_at": batch["created_at"],
        "finished_at": batch.get("finished_at"),
        "counts": store.count_rows(batch["batch_id"]),
        "totals": store.load_totals(batch["batch_id"]),
        "manifest": manifest_json or {},
        "reasons": reasons.catalog(),
    }
    return payload


def render_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def summary(payload: dict | None) -> list[dict]:
    if not payload:
        return []
    manifest_part = payload.get("manifest") or {}
    inputs = manifest_part.get("inputs") or []
    return [
        {"label": "run_id", "value": payload["run_id"]},
        {"label": "批次号", "value": payload["batch_id"]},
        {"label": "输入摘要", "value": payload["input_digest"]},
        {"label": "规则版本", "value": payload["rule_version"]},
        {"label": "样例", "value": payload["sample"]},
        {"label": "输入文件", "value": "、".join(item["name"] for item in inputs)},
        {"label": "阶段数", "value": str(len(manifest_part.get("stages") or []))},
        {"label": "四账", "value": "平衡" if manifest_part.get("all_balanced") else "不平"},
    ]
