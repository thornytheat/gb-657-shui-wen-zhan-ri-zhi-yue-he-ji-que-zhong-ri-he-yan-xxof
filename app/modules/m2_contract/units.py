"""单位归一：读数先折到契约单位，用量再统一折成 kWh。

同一块表的历史导出来自不同版本软件，会出现 `MWh` 与 `kWh` 混写、`度/千瓦时`
别名、单位列留空。先归一再计算，否则合计差 1000 倍。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config


class UnknownUnit(ValueError):
    def __init__(self, raw: str):
        super().__init__(f"单位无法识别：{raw!r}")
        self.code = "Q_BAD_UNIT"
        self.raw = raw


def canonical_unit(raw: str) -> str | None:
    """返回契约里的标准写法（kWh / MWh），识别不了返回 None。"""
    key = (raw or "").strip().lower().replace("·", "").replace(" ", "")
    if not key:
        return None
    factor = config.UNIT_TO_KWH.get(key)
    if factor is None:
        return None
    return "MWh" if factor == 1000 else "kWh"


def factor_to_kwh(unit: str) -> Decimal:
    canonical = canonical_unit(unit)
    if canonical is None:
        raise UnknownUnit(unit)
    return config.UNIT_TO_KWH[canonical.lower()]


def convert(value: Decimal, from_unit: str, to_unit: str) -> Decimal:
    """把读数从读数单位折成目标单位（都是累计示数，线性换算）。"""
    src = factor_to_kwh(from_unit)
    dst = factor_to_kwh(to_unit)
    if src == dst:
        return value
    return value * src / dst


def usage_to_kwh(value: Decimal, unit: str) -> Decimal:
    return value * factor_to_kwh(unit)
