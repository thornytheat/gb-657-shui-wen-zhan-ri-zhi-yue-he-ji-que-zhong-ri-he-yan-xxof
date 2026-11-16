"""区间还原：按 (表号, 寄存器, UTC 时间) 排序后逐对还原用量。

三件事一起做，缺一条结果就错：
1. 满量翻转补容量，倒走不补（倒走已在质量层隔离）；
2. 换表拆两段：旧表到换表点为止，新表首读从零起算；
3. 估抄行不作锚点，但它落在区间内时给区间打标记。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config
from ..m2_contract import coerce, contract as contract_mod, units
from . import rollover, swaps

DAYS_QUANT = Decimal("0.000000001")


def _days(start: str, end: str) -> Decimal:
    import datetime as dt
    left = dt.datetime.fromisoformat(start)
    right = dt.datetime.fromisoformat(end)
    return (Decimal((right - left).total_seconds()) / Decimal(86400)).quantize(DAYS_QUANT)


def _markers(method: str) -> list[str]:
    if method == "rollover":
        return ["K_ROLLOVER"]
    if method == "replacement_first":
        return ["K_REPLACEMENT_FIRST"]
    return []


def build_intervals(valid: list[dict], estimates: list[dict],
                    contracts: dict[str, contract_mod.MeterContract]) -> list[dict]:
    estimate_index: dict[tuple, list[str]] = {}
    for item in estimates:
        estimate_index.setdefault((item["meter_id"], item["register"]), []).append(
            item["read_at"])
    predecessors = swaps.predecessor_map(contracts)

    series: dict[tuple, list[dict]] = {}
    for record in valid:
        series.setdefault((record["meter_id"], record["register"]), []).append(record)

    intervals: list[dict] = []
    for key in sorted(series):
        meter_id, register = key
        meter = contracts[meter_id]
        predecessor = predecessors.get(meter_id)
        items = sorted(series[key], key=lambda item: item["read_at"])
        seq = 0
        last: dict | None = None
        for item in items:
            if last is None:
                last = item
                if predecessor is not None and predecessor.replaced_at is not None:
                    start = predecessor.replaced_at.isoformat()
                    if item["read_at"] > start:
                        seq += 1
                        intervals.append(_make(
                            seq=seq, meter=meter, register=register, start=start,
                            end=item["read_at"], prev=Decimal("0"),
                            cur=item["reading"], method="replacement_first",
                            estimates=estimate_index))
                continue
            delta, method = rollover.delta_for(last["reading"], item["reading"],
                                               meter.capacity)
            seq += 1
            intervals.append(_make(
                seq=seq, meter=meter, register=register, start=last["read_at"],
                end=item["read_at"], prev=last["reading"], cur=item["reading"],
                method=method, estimates=estimate_index, delta=delta))
            last = item
    intervals.sort(key=lambda item: (item["meter_id"], item["register"], item["start_at"]))
    return intervals


def _make(*, seq: int, meter: contract_mod.MeterContract, register: str, start: str,
          end: str, prev: Decimal, cur: Decimal, method: str,
          estimates: dict[tuple, list[str]], delta: Decimal | None = None) -> dict:
    if delta is None:
        delta = cur - prev
    usage_contract = delta * meter.multiplier
    usage_kwh = units.usage_to_kwh(usage_contract, meter.unit).quantize(
        config.USAGE_QUANT)
    flags = _markers(method)
    for stamp in estimates.get((meter.meter_id, register), []):
        if start < stamp <= end:
            flags.append("K_ESTIMATE_SKIPPED")
            break
    return {
        "interval_id": f"{meter.meter_id}:{register}:{seq:02d}",
        "meter_id": meter.meter_id,
        "register": register,
        "start_at": start,
        "end_at": end,
        "prev_reading": prev,
        "cur_reading": cur,
        "delta": delta,
        "multiplier": meter.multiplier,
        "unit": meter.unit,
        "usage_kwh": usage_kwh,
        "days": _days(start, end),
        "method": method,
        "flags": flags,
    }


def totals(intervals: list[dict],
           contracts: dict[str, contract_mod.MeterContract]) -> dict:
    """主寄存器区间用量合计：总表优先，避免与峰平谷重复计算。"""
    primary = {m.meter_id: m.primary_register for m in contracts.values()}
    total = Decimal("0")
    for item in intervals:
        if primary.get(item["meter_id"]) == item["register"]:
            total += item["usage_kwh"]
    return {"primary_usage_kwh": total.quantize(config.USAGE_QUANT),
            "interval_count": len(intervals)}


def primary_intervals(intervals: list[dict],
                      contracts: dict[str, contract_mod.MeterContract]) -> list[dict]:
    primary = {m.meter_id: m.primary_register for m in contracts.values()}
    return [item for item in intervals
            if primary.get(item["meter_id"]) == item["register"]]


__all__ = ["build_intervals", "totals", "primary_intervals", "coerce"]
