"""M2 表计契约：千分位/全角、哨兵三态、前导零、时区、单位归一。"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app import errors
from app.modules.m2_contract import coerce, contract, service, units


def test_thousands_and_fullwidth_normalized():
    """TRAP-06：千分位、全角数字都要归一再 Decimal 化。"""
    assert coerce.parse_decimal("1,212.5") == Decimal("1212.5")
    assert coerce.parse_decimal("３，０５０.0") == Decimal("3050.0")
    assert coerce.parse_decimal(" 2400.0 ") == Decimal("2400.0")


def test_sentinel_reading_is_three_state():
    """TRAP-07：哨兵与空串是 None，0 是有效读数。"""
    assert coerce.parse_decimal("-") is None
    assert coerce.parse_decimal("N/A") is None
    assert coerce.parse_decimal("未知") is None
    assert coerce.parse_decimal("") is None
    assert coerce.parse_decimal("0") == Decimal("0")


def test_meter_id_leading_zero_preserved(settings):
    """TRAP-08：表号 007 不能变成 7。"""
    contracts = contract.load(settings)
    assert "007" in contracts
    assert "7" not in contracts
    assert contracts["007"].meter_id == "007"


def test_timezone_offsets_collapse_to_same_instant():
    """TRAP-11：Z 与 +08:00 写同一时刻必须落到同一 UTC。"""
    zulu = coerce.parse_instant("2025-12-31T16:00:00Z")
    local = coerce.parse_instant("2026-01-01T00:00:00+08:00")
    assert zulu == local
    assert zulu.isoformat() == "2025-12-31T16:00:00+00:00"


def test_unit_mixing_normalized_to_contract_unit():
    """TRAP-15：kWh 与 MWh 混写先归一，否则合计差 1000 倍。"""
    assert units.convert(Decimal("12700"), "kWh", "MWh") == Decimal("12.7")
    assert units.convert(Decimal("12.5"), "MWh", "MWh") == Decimal("12.5")
    assert units.usage_to_kwh(Decimal("0.2"), "MWh") == Decimal("200.0")
    assert units.canonical_unit("度") == "kWh"


def test_unknown_unit_rejected_with_code(settings):
    """TRAP-15：认不出的单位不猜，直接隔离并给 Q_BAD_UNIT。"""
    contracts = contract.load(settings)
    rows = [{"lineno": 5, "segment": 1, "raw": "x", "cells": {
        "meter_id": "M0001", "register": "TOTAL",
        "read_at": "2026-02-15T00:00:00+08:00", "reading": "2000.0",
        "unit": "GJ", "read_type": "N", "note": ""}}]
    records, quarantine, _ = service.interpret_rows(rows, contracts)
    assert records == []
    assert quarantine[0]["code"] == "Q_BAD_UNIT"


def test_contract_rejects_duplicate_and_zero_multiplier():
    rows = [
        {"meter_id": "A1", "name": "甲", "capacity": "100", "multiplier": "1",
         "unit": "kWh", "registers": "TOTAL"},
        {"meter_id": "A1", "name": "乙", "capacity": "100", "multiplier": "1",
         "unit": "kWh", "registers": "TOTAL"},
    ]
    with pytest.raises(errors.ContractInvalid):
        contract.parse_rows(rows)
    with pytest.raises(errors.ContractInvalid):
        contract.parse_rows([{**rows[0], "meter_id": "B2", "multiplier": "0"}])


def test_capacity_and_multiplier_loaded(settings):
    contracts = contract.load(settings)
    assert contracts["M0002"].multiplier == Decimal("40")
    assert contracts["M0003"].capacity == Decimal("9999.9")
    assert contracts["M0003"].rollover_floor == Decimal("8999.91")
    assert contracts["M0006"].unit == "MWh"
    assert contracts["M0001"].primary_register == "TOTAL"


def test_preview_reports_estimate_and_missing(settings):
    contracts = contract.load(settings)
    missing = service.preview({"meter_id": "M0001", "register": "TOTAL",
                               "read_at": "2026-02-15T00:00:00+08:00",
                               "reading": "-", "unit": "kWh", "read_type": "N"}, contracts)
    assert missing["ok"] is False
    assert missing["code"] == "Q_MISSING_READING"
    estimate = service.preview({"meter_id": "M0003", "register": "TOTAL",
                                "read_at": "2026-02-15T00:00:00+08:00",
                                "reading": "5050.0", "unit": "kWh", "read_type": "估抄"},
                               contracts)
    assert estimate["ok"] is True
    assert estimate["is_estimate"] is True
    assert estimate["reading"] == "5050.0"
