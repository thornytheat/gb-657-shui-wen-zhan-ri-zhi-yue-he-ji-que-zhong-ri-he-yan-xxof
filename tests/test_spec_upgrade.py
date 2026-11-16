"""规范升级：新批次缺单位隔离、旧批次绑定原契约、升级入口可续跑与拒绝。

测试全部走真实的 SQLite 与样例文件，不 mock 存储：
- 新旧规范对同一份空单位样例给出不同结论；
- 旧批次用倍率/单位/换表关系快照重建，改 meters.csv 不影响旧账；
- 升级中断后旧账原样保留，续跑幂等且不新增批次；
- 证据不足的旧账明确拒绝，批次仍绑旧规范；
- 并发切换规范时在途批次保持原规范，重试不新增批次。
"""
from __future__ import annotations

import dataclasses
import pathlib
from decimal import Decimal

import pytest

from app import config, errors, specs
from app.modules.m4_intervals import service as intervals_service
from app.modules.m5_months import service as months_service
from app.modules.m6_batches import history, service, upgrade
from fixtures import expected


def _legacy(settings, store, sample="readings_blank_unit.csv", **kwargs):
    return service.run(settings, store, sample=sample, spec_version=specs.LEGACY,
                       **kwargs)


def _with_sample_dir(settings, directory) -> config.Settings:
    return dataclasses.replace(settings, sample_dir=str(directory))


@pytest.fixture()
def sample_copy(tmp_path) -> pathlib.Path:
    """复制样例目录，测试可改 meters.csv 而不污染仓库。"""
    target = tmp_path / "samples"
    target.mkdir()
    for item in pathlib.Path(config.PROJECT_ROOT / "sample_data").iterdir():
        if item.is_file():
            (target / item.name).write_bytes(item.read_bytes())
    return target


# ---------- 新规范 ----------
def test_new_batch_missing_unit_is_quarantined(settings, store):
    result = service.run(settings, store, sample="readings_blank_unit.csv")
    assert result["batch"]["spec_version"] == specs.CURRENT
    assert int(result["totals"]["quarantine"]) == 2
    codes = [item["code"] for item in store.load_quarantine(
        result["batch"]["batch_id"])]
    assert codes.count("Q_MISSING_UNIT") == 2


def test_new_batch_keeps_declared_units(settings, store):
    result = service.run(settings, store, sample="readings_dirty.csv")
    assert result["batch"]["spec_version"] == specs.CURRENT
    assert {item["code"] for item in store.load_quarantine(
        result["batch"]["batch_id"])} >= {"Q_BAD_UNIT"}
    # 明示单位的行不受影响：脏样例仍是原来的 45 有效行。
    assert int(result["totals"]["valid"]) == expected.VALID


def test_contract_preview_reports_missing_unit_under_current_spec(settings):
    from app.modules.m2_contract import contract, service as contract_service
    contracts = contract.load(settings)
    cells = {"meter_id": "M0001", "register": "TOTAL",
             "read_at": "2026-02-15T00:00:00+08:00", "reading": "2000.0",
             "unit": "", "read_type": "N"}
    current = contract_service.preview(cells, contracts, specs.CURRENT)
    assert current["ok"] is False and current["code"] == "Q_MISSING_UNIT"
    legacy = contract_service.preview(cells, contracts, specs.LEGACY)
    assert legacy["ok"] is True and legacy["reading"] == "2000.0"


# ---------- 旧批次 ----------
def test_legacy_batch_fills_unit_from_contract(settings, store):
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    assert result["batch"]["spec_version"] == specs.LEGACY
    assert int(result["totals"]["quarantine"]) == 0
    rows = store.load_readings(bid)
    flagged = [row for row in rows if "K_UNIT_ASSUMED" in row["flags"]]
    assert len(flagged) == 2
    assert all("K_UNIT_MISSING" in row["flags"] for row in flagged)


def test_legacy_and_current_agree_on_usage_when_units_present(settings, store):
    """空单位只在采信层有差别；单位齐全时两版规范的用量必须逐位相等。"""
    current = service.run(settings, store, sample="readings_clean.csv")
    legacy = service.run(settings, store, sample="readings_clean.csv",
                         spec_version=specs.LEGACY)
    assert current["batch"]["batch_id"] != legacy["batch"]["batch_id"]
    assert current["totals"]["interval_usage"] == legacy["totals"]["interval_usage"]
    assert current["totals"]["bill_amount"] == legacy["totals"]["bill_amount"]


def test_legacy_batch_binds_contract_snapshot(settings, store, sample_copy):
    """改 meters.csv 的倍率后，旧批次的区间重建仍用当时快照的倍率。"""
    run_settings = _with_sample_dir(settings, sample_copy)
    result = _legacy(run_settings, store, sample="readings_clean.csv")
    bid = result["batch"]["batch_id"]
    before = intervals_service.rebuild(run_settings, store, bid)["intervals"]

    meters = sample_copy / "meters.csv"
    text = meters.read_text(encoding="utf-8")
    meters.write_text(text.replace("M0002,砖厂专变,99999.9,40,",
                                   "M0002,砖厂专变,99999.9,80,"), encoding="utf-8")

    after = intervals_service.rebuild(run_settings, store, bid)["intervals"]
    assert [(item["interval_id"], str(item["usage_kwh"])) for item in before] == \
           [(item["interval_id"], str(item["usage_kwh"])) for item in after]
    m0002 = next(item for item in after if item["meter_id"] == "M0002")
    assert m0002["multiplier"] == Decimal("40")
    assert m0002["usage_kwh"] == expected.M0002_USAGE

    # 改的是现行契约；旧账仍按快照的 40 倍率，两者不会互相污染。
    fresh = service.run(run_settings, store, sample="readings_clean.csv",
                        spec_version=specs.LEGACY, mode="force")
    assert fresh["batch"]["batch_id"] != bid
    fresh_m0002 = next(item for item in
                       intervals_service.rebuild(run_settings, store,
                                                 fresh["batch"]["batch_id"])["intervals"]
                       if item["meter_id"] == "M0002")
    assert fresh_m0002["multiplier"] == Decimal("80")
    assert fresh_m0002["usage_kwh"] == expected.M0002_USAGE * 2


def test_legacy_swap_relation_bound_to_snapshot(settings, store, sample_copy):
    run_settings = _with_sample_dir(settings, sample_copy)
    result = service.run(run_settings, store, sample="readings_clean.csv")
    bid = result["batch"]["batch_id"]
    before = intervals_service.swaps_payload(run_settings, store, bid)
    meters = sample_copy / "meters.csv"
    meters.write_text(meters.read_text(encoding="utf-8").replace(
        "M0004,老仓库,99999.9,1,kWh,TOTAL,2026-02-10T00:00:00+08:00,M0005",
        "M0004,老仓库,99999.9,1,kWh,TOTAL,,"), encoding="utf-8")
    after = intervals_service.swaps_payload(run_settings, store, bid)
    assert before == after and len(after) == 1
    assert after[0]["meter_id"] == "M0004" and after[0]["replaced_by"] == "M0005"


# ---------- 升级入口 ----------
def test_upgrade_plan_reports_target_and_gaps(settings, store):
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    plan = upgrade.plan(settings, store, bid)
    assert plan["can_upgrade"] is True
    assert plan["from_version"] == specs.LEGACY
    assert plan["to_version"] == specs.CURRENT
    assert plan["blank_unit_rows"] == 2 and plan["gaps"] == []


def test_upgrade_success_keeps_batch_and_usage(settings, store):
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    usage_before = store.load_totals(bid)["interval_usage"]
    batch_count = len(store.list_batches(limit=100))

    outcome = upgrade.upgrade(settings, store, bid)
    assert outcome["status"] == "done"
    assert store.get_batch(bid)["spec_version"] == specs.CURRENT
    assert store.load_totals(bid)["interval_usage"] == usage_before
    assert len(store.list_batches(limit=100)) == batch_count
    rows = store.load_readings(bid)
    assert sum(1 for row in rows if "K_UNIT_MATERIALIZED" in row["flags"]) == 2


def test_upgrade_is_idempotent_and_single_record(settings, store):
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    first = upgrade.upgrade(settings, store, bid)
    second = upgrade.upgrade(settings, store, bid)
    assert first["status"] == second["status"] == "done"
    assert second["idempotent_replay"] is True
    assert len(store.list_upgrades(bid)) == 1
    assert len(store.list_batches(limit=100)) == 1


def test_upgrade_refuses_when_evidence_insufficient(settings, store):
    """空单位 + 读数自带单位：旧规范能算，但无法证明唯一单位，升级必须拒绝。"""
    result = _legacy(settings, store, sample="readings_blank_unit_ambiguous.csv")
    bid = result["batch"]["batch_id"]
    usage_before = store.load_totals(bid)["interval_usage"]

    plan = upgrade.plan(settings, store, bid)
    assert plan["can_upgrade"] is False
    assert any(gap["code"] == "K_UNIT_EVIDENCE" for gap in plan["gaps"])

    outcome = upgrade.upgrade(settings, store, bid)
    assert outcome["status"] == "refused"
    assert outcome["unchanged"] is True
    assert store.get_batch(bid)["spec_version"] == specs.LEGACY
    assert store.load_totals(bid)["interval_usage"] == usage_before
    record = store.list_upgrades(bid)[0]
    assert record["status"] == "refused"


def test_upgrade_refused_batch_retry_does_not_grow(settings, store):
    result = _legacy(settings, store, sample="readings_blank_unit_ambiguous.csv")
    bid = result["batch"]["batch_id"]
    for _ in range(3):
        upgrade.upgrade(settings, store, bid)
    assert len(store.list_upgrades(bid)) == 1
    assert store.list_upgrades(bid)[0]["status"] == "refused"


def test_upgrade_resumes_after_interruption(settings, store):
    """中断只留下 running 记录，旧账不动；续跑在同一 upgrade_id 上完成。"""
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    usage_before = store.load_totals(bid)["interval_usage"]
    uid = upgrade.upgrade_id_for(bid, specs.CURRENT)
    store.save_upgrade({
        "upgrade_id": uid, "batch_id": bid, "from_version": specs.LEGACY,
        "to_version": specs.CURRENT, "status": "running", "phase": "restore",
        "started_at": history.iso(history.now_utc()), "finished_at": None,
        "from_digest": "frozen-before-crash", "to_digest": None,
        "note": "", "detail": {}})
    assert store.get_batch(bid)["spec_version"] == specs.LEGACY
    assert store.load_totals(bid)["interval_usage"] == usage_before

    resumed = upgrade.resume_pending(settings, store, bid)
    assert resumed["status"] == "done" and resumed["resumed"] is True
    assert store.get_batch(bid)["spec_version"] == specs.CURRENT
    assert store.load_totals(bid)["interval_usage"] == usage_before
    assert len(store.list_upgrades(bid)) == 1
    assert len(store.list_batches(limit=100)) == 1
    assert upgrade.resume_pending(settings, store, bid)["status"] == "idle"


def test_upgrade_stage_failure_leaves_old_account_intact(settings, store, monkeypatch):
    """升级在某阶段抛错：旧账行数与合计原样保留，记录停在 running 可续跑。"""
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    before = store.count_rows(bid)
    usage_before = store.load_totals(bid)["interval_usage"]

    from app.modules.m6_batches import upgrade as upgrade_mod
    calls = {"n": 0}
    original = upgrade_mod.allocate_mod.allocate

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("模拟升级中断")
        return original(*args, **kwargs)

    monkeypatch.setattr(upgrade_mod.allocate_mod, "allocate", flaky)
    with pytest.raises(RuntimeError):
        upgrade.upgrade(settings, store, bid)

    assert store.get_batch(bid)["spec_version"] == specs.LEGACY
    assert store.count_rows(bid) == before
    assert store.load_totals(bid)["interval_usage"] == usage_before
    assert store.list_upgrades(bid)[0]["status"] == "running"

    outcome = upgrade.resume_pending(settings, store, bid)
    assert outcome["status"] == "done"
    assert store.get_batch(bid)["spec_version"] == specs.CURRENT
    assert store.load_totals(bid)["interval_usage"] == usage_before
    assert len(store.list_batches(limit=100)) == 1


def test_concurrent_upgrade_creates_single_record(settings, store):
    """两个线程同时点升级：只有一个升级记录，仍是同一个批次。"""
    import threading
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    outcomes: list[dict] = []
    errors_seen: list[BaseException] = []

    def worker():
        try:
            outcomes.append(upgrade.upgrade(settings, store, bid))
        except BaseException as exc:  # noqa: BLE001
            errors_seen.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors_seen
    assert {item["status"] for item in outcomes} == {"done"}
    assert len(store.list_upgrades(bid)) == 1
    assert len(store.list_batches(limit=100)) == 1
    assert store.get_batch(bid)["spec_version"] == specs.CURRENT


def test_upgrade_rejects_changed_source_input(settings, store, sample_copy):
    run_settings = _with_sample_dir(settings, sample_copy)
    result = _legacy(run_settings, store)
    bid = result["batch"]["batch_id"]
    path = sample_copy / "readings_blank_unit.csv"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    outcome = upgrade.upgrade(run_settings, store, bid)
    assert outcome["status"] == "refused"
    assert "摘要不一致" in outcome["reason"]
    assert store.get_batch(bid)["spec_version"] == specs.LEGACY


def test_upgrade_target_must_be_known(settings, store):
    result = _legacy(settings, store)
    with pytest.raises(specs.UnknownSpec):
        upgrade.upgrade(settings, store, result["batch"]["batch_id"], "hydrocalendar/9.9.9")


def test_retry_keeps_bound_spec_and_no_new_batch(settings, store):
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    again = service.retry(settings, store, bid)
    assert again["batch"]["batch_id"] == bid
    assert store.get_batch(bid)["spec_version"] == specs.LEGACY
    assert len(store.list_batches(limit=100)) == 1
    assert int(store.load_totals(bid)["quarantine"]) == 0


# ---------- 并发切换 ----------
def test_inflight_batch_keeps_original_spec(settings, store):
    """一批旧规范在跑；期间把现行规范切到另一版本，在途批次仍按旧规范落地。"""
    inflight = _legacy(settings, store)
    bid = inflight["batch"]["batch_id"]
    switched = dataclasses.replace(settings, rule_version="hydrocalendar/2.0.0")
    service.retry(switched, store, bid)
    assert store.get_batch(bid)["spec_version"] == specs.LEGACY
    assert store.load_manifest(bid)["rule_version"] == specs.LEGACY

    fresh = service.run(switched, store, sample="readings_dirty.csv")
    assert fresh["batch"]["spec_version"] == specs.CURRENT
    assert len(store.list_batches(limit=100)) == 2


def test_retry_after_spec_switch_does_not_create_batch(settings, store):
    result = service.run(settings, store, sample="readings_blank_unit.csv")
    bid = result["batch"]["batch_id"]
    legacy_settings = dataclasses.replace(settings, rule_version=specs.LEGACY)
    service.retry(legacy_settings, store, bid)
    assert store.get_batch(bid)["spec_version"] == specs.CURRENT
    assert len(store.list_batches(limit=100)) == 1


def test_migrated_batch_reads_old_multiplier_from_snapshot(settings, store, sample_copy):
    run_settings = _with_sample_dir(settings, sample_copy)
    result = _legacy(run_settings, store, sample="readings_clean.csv")
    bid = result["batch"]["batch_id"]
    upgrade.upgrade(run_settings, store, bid)
    meters = sample_copy / "meters.csv"
    meters.write_text(meters.read_text(encoding="utf-8").replace(
        "M0002,砖厂专变,99999.9,40,", "M0002,砖厂专变,99999.9,80,"), encoding="utf-8")
    allocation = months_service.run(run_settings, store, bid)
    assert allocation["contracts"]["M0002"].multiplier == Decimal("40")
    assert any(item["meter_id"] == "M0002" for item in allocation["allocations"])


def test_retry_after_upgrade_keeps_materialized_units(settings, store):
    """升级后重试仍按固化的单位重算，不能把历史空单位行重新隔离掉。"""
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    upgrade.upgrade(settings, store, bid)
    migrated = store.load_totals(bid)["interval_usage"]

    service.retry(settings, store, bid)
    assert store.load_totals(bid)["interval_usage"] == migrated
    assert int(store.load_totals(bid)["quarantine"]) == 0
    rows = store.load_readings(bid)
    assert sum(1 for row in rows if "K_UNIT_MATERIALIZED" in row["flags"]) == 2
    assert len(store.list_batches(limit=100)) == 1


def test_new_batch_still_isolates_blank_units_after_upgrade(settings, store):
    """升级只作用于既有批次；同样例新开批次仍按现行规范隔离空单位行。"""
    result = _legacy(settings, store)
    upgrade.upgrade(settings, store, result["batch"]["batch_id"])
    fresh = service.run(settings, store, sample="readings_blank_unit.csv")
    assert fresh["batch"]["batch_id"] != result["batch"]["batch_id"]
    assert int(fresh["totals"]["quarantine"]) == 2


def test_intervals_page_usage_check_flags_rebuild_changes(settings, store, sample_copy):
    """现行契约被改后，页面重算能看到「重算变动」；落库账仍按快照。"""
    run_settings = _with_sample_dir(settings, sample_copy)
    result = _legacy(run_settings, store, sample="readings_clean.csv")
    bid = result["batch"]["batch_id"]
    stored = intervals_service.rebuild(run_settings, store, bid)["intervals"]
    check = intervals_service.usage_check(store, bid, stored)
    assert check["matches"] is True and check["rebuilt_changed"] == 0
    assert check["stored_usage"] == check["recomputed_usage"]

    meters = sample_copy / "meters.csv"
    meters.write_text(meters.read_text(encoding="utf-8").replace(
        "M0002,砖厂专变,99999.9,40,", "M0002,砖厂专变,99999.9,80,"), encoding="utf-8")
    # 快照仍在，重算不变；这里再构造一份不同结果模拟「契约快照被绕过」。
    drifted = [dict(item) for item in stored]
    drifted[0] = {**drifted[0], "usage_kwh": drifted[0]["usage_kwh"] + Decimal("1")}
    drifted_check = intervals_service.usage_check(store, bid, drifted)
    assert drifted_check["rebuilt_changed"] == 1
    assert drifted_check["matches"] is False


# ---------- 区间页与用量核对 ----------
def test_intervals_page_selects_batch_and_shows_usage(client, settings, store):
    first = client.post("/api/runs", json={"sample": "readings_clean.csv"}).json()
    second = client.post("/api/runs", json={"sample": "readings_dirty.csv"}).json()
    page = client.get(f"/intervals?batch_id={first['batch_id']}")
    assert page.status_code == 200
    assert first["batch_id"] in page.text
    assert second["batch_id"] in page.text
    assert "用量核对" in page.text


def test_intervals_rebuild_endpoint_returns_usage_check(client):
    run = client.post("/api/runs", json={"sample": "readings_dirty.csv"}).json()
    response = client.post("/api/intervals/rebuild", json={"batch_id": run["batch_id"]})
    body = response.json()
    assert body["batch_id"] == run["batch_id"]
    check = body["usage_check"]
    assert check["matches"] is True
    assert check["interval_minus_month"] == "0.000"
    assert Decimal(check["interval_usage"]) == expected.TOTAL_USAGE


def test_upgrade_api_end_to_end(settings, store, client):
    result = _legacy(settings, store)
    bid = result["batch"]["batch_id"]
    plan = client.get(f"/api/batches/{bid}/upgrade/plan").json()
    assert plan["can_upgrade"] is True
    response = client.post(f"/api/batches/{bid}/upgrade", json={})
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "done"
    assert body["spec_version"] == specs.CURRENT
    status = client.get(f"/api/specs/upgrades?batch_id={bid}").json()
    assert status["spec_version"] == specs.CURRENT
    assert status["upgrades"][0]["status"] == "done"
    batches = client.get("/api/batches").json()["batches"]
    assert [item["spec_version"] for item in batches] == [specs.CURRENT]


def test_upgrade_api_refusal_returns_200_with_unchanged(settings, store, client):
    result = _legacy(settings, store, sample="readings_blank_unit_ambiguous.csv")
    bid = result["batch"]["batch_id"]
    response = client.post(f"/api/batches/{bid}/upgrade", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "refused"
    assert body["unchanged"] is True
    assert body["spec_version"] == specs.LEGACY


def test_upgrade_api_unknown_batch_404(client):
    response = client.get("/api/batches/mb-missing/upgrade/plan")
    assert response.status_code == 404
    assert response.json()["error"] == "E_BATCH_NOT_FOUND"


def test_spec_catalog_lists_both_versions(client):
    body = client.get("/api/specs").json()
    versions = {item["version"] for item in body["specs"]}
    assert versions == {specs.LEGACY, specs.CURRENT}
    assert body["current"] == specs.CURRENT


def test_http_run_always_binds_current_spec(client, settings, store):
    """HTTP 入口不接受把新数据塞回旧规范：新批次一律现行规范。"""
    response = client.post("/api/runs", json={"sample": "readings_clean.csv",
                                              "spec_version": specs.LEGACY})
    assert response.status_code == 200
    assert response.json()["spec_version"] == specs.CURRENT


def test_service_rejects_unknown_spec(settings, store):
    with pytest.raises(errors.BadRequest) as excinfo:
        service.run(settings, store, sample="readings_clean.csv",
                    spec_version="nope")
    assert excinfo.value.code == "E_SPEC_UNKNOWN"


def test_reason_codes_registered():
    from app import reasons
    for code in ("Q_MISSING_UNIT", "K_UNIT_MISSING", "K_UNIT_MATERIALIZED",
                 "K_SPEC_UPGRADED", "K_SPEC_UPGRADE_REFUSED",
                 "E_UPGRADE_IN_PROGRESS", "E_INPUT_CHANGED"):
        assert reasons.label(code) != code


# ---------- 存储迁移 ----------
def test_preexisting_batches_migrate_to_legacy_binding(tmp_path, settings):
    """迁移前写入的批次 spec_version 为空：必须补成旧规范，不能当新规范。"""
    import sqlite3
    path = tmp_path / "old.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE batches (batch_id TEXT PRIMARY KEY, batch_key TEXT NOT NULL,
               run_id TEXT NOT NULL, sample TEXT NOT NULL, status TEXT NOT NULL,
               created_at TEXT NOT NULL, finished_at TEXT, input_digest TEXT NOT NULL,
               rule_version TEXT NOT NULL, params_json TEXT NOT NULL DEFAULT '{}',
               note TEXT NOT NULL DEFAULT '');
           INSERT INTO batches VALUES ('mb-old','k','r','s','done','t',NULL,'d',
               'hydrocalendar/1.0.0','{}','');""")
    conn.commit()
    conn.close()
    from app.store import Store
    store = Store(str(path))
    batch = store.get_batch("mb-old")
    assert batch["spec_version"] == specs.LEGACY
    assert specs.bound(batch["spec_version"]).unit_required is False
