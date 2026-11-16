"""月度分摊编排：区间 → 月桶 → 分量闭合 → 账单。

两步尾差都要处理，否则账永远差几分：
- 分量闭合：同月「总表」与「峰+平+谷」各自最大余额后仍可能差 0.001，
  差额并入当月用量最大的分量（可复算、只动一个数）；
- 账单闭合：分项金额各自截尾后与「先合计再舍入」的账单额可能差 1 分，
  差额按小数部分从大到小逐分补到分项上。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m2_contract import contract as contract_mod
from . import tou, weights


def allocate(intervals: list[dict],
             contracts: dict[str, contract_mod.MeterContract],
             prices: dict[str, Decimal]) -> dict:
    buckets: dict[tuple, dict] = {}
    for interval in intervals:
        import datetime as dt
        start = dt.datetime.fromisoformat(interval["start_at"])
        end = dt.datetime.fromisoformat(interval["end_at"])
        exact_parts, quant_parts = weights.allocate_interval(
            start, end, interval["usage_kwh"])
        exact_map = dict(exact_parts)
        for month, usage in quant_parts:
            key = (month, interval["meter_id"], interval["register"])
            bucket = buckets.setdefault(key, {
                "month": month, "meter_id": interval["meter_id"],
                "register": interval["register"],
                "usage_kwh": Decimal("0"), "exact_share": Decimal("0"),
                "tou_adjusted": False, "sources": [],
            })
            bucket["usage_kwh"] += usage
            bucket["exact_share"] += exact_map.get(month, Decimal("0"))
            bucket["sources"].append(interval["interval_id"])

    allocations = sorted(buckets.values(),
                         key=lambda item: (item["month"], item["meter_id"], item["register"]))
    closure = _close_components(allocations, contracts)
    bills = build_bills(allocations, contracts, prices)
    return {
        "allocations": allocations,
        "bills": bills,
        "stats": {
            "month_count": len({item["month"] for item in allocations}),
            "allocation_count": len(allocations),
            "bill_count": len(bills),
            "tou_adjustments": closure["adjusted"],
            "tou_residual_before": closure["before"],
            "tou_residual_after": closure["after"],
            "amount_total": sum((bill["amount_yuan"] for bill in bills), Decimal("0")),
            "usage_total": usage_total(allocations, contracts),
        },
    }


def usage_total(allocations: list[dict],
                contracts: dict[str, contract_mod.MeterContract]) -> Decimal:
    """主寄存器的月度用量合计（与区间账对齐）。"""
    primary = {m.meter_id: m.primary_register for m in contracts.values()}
    total = Decimal("0")
    for item in allocations:
        if primary.get(item["meter_id"]) == item["register"]:
            total += item["usage_kwh"]
    return total.quantize(config.USAGE_QUANT)


def _close_components(allocations: list[dict],
                      contracts: dict[str, contract_mod.MeterContract]) -> dict:
    by_month: dict[tuple, dict[str, dict]] = {}
    for item in allocations:
        by_month.setdefault((item["month"], item["meter_id"]), {})[item["register"]] = item
    stats = {"adjusted": 0, "before": Decimal("0"), "after": Decimal("0")}
    for (month, meter_id), registers in sorted(by_month.items()):
        meter = contracts[meter_id]
        if "TOTAL" not in meter.registers:
            continue
        if not all(name in registers for name in config.TOU_REGISTERS):
            continue
        total = registers["TOTAL"]["usage_kwh"]
        components = {name: registers[name]["usage_kwh"] for name in config.TOU_REGISTERS}
        stats["before"] += abs(total - sum(components.values(), Decimal("0")))
        closed, residual, winner = tou.close_components(total, components)
        stats["after"] += abs(total - sum(closed.values(), Decimal("0")))
        if residual != 0 and winner is not None:
            stats["adjusted"] += 1
            registers[winner]["usage_kwh"] = closed[winner]
            registers[winner]["tou_adjusted"] = True
            registers[winner]["sources"].append("K_TOU_ADJUSTED")
            for name in config.TOU_REGISTERS:
                if name != winner:
                    registers[name]["usage_kwh"] = closed[name]
    return stats


def build_bills(allocations: list[dict],
                contracts: dict[str, contract_mod.MeterContract],
                prices: dict[str, Decimal]) -> list[dict]:
    by_month: dict[tuple, dict[str, dict]] = {}
    for item in allocations:
        by_month.setdefault((item["month"], item["meter_id"]), {})[item["register"]] = item
    bills: list[dict] = []
    for (month, meter_id), registers in sorted(by_month.items()):
        meter = contracts[meter_id]
        if all(name in registers for name in config.TOU_REGISTERS):
            lines = [(name, registers[name]["usage_kwh"]) for name in config.TOU_REGISTERS]
        else:
            primary = meter.primary_register
            if primary not in registers:
                continue
            lines = [(primary, registers[primary]["usage_kwh"])]
        bill = tou.build_bill(month=month, meter_id=meter_id, lines=lines,
                              prices=prices, total_usage=_bill_usage(registers, meter))
        if bill is None:
            continue
        bills.append(bill)
    return bills


def _bill_usage(registers: dict[str, dict], meter: contract_mod.MeterContract) -> Decimal:
    if "TOTAL" in registers:
        return registers["TOTAL"]["usage_kwh"]
    return sum((item["usage_kwh"] for item in registers.values()), Decimal("0"))
