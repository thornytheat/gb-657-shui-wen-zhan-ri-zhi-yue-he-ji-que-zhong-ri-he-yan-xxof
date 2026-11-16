"""M4 区间还原：满量翻转、换表两段、倍率、单位、估抄标记、半开区间。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.m1_ingest import service as m1_service
from app.modules.m2_contract import contract as contract_mod
from app.modules.m2_contract import service as m2_service
from app.modules.m3_quality import service as m3_service
from app.modules.m4_intervals import restore, rollover
from fixtures import expected


def _intervals(settings):
    ingested = m1_service.ingest(settings, "readings_dirty.csv")
    contracts = contract_mod.load(settings)
    records, _, _ = m2_service.interpret_rows(ingested.rows, contracts)
    quality = m3_service.evaluate(records, contracts)
    return restore.build_intervals(quality["valid"], quality["estimate"], contracts), contracts


def _find(intervals, meter_id, register):
    items = [item for item in intervals
             if item["meter_id"] == meter_id and item["register"] == register]
    return sorted(items, key=lambda item: item["start_at"])


def test_rollover_adds_capacity(settings):
    intervals, _ = _intervals(settings)
    first = _find(intervals, "M0003", "TOTAL")[0]
    assert first["method"] == "rollover"
    assert first["delta"] == expected.M0003_ROLLOVER
    assert first["usage_kwh"] == expected.M0003_ROLLOVER
    assert "K_ROLLOVER" in first["flags"]


def test_naive_subtraction_would_drop_rollover(settings):
    """反例：直接相减得到负数，朴素实现会把整段丢掉或倒扣。"""
    _, contracts = _intervals(settings)
    capacity = contracts["M0003"].capacity
    assert rollover.classify_jump(Decimal("9900.0"), Decimal("150.0"), capacity) == "rollover"
    assert Decimal("150.0") - Decimal("9900.0") < 0
    delta, method = rollover.delta_for(Decimal("9900.0"), Decimal("150.0"), capacity)
    assert (delta, method) == (Decimal("249.9"), "rollover")
    with pytest.raises(rollover.ReverseMovement):
        rollover.delta_for(Decimal("1212.5"), Decimal("1000.0"), Decimal("99999.9"))


def test_replacement_splits_two_segments(settings):
    intervals, _ = _intervals(settings)
    old = _find(intervals, "M0004", "TOTAL")
    new = _find(intervals, "M0005", "TOTAL")
    assert len(old) == 2 and len(new) == 2
    assert old[-1]["usage_kwh"] == expected.M0004_LAST
    assert old[-1]["end_at"] == "2026-02-09T16:00:00+00:00"
    assert new[0]["start_at"] == old[-1]["end_at"]
    assert new[0]["method"] == "replacement_first"
    assert new[0]["usage_kwh"] == expected.M0005_FIRST


def test_new_meter_first_reading_counts_from_zero(settings):
    """换表后新表首读本身就是一段用量，不能当基准丢掉。"""
    intervals, _ = _intervals(settings)
    first = _find(intervals, "M0005", "TOTAL")[0]
    assert first["prev_reading"] == Decimal("0")
    assert first["days"] == Decimal("10.000000000")
    assert first["usage_kwh"] == expected.M0005_FIRST
    assert "K_REPLACEMENT_FIRST" in first["flags"]


def test_multiplier_applied(settings):
    intervals, _ = _intervals(settings)
    item = _find(intervals, "M0002", "TOTAL")[0]
    assert item["delta"] == Decimal("12.5")
    assert item["multiplier"] == Decimal("40")
    assert item["usage_kwh"] == expected.M0002_USAGE


def test_mixed_unit_interval_usage_kwh(settings):
    intervals, _ = _intervals(settings)
    item = _find(intervals, "M0006", "TOTAL")[0]
    assert item["delta"] == Decimal("0.2")
    assert item["unit"] == "MWh"
    assert item["usage_kwh"] == expected.M0006_USAGE


def test_estimate_flag_on_spanning_interval(settings):
    intervals, _ = _intervals(settings)
    item = _find(intervals, "M0003", "TOTAL")[0]
    assert "K_ESTIMATE_SKIPPED" in item["flags"]
    assert item["delta"] == expected.M0003_ROLLOVER


def test_intervals_are_half_open_and_contiguous(settings):
    """TRAP-23：相邻区间首尾相接，上一段止 = 下一段起，不重不漏。"""
    intervals, _ = _intervals(settings)
    series: dict[tuple, list[dict]] = {}
    for item in intervals:
        series.setdefault((item["meter_id"], item["register"]), []).append(item)
    for items in series.values():
        items.sort(key=lambda item: item["start_at"])
        for left, right in zip(items, items[1:]):
            assert left["end_at"] == right["start_at"]


def test_unsorted_input_sorted_by_time(settings):
    ingested = m1_service.ingest(settings, "readings_dirty.csv")
    contracts = contract_mod.load(settings)
    records, _, _ = m2_service.interpret_rows(ingested.rows, contracts)
    quality = m3_service.evaluate(records, contracts)
    forward = restore.build_intervals(quality["valid"], quality["estimate"], contracts)
    reversed_rows = list(reversed(quality["valid"]))
    backward = restore.build_intervals(reversed_rows, quality["estimate"], contracts)
    assert [(item["interval_id"], str(item["usage_kwh"])) for item in forward] == \
           [(item["interval_id"], str(item["usage_kwh"])) for item in backward]
