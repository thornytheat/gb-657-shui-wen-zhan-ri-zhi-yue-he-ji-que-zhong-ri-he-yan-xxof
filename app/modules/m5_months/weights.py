"""月度切分：按日权重把区间用量切到自然月，余数用最大余额法分到 0.001 kWh。

权重是「区间落在该月的天数」；月桶左闭右开 [月初 00:00, 下月初 00:00)，
相邻月份不重不漏。各月分到的用量之和必须与区间用量逐位相等。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, ROUND_FLOOR

from ... import config


def to_local(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.UTC)
    return value.astimezone(config.CHINA_TZ)


def month_of(value: dt.datetime) -> str:
    local = to_local(value)
    return f"{local.year:04d}-{local.month:02d}"


def month_start(month: str) -> dt.datetime:
    year, mon = int(month[:4]), int(month[5:7])
    return dt.datetime(year, mon, 1, tzinfo=config.CHINA_TZ)


def shift_month(month: str, delta: int) -> str:
    year, mon = int(month[:4]), int(month[5:7])
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_slices(start: dt.datetime, end: dt.datetime) -> list[tuple[str, Decimal]]:
    """返回 [(月份, 天数)]，天数用 Decimal，相邻桶首尾相接。"""
    left, right = to_local(start), to_local(end)
    if right <= left:
        return []
    slices: list[tuple[str, Decimal]] = []
    cursor = left
    while cursor < right:
        month = f"{cursor.year:04d}-{cursor.month:02d}"
        bucket_end = min(right, month_start(shift_month(month, 1)))
        seconds = Decimal((bucket_end - cursor).total_seconds())
        slices.append((month, (seconds / Decimal(86400)).quantize(Decimal("0.000000001"))))
        cursor = bucket_end
    return slices


def largest_remainder(total: Decimal, slices: list[tuple[str, Decimal]],
                      quant: Decimal = config.USAGE_QUANT) -> list[tuple[str, Decimal]]:
    """把 total 按权重切分，尾差按小数部分从大到小逐份补齐。"""
    positive = [(key, weight) for key, weight in slices if weight > 0]
    if not positive or total == 0:
        return [(key, Decimal("0").quantize(quant)) for key, _ in slices]
    weight_sum = sum((weight for _, weight in positive), Decimal("0"))
    units_total = int((total / quant).to_integral_value(rounding=ROUND_FLOOR))
    base: dict[str, int] = {}
    fractions: list[tuple[str, Decimal]] = []
    allocated = 0
    for key, weight in positive:
        exact_units = total * weight / weight_sum / quant
        floor_units = int(exact_units.to_integral_value(rounding=ROUND_FLOOR))
        base[key] = floor_units
        allocated += floor_units
        fractions.append((key, exact_units - floor_units))
    remainder = units_total - allocated
    order = sorted(fractions, key=lambda item: (-item[1], item[0]))
    for index in range(remainder):
        base[order[index % len(order)][0]] += 1
    result = []
    for key, _ in slices:
        units = base.get(key, 0)
        result.append((key, (Decimal(units) * quant).quantize(quant)))
    return result


def allocate_interval(start: dt.datetime, end: dt.datetime, usage: Decimal,
                      ) -> tuple[list[tuple[str, Decimal]], list[tuple[str, Decimal]]]:
    """返回 (精确份额, 量化份额)，两者只在尾差上有别。"""
    slices = month_slices(start, end)
    if not slices:
        return [], []
    weight_sum = sum((weight for _, weight in slices), Decimal("0"))
    exact = [(key, usage * weight / weight_sum) for key, weight in slices]
    return exact, largest_remainder(usage, slices)


def split_counts(slices: list[tuple[str, Decimal]]) -> int:
    return len(slices)
