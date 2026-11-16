"""区间还原编排：可从已落库的读数重算，供页面与接口单独演示本模块。"""
from __future__ import annotations

from decimal import Decimal

from ..m2_contract import contract as contract_mod
from . import restore, swaps


def records_from_store(rows: list[dict], read_type: str) -> list[dict]:
    out = []
    for row in rows:
        if row.get("reading_norm") is None:
            continue
        out.append({
            "lineno": row["lineno"],
            "segment": row["segment"],
            "meter_id": row["meter_id"],
            "register": row["register"],
            "read_at": row["read_at"],
            "reading": Decimal(str(row["reading_norm"])),
            "read_type": read_type,
            "flags": list(row.get("flags") or []),
        })
    return out


def rebuild(settings, store, batch_id: str) -> dict:
    contracts = contract_mod.load(settings)
    valid = records_from_store(store.load_readings(batch_id, status="valid"), "N")
    estimates = records_from_store(store.load_readings(batch_id, status="estimate"), "E")
    intervals = restore.build_intervals(valid, estimates, contracts)
    summary = restore.totals(intervals, contracts)
    return {
        "batch_id": batch_id,
        "contracts": {m.meter_id: m for m in contracts.values()},
        "intervals": intervals,
        "totals": summary,
        "swaps": swaps.replacement_pairs(contracts),
        "method_counts": _counts(intervals),
    }


def swaps_payload(settings) -> list[dict]:
    contracts = contract_mod.load(settings)
    return [{"meter_id": item.meter_id, "name": item.name,
             "replaced_by": item.replaced_by,
             "replaced_at": item.replaced_at.isoformat() if item.replaced_at else None}
            for item in contracts.values() if item.replaced_by]


def _counts(intervals: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for item in intervals:
        counts[item["method"]] = counts.get(item["method"], 0) + 1
    return counts


def flagged(intervals: list[dict]) -> list[dict]:
    return [item for item in intervals if item["flags"]]


def payload(intervals: list[dict]) -> list[dict]:
    return [{
        "interval_id": item["interval_id"],
        "meter_id": item["meter_id"],
        "register": item["register"],
        "start_at": item["start_at"],
        "end_at": item["end_at"],
        "prev_reading": str(item["prev_reading"]),
        "cur_reading": str(item["cur_reading"]),
        "delta": str(item["delta"]),
        "multiplier": str(item["multiplier"]),
        "unit": item["unit"],
        "usage_kwh": str(item["usage_kwh"]),
        "days": str(item["days"]),
        "method": item["method"],
        "flags": item["flags"],
    } for item in intervals]
