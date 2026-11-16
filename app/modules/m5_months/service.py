"""月度切分编排：从已落库区间重算，页面与接口共用同一份结果。"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m2_contract import contract as contract_mod
from . import allocate as allocate_mod


def intervals_from_store(rows: list[dict]) -> list[dict]:
    return [{
        "interval_id": row["interval_id"],
        "meter_id": row["meter_id"],
        "register": row["register"],
        "start_at": row["start_at"],
        "end_at": row["end_at"],
        "usage_kwh": Decimal(str(row["usage_kwh"])),
        "days": Decimal(str(row["days"])),
    } for row in rows]


def run(settings, store, batch_id: str) -> dict:
    contracts = contract_mod.load(settings)
    prices = contract_mod.load_prices(settings)
    intervals = intervals_from_store(store.load_intervals(batch_id))
    result = allocate_mod.allocate(intervals, contracts, prices)
    result["contracts"] = contracts
    result["prices"] = prices
    result["batch_id"] = batch_id
    return result


def primary_map(settings) -> dict[str, str]:
    return {meter.meter_id: meter.primary_register
            for meter in contract_mod.load(settings).values()}


def month_summary(allocations: list[dict],
                  primary: dict[str, str] | None = None) -> list[dict]:
    """按月汇总主寄存器用量（总表优先），避免与峰平谷重复相加。"""
    totals: dict[str, dict] = {}
    for item in allocations:
        if primary and primary.get(item["meter_id"]) != item["register"]:
            continue
        bucket = totals.setdefault(item["month"], {"month": item["month"],
                                                   "usage_kwh": Decimal("0")})
        bucket["usage_kwh"] += item["usage_kwh"]
    return sorted(totals.values(), key=lambda item: item["month"])


def rows_by_month(allocations: list[dict],
                  primary: dict[str, str] | None = None) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for item in allocations:
        grouped.setdefault(item["month"], []).append(item)
    out = []
    for month in sorted(grouped):
        rows = sorted(grouped[month], key=lambda item: (item["meter_id"], item["register"]))
        rows = sorted(rows, key=lambda item: (item["meter_id"],
                                              config.REGISTERS.index(item["register"])
                                              if item["register"] in config.REGISTERS else 9))
        total = sum((row["usage_kwh"] for row in rows
                     if not primary or primary.get(row["meter_id"]) == row["register"]),
                    Decimal("0"))
        out.append({
            "month": month,
            "rows": rows,
            "total": total,
            "adjusted": sum(1 for row in rows if row["tou_adjusted"]),
        })
    return out


def payload(allocations: list[dict], bills: list[dict],
            primary: dict[str, str] | None = None) -> dict:
    return {
        "allocations": [{
            "month": item["month"],
            "meter_id": item["meter_id"],
            "register": item["register"],
            "usage_kwh": str(item["usage_kwh"]),
            "exact_share": str(item["exact_share"]),
            "tou_adjusted": item["tou_adjusted"],
            "register_label": config.REGISTER_LABELS.get(item["register"], item["register"]),
        } for item in allocations],
        "bills": [{
            "month": bill["month"],
            "meter_id": bill["meter_id"],
            "usage_kwh": str(bill["usage_kwh"]),
            "amount_yuan": str(bill["amount_yuan"]),
            "adjust_cents": bill["adjust_cents"],
            "components": bill["components"],
        } for bill in bills],
        "months": [{"month": item["month"], "usage_kwh": str(item["usage_kwh"])}
                   for item in month_summary(allocations, primary)],
    }
