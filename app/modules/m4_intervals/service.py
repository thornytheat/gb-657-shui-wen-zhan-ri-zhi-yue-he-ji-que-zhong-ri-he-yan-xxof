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
    """从批次快照契约重算区间：旧账的倍率、单位、换表关系不随现行契约改动。"""
    batch = store.get_batch(batch_id)
    contracts = contract_mod.from_meter_rows(store.load_meters(batch_id))
    if not contracts:
        contracts = contract_mod.load(settings)
    spec_version = (batch or {}).get("spec_version") or settings.rule_version
    valid = records_from_store(store.load_readings(batch_id, status="valid"), "N")
    estimates = records_from_store(store.load_readings(batch_id, status="estimate"), "E")
    intervals = restore.build_intervals(valid, estimates, contracts)
    summary = restore.totals(intervals, contracts)
    return {
        "batch_id": batch_id,
        "spec_version": spec_version,
        "contracts": {m.meter_id: m for m in contracts.values()},
        "intervals": intervals,
        "totals": summary,
        "swaps": swaps.replacement_pairs(contracts),
        "method_counts": _counts(intervals),
    }


def swaps_payload(settings, store=None, batch_id: str | None = None) -> list[dict]:
    contracts = None
    if store is not None and batch_id:
        contracts = contract_mod.from_meter_rows(store.load_meters(batch_id))
    if not contracts:
        contracts = contract_mod.load(settings)
    return [{"meter_id": item.meter_id, "name": item.name,
             "replaced_by": item.replaced_by,
             "replaced_at": item.replaced_at.isoformat() if item.replaced_at else None}
            for item in contracts.values() if item.replaced_by]


def usage_check(store, batch_id: str,
                intervals: list[dict] | None = None) -> dict:
    """区间账 vs 月度账 vs 重建结果：页面直接展示两侧用量与差额，便于人工核对。

    `intervals` 传入刚重建的结果时，额外逐段比对「重建的用量」与「落库的用量」，
    两者不一致说明现行契约或读数与批次快照有出入，必须在页面上看见。
    """
    from decimal import Decimal
    stored_intervals = store.load_intervals(batch_id)
    rebuilt = intervals if intervals is not None else stored_intervals
    totals = store.load_totals(batch_id)
    contracts = contract_mod.from_meter_rows(store.load_meters(batch_id))
    primary = {m.meter_id: m.primary_register for m in contracts.values()}

    def _primary_usage(items):
        return sum((item["usage_kwh"] for item in items
                    if not primary or primary.get(item["meter_id"]) == item["register"]),
                   Decimal("0")).quantize(Decimal("0.001"))

    interval_usage = Decimal(totals.get("interval_usage") or "0")
    month_usage = Decimal(totals.get("month_usage") or "0")
    recomputed = _primary_usage(rebuilt)
    stored_by_id = {item["interval_id"]: item for item in stored_intervals}
    rebuilt_by_id = {item["interval_id"]: item for item in rebuilt}
    mismatched = sorted(
        key for key in stored_by_id.keys() & rebuilt_by_id.keys()
        if stored_by_id[key]["usage_kwh"] != rebuilt_by_id[key]["usage_kwh"])
    missing = sorted(stored_by_id.keys() ^ rebuilt_by_id.keys())
    return {
        "interval_usage": interval_usage,
        "recomputed_usage": recomputed,
        "stored_usage": _primary_usage(stored_intervals),
        "month_usage": month_usage,
        "interval_minus_month": (interval_usage - month_usage).quantize(
            Decimal("0.001")),
        "rebuilt_changed": len(mismatched) + len(missing),
        "rebuilt_mismatched_ids": mismatched[:20],
        "rebuilt_missing_ids": missing[:20],
        "interval_count": len(stored_intervals),
        "rebuilt_count": len(rebuilt),
        "matches": interval_usage == month_usage == recomputed,
    }


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
