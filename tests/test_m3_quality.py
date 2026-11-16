"""M3 质量隔离：负数、倒走、同键冲突、未知表、超满量、换表后抄表。"""
from __future__ import annotations

from decimal import Decimal

from app import reasons
from app.modules.m2_contract import contract as contract_mod
from app.modules.m2_contract import service as m2_service
from app.modules.m3_quality import service as m3_service
from app.modules.m1_ingest import service as m1_service
from fixtures import expected


def _evaluate(settings):
    ingested = m1_service.ingest(settings, "readings_dirty.csv")
    contracts = contract_mod.load(settings)
    records, contract_q, _ = m2_service.interpret_rows(ingested.rows, contracts)
    quality = m3_service.evaluate(records, contracts)
    return quality, contract_q, ingested


def test_negative_reading_quarantined(settings):
    quality, _, _ = _evaluate(settings)
    codes = [item["code"] for item in quality["quarantine"]]
    assert "Q_NEGATIVE_READING" in codes
    row = next(item for item in quality["quarantine"]
               if item["code"] == "Q_NEGATIVE_READING")
    assert row["raw_value"] == "-5.0"
    assert row["level"] == "error"


def test_reverse_reading_quarantined_not_rollover(settings):
    """倒走与满量翻转只差一个前提：上一条读数没到满量线就不许补容量。"""
    quality, _, _ = _evaluate(settings)
    row = next(item for item in quality["quarantine"]
               if item["code"] == "Q_REVERSE_READING")
    assert row["meter_id"] == "M0002"
    assert "1212.5" in row["detail"]


def test_duplicate_key_conflict_isolates_both_rows(settings):
    """TRAP-14：同一 (表号, 寄存器, 时间) 两条不同取值，两条都隔离并留原值。"""
    quality, _, _ = _evaluate(settings)
    rows = [item for item in quality["quarantine"]
            if item["code"] == "Q_DUPLICATE_CONFLICT"]
    assert len(rows) == 2
    assert {item["raw_value"] for item in rows} == {"200.0", "210.0"}
    assert all(item["meter_id"] == "007" for item in rows)


def test_unknown_meter_and_leading_zero_mismatch(settings):
    """TRAP-08：契约里是 007，写成 7 就是未知表计。"""
    quality, contract_q, _ = _evaluate(settings)
    unknown = {item["raw_value"] for item in contract_q
               if item["code"] == "Q_UNKNOWN_METER"}
    assert "M0099" in unknown
    assert "7" in unknown


def test_estimate_row_kept_but_not_anchor(settings):
    quality, _, _ = _evaluate(settings)
    assert len(quality["estimate"]) == expected.ESTIMATE
    estimate = quality["estimate"][0]
    assert estimate["meter_id"] == "M0003"
    assert estimate["reading"] == Decimal("5050.0")
    assert estimate["read_type"] == "E"


def test_exceeds_capacity_quarantined(settings):
    quality, _, _ = _evaluate(settings)
    row = next(item for item in quality["quarantine"]
               if item["code"] == "Q_EXCEEDS_CAPACITY")
    assert row["raw_value"] == "10500.0"
    assert "9999.9" in row["detail"]


def test_retired_meter_reading_quarantined(settings):
    quality, _, _ = _evaluate(settings)
    row = next(item for item in quality["quarantine"]
               if item["code"] == "Q_METER_RETIRED")
    assert row["meter_id"] == "M0004"
    assert row["level"] == "warn"


def test_quarantine_distribution_counts(settings):
    quality, contract_q, ingested = _evaluate(settings)
    everything = ingested.quarantine + contract_q + quality["quarantine"]
    assert len(everything) == expected.QUARANTINE
    codes = {item["code"] for item in everything}
    assert codes == expected.QUARANTINE_CODES
    distribution = __import__("app.modules.m3_quality.quarantine",
                              fromlist=["distribution"]).distribution(everything)
    assert sum(item["count"] for item in distribution) == expected.QUARANTINE
    assert distribution[0]["label"] == reasons.label(distribution[0]["code"])
