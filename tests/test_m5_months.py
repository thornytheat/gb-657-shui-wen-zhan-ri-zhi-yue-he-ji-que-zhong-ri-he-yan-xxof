"""M5 月度切分：日权重、最大余额、分量闭合、账单尾差、Decimal 精度。"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.modules.m2_contract import contract as contract_mod
from app.modules.m5_months import allocate as allocate_mod
from app.modules.m5_months import tou, weights
from fixtures import expected

PRIMARY = ("M0001", "M0002", "M0003", "M0004", "M0005", "M0006", "007", "M0008")


def _primary_allocations(store, batch_id):
    return [item for item in store.load_allocations(batch_id)
            if item["register"] == "TOTAL" and item["meter_id"] in PRIMARY]


def test_month_slices_are_half_open():
    """TRAP-23：月桶 [月初, 下月初)，跨月区间按日权重切成 16 + 15 天。"""
    slices = weights.month_slices(
        dt.datetime.fromisoformat("2026-03-16T00:00:00+08:00"),
        dt.datetime.fromisoformat("2026-04-16T00:00:00+08:00"))
    assert [(key, value) for key, value in slices] == [
        ("2026-03", Decimal("16.000000000")), ("2026-04", Decimal("15.000000000"))]
    assert sum(value for _, value in slices) == Decimal("31.000000000")


def test_largest_remainder_keeps_total_exact():
    """TRAP-24：切分后的两段之和逐位等于区间用量。"""
    parts = weights.largest_remainder(
        Decimal("500"), [("2026-03", Decimal(16)), ("2026-04", Decimal(15))])
    assert parts == [("2026-03", Decimal("258.065")), ("2026-04", Decimal("241.935"))]
    assert sum(value for _, value in parts) == Decimal("500.000")


def test_cross_year_three_month_slice():
    slices = weights.month_slices(
        dt.datetime.fromisoformat("2025-12-01T00:00:00+08:00"),
        dt.datetime.fromisoformat("2026-03-01T00:00:00+08:00"))
    assert [key for key, _ in slices] == ["2025-12", "2026-01", "2026-02"]
    assert [str(value) for _, value in slices] == [
        "31.000000000", "31.000000000", "28.000000000"]


def test_month_totals_sum_to_interval_usage(store, batch_id):
    totals = {}
    for item in _primary_allocations(store, batch_id):
        totals[item["month"]] = totals.get(item["month"], Decimal("0")) + item["usage_kwh"]
    assert totals == expected.MONTH_TOTALS
    assert sum(totals.values(), Decimal("0")) == expected.TOTAL_USAGE


def test_tou_components_closed_to_total(store, batch_id):
    """TRAP-24：分量各自最大余额后与总表差 0.001，残差并入当月最大分量。"""
    rows = {(item["month"], item["register"]): item
            for item in store.load_allocations(batch_id) if item["meter_id"] == "M0008"}
    for month, target in (("2026-01", expected.M0008_JAN),
                          ("2026-02", expected.M0008_FEB),
                          ("2025-12", expected.M0008_DEC)):
        values = {name: rows[(month, name)]["usage_kwh"] for name in target}
        assert values == target
        components = values["PEAK"] + values["FLAT"] + values["VALLEY"]
        assert components == values["TOTAL"]
    assert rows[("2026-01", "FLAT")]["tou_adjusted"] is True
    assert rows[("2026-02", "FLAT")]["tou_adjusted"] is True
    assert rows[("2025-12", "FLAT")]["tou_adjusted"] is False


def test_bill_rounding_closed_not_naive(store, batch_id):
    """账单额先合计再舍入；分项截尾后补 2 分，朴素逐项四舍五入会多 1 分。"""
    bill = next(item for item in store.load_bills(batch_id)
                if item["month"] == "2026-03" and item["meter_id"] == "M0001")
    amount, components, cents = expected.M0001_MAR_BILL
    assert bill["amount_yuan"] == amount
    assert {item["register"]: Decimal(item["amount"]) for item in bill["components"]} == components
    assert bill["adjust_cents"] == cents
    assert sum(Decimal(item["amount"]) for item in bill["components"]) == amount
    exact = (Decimal("163.226") * Decimal("1.05")
             + Decimal("183.629") * Decimal("0.65")
             + Decimal("61.210") * Decimal("0.32"))
    naive = sum((value.quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")
                 for value in (Decimal("163.226") * Decimal("1.05"),
                               Decimal("183.629") * Decimal("0.65"),
                               Decimal("61.210") * Decimal("0.32"))), Decimal("0"))
    assert exact.quantize(Decimal("0.01"), rounding="ROUND_HALF_UP") == amount
    assert naive == expected.M0001_MAR_NAIVE


def test_decimal_totals_exact(store, batch_id):
    """TRAP-10：0.001 刻度累加 16 行，Decimal 精确，二进制浮点有尾差。"""
    values = [item["usage_kwh"] for item in _primary_allocations(store, batch_id)]
    exact = sum(values, Decimal("0"))
    assert exact == expected.TOTAL_USAGE
    assert Decimal(float(exact)) != exact
    assert Decimal(sum(float(value) for value in values)) != exact


def test_bill_component_sum_equals_amount(store, batch_id):
    for bill in store.load_bills(batch_id):
        total = sum((Decimal(item["amount"]) for item in bill["components"]), Decimal("0"))
        assert total == bill["amount_yuan"]


def test_zero_usage_returns_zero_parts():
    parts = weights.largest_remainder(Decimal("0"), [("2026-01", Decimal(1)),
                                                    ("2026-02", Decimal(2))])
    assert [value for _, value in parts] == [Decimal("0.000"), Decimal("0.000")]


def test_component_residual_goes_to_largest():
    closed, residual, winner = tou.close_components(
        Decimal("100.000"),
        {"PEAK": Decimal("30.000"), "FLAT": Decimal("40.000"), "VALLEY": Decimal("30.000")})
    assert residual == Decimal("0.000")
    assert winner is None
    closed, residual, winner = tou.close_components(
        Decimal("100.001"),
        {"PEAK": Decimal("30.000"), "FLAT": Decimal("70.000"), "VALLEY": Decimal("0.000")})
    assert residual == Decimal("0.001")
    assert winner == "FLAT"
    assert closed["FLAT"] == Decimal("70.001")
    assert sum(closed.values()) == Decimal("100.001")


def test_component_residual_tie_goes_to_peak():
    closed, residual, winner = tou.close_components(
        Decimal("100.001"),
        {"PEAK": Decimal("50.000"), "FLAT": Decimal("50.000"), "VALLEY": Decimal("0.000")})
    assert residual == Decimal("0.001")
    assert winner == "PEAK"
    assert sum(closed.values()) == Decimal("100.001")


def test_allocate_is_deterministic(settings, store, batch_id):
    contracts = contract_mod.load(settings)
    prices = contract_mod.load_prices(settings)
    first = allocate_mod.allocate(store.load_intervals(batch_id), contracts, prices)
    second = allocate_mod.allocate(store.load_intervals(batch_id), contracts, prices)
    assert [(a["month"], a["meter_id"], a["register"], str(a["usage_kwh"]))
            for a in first["allocations"]] == \
           [(a["month"], a["meter_id"], a["register"], str(a["usage_kwh"]))
            for a in second["allocations"]]
