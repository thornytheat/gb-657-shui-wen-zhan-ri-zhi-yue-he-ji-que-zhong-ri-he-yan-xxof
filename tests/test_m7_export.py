"""M7 导出：BOM/CRLF、原值保形、Decimal 定长、清单字段、缺批次 404。"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app import errors
from app.modules.m7_export import service


def test_monthly_csv_starts_with_bom_and_crlf(client_with_run):
    body = client_with_run.get("/api/export/monthly.csv").text
    assert body.startswith("\ufeff")
    assert "\r\n" in body
    assert body.splitlines()[0].lstrip("\ufeff").startswith("月份,表号,寄存器")


def test_monthly_csv_row_values(client_with_run):
    """金额只落在分项行上：峰 171.39 / 平 119.36 / 谷 19.58，合计 310.33。"""
    lines = client_with_run.get("/api/export/monthly.csv").text.splitlines()
    peak = next(line for line in lines if line.startswith("2026-03,M0001,峰,163.226"))
    flat = next(line for line in lines if line.startswith("2026-03,M0001,平,183.629"))
    valley = next(line for line in lines if line.startswith("2026-03,M0001,谷,61.210"))
    assert peak.split(",")[4] == "171.39"
    assert flat.split(",")[4] == "119.36"
    assert valley.split(",")[4] == "19.58"
    total = next(line for line in lines if line.startswith("2026-03,M0001,总,408.065"))
    assert total.split(",")[4] == ""


def test_quarantine_csv_has_lineno_and_reason(run_result, settings, store, batch_id):
    body, _ = service.quarantine_csv(settings, store, batch_id)
    lines = body.splitlines()
    assert lines[0].lstrip("\ufeff").startswith("原始行号,分段,原因码")
    assert any("Q_BAD_TIME" in line for line in lines)
    assert any("Q_DUPLICATE_CONFLICT" in line for line in lines)


def test_manifest_json_has_run_id_and_inputs(run_result, settings, store, batch_id):
    body, _ = service.manifest_json(settings, store, batch_id)
    payload = json.loads(body)
    assert payload["run_id"].startswith("MB-")
    assert len(payload["input_digest"]) == 64
    assert payload["counts"]["intervals"] == 32
    assert payload["manifest"]["all_balanced"] is True
    assert any(item["code"] == "Q_REVERSE_READING" for item in payload["reasons"])


def test_readings_csv_preserves_raw_text(run_result, settings, store, batch_id):
    body, _ = service.readings_csv(settings, store, batch_id)
    assert "２１００.0" in body
    assert '"1,212.5"' in body


def test_export_decimal_is_fixed_length(run_result, settings, store, batch_id):
    body, _ = service.intervals_csv(settings, store, batch_id)
    assert "E+" not in body and "e-" not in body
    row = next(line for line in body.splitlines() if "M0003:TOTAL:01" in line)
    assert row.split(",")[11] == "249.900"


def test_export_missing_batch_returns_404(client):
    response = client.get("/api/export/monthly.csv")
    assert response.status_code == 404
    assert response.json()["error"] == "E_BATCH_NOT_FOUND"


def test_context_requires_existing_batch(store):
    with pytest.raises(errors.NotFound):
        service.monthly_csv(None, store, "mb-missing")


def test_yuan_and_kwh_formatting():
    from app.modules.m7_export import csv_out
    assert csv_out.kwh(Decimal("1234.5")) == "1234.500"
    assert csv_out.yuan(Decimal("1234.5")) == "1234.50"
    assert csv_out.render(["a"], [["1,2"]]) == '\ufeffa\r\n"1,2"\r\n'
