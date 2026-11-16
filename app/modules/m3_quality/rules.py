"""质量规则：行级范围校验 + 重复键取值冲突 + 序列级单调校验。

顺序是有意义的：先判单行能否成立（负数、超满量、抄表晚于换表），
再判同键是否冲突，最后按 (表号, 寄存器, 时间) 走一遍单调性。
只有通过单行校验的行才参与冲突与单调判定，否则同一行会被打上三种码。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from ..m2_contract import contract as contract_mod
from ..m4_intervals import rollover


def check_row(record: dict, meter: contract_mod.MeterContract) -> dict | None:
    reading: Decimal = record["reading"]
    if reading < 0:
        return {"code": "Q_NEGATIVE_READING", "field": "reading",
                "detail": f"读数为负：{reading}"}
    if reading > meter.capacity:
        return {"code": "Q_EXCEEDS_CAPACITY", "field": "reading",
                "detail": f"读数 {reading} 超过满量 {meter.capacity}"}
    if meter.replaced_at is not None:
        read_at = record["read_at"]
        if isinstance(read_at, str):
            read_at = dt.datetime.fromisoformat(read_at)
        if read_at > meter.replaced_at:
            return {"code": "Q_METER_RETIRED", "field": "read_at",
                    "detail": "抄表时间晚于换表时点"}
    return None


def group_by_key(records: list[dict]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = {}
    for record in records:
        key = (record["meter_id"], record["register"], record["read_at"])
        groups.setdefault(key, []).append(record)
    return groups


def split_conflicts(records: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """同键多行：取值不同 → 全部隔离；取值相同 → 留最早一行，其余合并并留痕。"""
    keep: list[dict] = []
    conflicted: list[dict] = []
    merged: list[dict] = []
    for items in group_by_key(records).values():
        items.sort(key=lambda item: item["lineno"])
        if len(items) == 1:
            keep.append(items[0])
            continue
        values = {item["reading"] for item in items}
        if len(values) > 1:
            for item in items:
                conflicted.append({**item, "_conflict_values": sorted(str(v) for v in values)})
        else:
            keep.append(items[0])
            for item in items[1:]:
                merged.append({**item, "_merged_into": items[0]["lineno"]})
    keep.sort(key=lambda item: item["lineno"])
    return keep, conflicted, merged


def monotonic_quarantine(records: list[dict],
                         contracts: dict[str, contract_mod.MeterContract],
                         ) -> tuple[list[dict], list[dict]]:
    """按表号+寄存器排序后逐条比对：倒走隔离，满量翻转放行。

    估抄行既不作锚点也不参与单调判定：它的可信度低，用它会污染相邻区间。
    """
    kept: list[dict] = []
    dropped: list[dict] = []
    series: dict[tuple, list[dict]] = {}
    for record in records:
        series.setdefault((record["meter_id"], record["register"]), []).append(record)
    for key in sorted(series):
        meter = contracts[key[0]]
        items = sorted(series[key], key=lambda item: item["read_at"])
        last: dict | None = None
        for item in items:
            if item.get("read_type") == "E":
                kept.append(item)
                continue
            if last is None:
                kept.append(item)
                last = item
                continue
            verdict = rollover.classify_jump(last["reading"], item["reading"],
                                             meter.capacity)
            if verdict == "reverse":
                dropped.append({**item, "_prev": str(last["reading"]),
                                "_prev_lineno": last["lineno"]})
                continue
            kept.append(item)
            last = item
    kept.sort(key=lambda item: (item["meter_id"], item["register"], item["read_at"]))
    return kept, dropped
