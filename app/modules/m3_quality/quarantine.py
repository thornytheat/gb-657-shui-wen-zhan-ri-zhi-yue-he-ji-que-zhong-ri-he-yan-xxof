"""隔离区：原因码分布、原值证据、CSV 行（行号 + 原因码 + 原始单元格）。"""
from __future__ import annotations

from ... import reasons

CSV_HEADER = ["原始行号", "分段", "原因码", "原因", "等级", "字段", "原始值",
              "表号", "说明"]


def build_row(*, lineno: int, segment: int, code: str, stage: str, field: str,
              raw_value: str, meter_id: str, detail: str) -> dict:
    return {"lineno": lineno, "segment": segment, "code": code, "stage": stage,
            "level": reasons.level(code), "field": field, "raw_value": raw_value,
            "meter_id": meter_id, "detail": detail}


def distribution(quarantine: list[dict]) -> list[dict]:
    counts: dict[str, dict] = {}
    for item in quarantine:
        bucket = counts.setdefault(item["code"], {
            "code": item["code"], "label": reasons.label(item["code"]),
            "level": reasons.level(item["code"]), "count": 0,
        })
        bucket["count"] += 1
    return sorted(counts.values(), key=lambda item: (-item["count"], item["code"]))


def summary(quarantine: list[dict]) -> dict:
    total = len(quarantine)
    return {
        "total": total,
        "by_level": {
            level: sum(1 for item in quarantine if item["level"] == level)
            for level in ("error", "warn", "info")
        },
        "top": distribution(quarantine)[:5],
    }


def csv_rows(quarantine: list[dict]) -> list[list[str]]:
    rows = []
    for item in quarantine:
        rows.append([
            str(item["lineno"]), str(item["segment"]), item["code"],
            reasons.label(item["code"]), item["level"], item.get("field") or "",
            item.get("raw_value") or "", item.get("meter_id") or "",
            item.get("detail") or "",
        ])
    return rows


def to_dict(item: dict) -> dict:
    return {
        "lineno": item["lineno"],
        "segment": item["segment"],
        "code": item["code"],
        "label": reasons.label(item["code"]),
        "level": item["level"],
        "stage": item["stage"],
        "field": item.get("field"),
        "raw_value": item.get("raw_value"),
        "meter_id": item.get("meter_id"),
        "detail": item.get("detail"),
    }
