"""端到端：脏样例 → 采信 → 隔离 → 还原 → 切分 → 对账 → 导出，全部走 HTTP。"""
from __future__ import annotations

from fixtures import expected


def test_full_pipeline_over_http(client):
    response = client.post("/api/runs", json={"sample": "readings_dirty.csv"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "done"
    assert payload["reconcile"]["all_balanced"] is True
    assert int(payload["totals"]["valid"]) == expected.VALID
    assert int(payload["totals"]["quarantine"]) == expected.QUARANTINE

    for path in ("/overview", "/readings", "/intervals", "/months", "/batches", "/report"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "hydrocalendar" in page.text

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["latest_batch"] == payload["batch_id"]

    monthly = client.get("/api/export/monthly.csv")
    assert monthly.status_code == 200
    assert len(monthly.text) > 500
    assert "4499" not in monthly.text.splitlines()[0]


def test_second_run_reuses_batch_and_rows(client):
    first = client.post("/api/runs", json={"sample": "readings_dirty.csv"}).json()
    second = client.post("/api/runs", json={"sample": "readings_dirty.csv"}).json()
    assert second["batch_id"] == first["batch_id"]
    assert second["idempotent_replay"] is True
    assert second["counts"] == first["counts"]
    assert second["totals"]["interval_usage"] == first["totals"]["interval_usage"]
    assert second["totals"]["bill_amount"] == first["totals"]["bill_amount"]


def test_failure_inputs_return_4xx_with_code(client):
    unknown = client.post("/api/runs", json={"sample": "../secret.csv"})
    assert unknown.status_code == 400
    assert unknown.json()["error"] == "E_SAMPLE_UNKNOWN"

    empty = client.post("/api/readings/import", json={"sample": "readings_empty.csv"})
    assert empty.status_code == 422
    assert empty.json()["error"] == "E_EMPTY_FILE"
    assert empty.json()["detail"]["failed_stage"] == "ingest"

    header = client.post("/api/readings/import", json={"sample": "readings_bad_header.csv"})
    assert header.status_code == 422
    assert header.json()["error"] == "E_HEADER_INVALID"


def test_pages_render_without_any_batch(client):
    for path in ("/overview", "/readings", "/intervals", "/months", "/batches", "/report"):
        page = client.get(path)
        assert page.status_code == 200
        assert "empty-line" in page.text
    assert client.get("/api/quarantine").json()["rows"] == []
    assert client.get("/api/reconcile").json()["accounts"] == []
