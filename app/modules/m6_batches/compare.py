"""四账核对：读数账 / 区间账 / 月度账 / 账单账。

四账各自左右两侧独立算出，再相减。差额不为 0 时不允许写「平衡」，
页面与导出都显示实际差额与说明。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config


def _account(key: str, label: str, left, right, unit: str, note: str = "") -> dict:
    diff = left - right
    return {"key": key, "label": label, "left": left, "right": right,
            "diff": diff, "unit": unit, "balanced": diff == 0, "note": note}


def build(*, raw_lines: int, valid: int, estimate: int, quarantine: int, blank: int,
          interval_usage: Decimal, month_usage: Decimal,
          tou_before: Decimal, tou_after: Decimal, tou_adjustments: int,
          bill_total: Decimal, component_total: Decimal, bill_count: int) -> dict:
    accounts = [
        _account("readings", "读数账", raw_lines,
                 valid + estimate + quarantine + blank, "行",
                 f"有效 {valid} + 估抄 {estimate} + 隔离 {quarantine} + 空行 {blank}"),
        _account("intervals", "区间账",
                 interval_usage.quantize(config.USAGE_QUANT),
                 month_usage.quantize(config.USAGE_QUANT), "kWh",
                 "主寄存器区间用量 = 月度分摊用量"),
        _account("months", "月度账",
                 tou_after.quantize(config.USAGE_QUANT), Decimal("0"), "kWh",
                 f"闭合前尾差 {tou_before.quantize(config.USAGE_QUANT)}，"
                 f"并入最大分量 {tou_adjustments} 组"),
        _account("bills", "账单账",
                 bill_total.quantize(config.MONEY_QUANT),
                 component_total.quantize(config.MONEY_QUANT), "元",
                 f"{bill_count} 张账单 = 分项金额之和"),
    ]
    diffs = [item for item in accounts if not item["balanced"]]
    return {"accounts": accounts, "all_balanced": not diffs,
            "diff_count": len(diffs),
            "checked_at": None}


def payload(reconcile: dict | None) -> dict:
    if not reconcile:
        return {"accounts": [], "all_balanced": None, "diff_count": 0}
    return {
        "all_balanced": reconcile["all_balanced"],
        "diff_count": reconcile["diff_count"],
        "accounts": [{
            "key": item["key"],
            "label": item["label"],
            "left": str(item["left"]),
            "right": str(item["right"]),
            "diff": str(item["diff"]),
            "unit": item["unit"],
            "balanced": item["balanced"],
            "note": item["note"],
        } for item in reconcile["accounts"]],
    }


def page_rows(reconcile: dict | None) -> list[dict]:
    if not reconcile:
        return []
    return [{
        "key": item["key"], "label": item["label"], "left": item["left"],
        "right": item["right"], "diff": item["diff"], "unit": item["unit"],
        "balanced": item["balanced"], "note": item["note"],
    } for item in reconcile["accounts"]]
