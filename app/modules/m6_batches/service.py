"""批次编排：采信 → 契约 → 质量 → 还原 → 切分 → 对账 → 导出。

一个批次的所有行在一个事务里写入，失败整体回滚；业务失败（空文件、契约无效）
只落批次与阶段，方便页面上看到失败阶段并重试。
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

from ... import config, errors, manifest, reasons, specs
from ..m1_ingest import service as m1_service
from ..m2_contract import contract as contract_mod
from ..m2_contract import service as m2_service
from ..m3_quality import service as m3_service
from ..m4_intervals import restore as restore_mod
from ..m5_months import allocate as allocate_mod
from . import compare, history, resume


def _ms(start: float) -> int:
    return max(1, int((time.perf_counter() - start) * 1000))


def _bound_contracts(settings, store, batch_id: str | None,
                     ) -> dict[str, contract_mod.MeterContract]:
    """批次已存在时用它的契约快照；新批次才读现行 meters.csv。"""
    if batch_id:
        snapshot = store.load_meters(batch_id)
        if snapshot:
            return contract_mod.from_meter_rows(snapshot)
    return contract_mod.load(settings)


def _bound_prices(settings, store, batch_id: str | None) -> dict[str, Decimal]:
    if batch_id:
        snapshot = store.load_prices(batch_id)
        if snapshot:
            return snapshot
    return contract_mod.load_prices(settings)


def run(settings, store, *, sample: str | None = None, mode: str = "full",
        batch_id: str | None = None, spec_version: str | None = None,
        materialize_missing_unit: bool | None = None) -> dict:
    """跑一次全流程。

    - 新批次：绑定 `settings.rule_version`（现行规范）并快照契约与电价；
    - 已有批次（重试 / 重建）：沿用该批次绑定的规范与输入摘要，绝不新增批次；
    - 在途批次：规范在进入本函数时就已固定，之后切换现行规范不影响它；
    - 已升级批次重试：继续沿用升级时固化的空单位，不会把历史行重新隔离。
    """
    existing = store.get_batch(batch_id) if batch_id else None
    if batch_id and existing is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_BATCH_NOT_FOUND")
    if spec_version is not None and spec_version not in specs.SPECS:
        raise errors.BadRequest(
            f"规范版本未知：{spec_version!r}", code="E_SPEC_UNKNOWN",
            detail={"spec_version": spec_version})
    bound_version = (spec_version
                     or (existing.get("spec_version") if existing else None)
                     or settings.rule_version)
    spec = specs.bound(bound_version)
    if materialize_missing_unit is None:
        materialize_missing_unit = bool(
            existing is not None and specs.bound(bound_version).unit_required
            and any(item["status"] == "done"
                    for item in store.list_upgrades(existing["batch_id"])))
    name = sample or (existing["sample"] if existing else config.DEFAULT_SAMPLE)
    path = m1_service.sample_path(settings, name)
    sample_bytes = path.read_bytes()
    meters_bytes = (Path(settings.sample_dir) / config.METERS_FILE).read_bytes()
    prices_bytes = (Path(settings.sample_dir) / config.PRICES_FILE).read_bytes()
    if existing is not None:
        # 重试 / 重建沿用原输入摘要与批次键：现行契约文件变了也不改写历史证据。
        digest = existing["input_digest"]
        key = existing["batch_key"]
        batch_id = existing["batch_id"]
    else:
        digest = manifest.input_digest(name, sample_bytes, meters_bytes, prices_bytes,
                                       spec.version)
        key = manifest.batch_key(name, digest)

    if existing is None and mode != "force":
        found = resume.find_replay(store, key)
        if found is not None:
            store.touch_batch(found["batch_id"], history.iso(history.now_utc()))
            found = store.get_batch(found["batch_id"]) or found
            return resume.replay_payload(found, store)

    started_at = history.iso(history.now_utc())
    batch = history.build_batch(
        batch_key=key, run_id=history.make_run_id(), sample=name, status="running",
        digest=digest, rule_version=spec.version,
        params={"sample": name, "mode": mode, "spec_version": spec.version},
        created_at=(existing["created_at"] if existing else started_at),
        updated_at=started_at, spec_version=spec.version)
    if existing is not None:
        batch["batch_id"] = batch_id
    batch_id = batch["batch_id"]

    t0 = time.perf_counter()
    stages: list[dict] = []

    def fail(stage: str, exc: errors.MeterbackError, elapsed: float):
        stages.append({"stage": stage, "in_count": 0, "out_count": 0,
                       "quarantine_count": 0, "status": "failed",
                       "elapsed_ms": _ms(elapsed), "note": exc.code})
        stamp = history.iso(history.now_utc())
        batch.update(status="failed", finished_at=stamp, updated_at=stamp,
                     note=exc.code)
        store.save_attempt(batch=batch, stages=stages, note=exc.code)
        exc.detail = dict(exc.detail, batch_id=batch_id, failed_stage=stage,
                          spec_version=spec.version)
        return exc

    # ---- M1 采信 ----
    mark = time.perf_counter()
    try:
        ingested = m1_service.ingest(settings, name, data=sample_bytes)
    except errors.MeterbackError as exc:
        raise fail("ingest", exc, mark) from exc
    stages.append({"stage": "ingest", "in_count": ingested.data_lines,
                   "out_count": len(ingested.rows),
                   "quarantine_count": len(ingested.quarantine), "status": "ok",
                   "elapsed_ms": _ms(mark),
                   "note": f"{ingested.encoding} · {ingested.segments} 段 · "
                           f"{spec.version}"})

    # ---- M2 契约 ----
    mark = time.perf_counter()
    try:
        if existing is not None:
            contracts = _bound_contracts(settings, store, batch_id)
            prices = _bound_prices(settings, store, batch_id)
        else:
            contracts = contract_mod.load(settings)
            prices = contract_mod.load_prices(settings)
    except errors.MeterbackError as exc:
        raise fail("contract", exc, mark) from exc
    records, contract_q, _ = m2_service.interpret_rows(
        ingested.rows, contracts, spec.version,
        materialize_missing_unit=materialize_missing_unit)
    stages.append({"stage": "contract", "in_count": len(ingested.rows),
                   "out_count": len(records), "quarantine_count": len(contract_q),
                   "status": "ok", "elapsed_ms": _ms(mark),
                   "note": f"{len(contracts)} 只表 · 单位"
                           f"{'必须明示' if spec.unit_required else '可按契约补齐'}"})

    # ---- M3 质量 ----
    mark = time.perf_counter()
    quality = m3_service.evaluate(records, contracts)
    stages.append({"stage": "quality", "in_count": len(records),
                   "out_count": len(quality["valid"]) + len(quality["estimate"]),
                   "quarantine_count": len(quality["quarantine"]), "status": "ok",
                   "elapsed_ms": _ms(mark),
                   "note": f"估抄 {len(quality['estimate'])}"})

    # ---- M4 还原 ----
    mark = time.perf_counter()
    intervals = restore_mod.build_intervals(quality["valid"], quality["estimate"],
                                            contracts)
    interval_stats = restore_mod.totals(intervals, contracts)
    stages.append({"stage": "restore", "in_count": len(quality["valid"]),
                   "out_count": len(intervals), "quarantine_count": 0, "status": "ok",
                   "elapsed_ms": _ms(mark),
                   "note": f"{interval_stats['primary_usage_kwh']} kWh"})

    # ---- M5 切分 ----
    mark = time.perf_counter()
    allocated = allocate_mod.allocate(intervals, contracts, prices)
    stages.append({"stage": "allocate", "in_count": len(intervals),
                   "out_count": len(allocated["allocations"]), "quarantine_count": 0,
                   "status": "ok", "elapsed_ms": _ms(mark),
                   "note": f"{allocated['stats']['month_count']} 个月"})

    # ---- M6 对账 ----
    mark = time.perf_counter()
    quarantine = ingested.quarantine + contract_q + quality["quarantine"]
    month_usage = allocated["stats"]["usage_total"]
    bill_total = sum((bill["amount_yuan"] for bill in allocated["bills"]), Decimal("0"))
    component_total = sum(
        (sum((Decimal(item["amount"]) for item in bill["components"]), Decimal("0"))
         for bill in allocated["bills"]), Decimal("0"))
    reconcile = compare.build(
        raw_lines=ingested.data_lines, valid=len(quality["valid"]),
        estimate=len(quality["estimate"]), quarantine=len(quarantine),
        blank=ingested.blank_lines,
        interval_usage=interval_stats["primary_usage_kwh"], month_usage=month_usage,
        tou_before=allocated["stats"]["tou_residual_before"],
        tou_after=allocated["stats"]["tou_residual_after"],
        tou_adjustments=allocated["stats"]["tou_adjustments"],
        bill_total=bill_total, component_total=component_total,
        bill_count=len(allocated["bills"]))
    stages.append({"stage": "reconcile", "in_count": len(reconcile["accounts"]),
                   "out_count": sum(1 for a in reconcile["accounts"] if a["balanced"]),
                   "quarantine_count": reconcile["diff_count"], "status": "ok",
                   "elapsed_ms": _ms(mark),
                   "note": "四账平衡" if reconcile["all_balanced"] else "四账不平"})

    # ---- M7 导出（文件由 GET 接口按需生成） ----
    mark = time.perf_counter()
    stages.append({"stage": "export", "in_count": len(allocated["allocations"]),
                   "out_count": 4, "quarantine_count": 0, "status": "ok",
                   "elapsed_ms": _ms(mark), "note": "4 个导出文件"})

    finished_at = history.iso(history.now_utc())
    elapsed_ms = _ms(t0)
    totals = {
        "raw_lines": ingested.data_lines,
        "valid": len(quality["valid"]),
        "estimate": len(quality["estimate"]),
        "quarantine": len(quarantine),
        "blank": ingested.blank_lines,
        "interval_usage": interval_stats["primary_usage_kwh"],
        "month_usage": month_usage,
        "bill_amount": bill_total.quantize(config.MONEY_QUANT),
        "interval_count": len(intervals),
        "allocation_count": len(allocated["allocations"]),
        "bill_count": len(allocated["bills"]),
        "month_count": allocated["stats"]["month_count"],
        "meter_count": len(contracts),
        "tou_adjustments": allocated["stats"]["tou_adjustments"],
        "segments": ingested.segments,
        "encoding": ingested.encoding,
        "balanced": "是" if reconcile["all_balanced"] else "否",
        "reconcile_json": json.dumps(reconcile, ensure_ascii=False, default=str),
    }
    prior_inputs: dict[str, dict] = {}
    if existing is not None:
        prior = store.load_manifest(batch_id) or {}
        prior_inputs = {item.get("name"): item for item in prior.get("inputs") or []}

    def _input(name_: str, digest_: str, size: int, encoding: str) -> dict:
        """重试时沿用首次运行的输入摘要，现行契约改动不覆盖历史证据。"""
        if name_ in prior_inputs:
            return dict(prior_inputs[name_], name=name_)
        return {"name": name_, "sha256": digest_, "bytes": size, "encoding": encoding}

    manifest_json = manifest.build(
        run_id=batch["run_id"], batch_id=batch_id, batch_key_value=key,
        settings=settings, sample=name, digest=digest,
        rule_version=spec.version,
        inputs=[_input(name, ingested.digest, ingested.byte_len, ingested.encoding),
                _input(config.METERS_FILE, manifest.digest_bytes(meters_bytes),
                       len(meters_bytes), "utf-8"),
                _input(config.PRICES_FILE, manifest.digest_bytes(prices_bytes),
                       len(prices_bytes), "utf-8")],
        params={"sample": name, "mode": mode, "rule_version": spec.version,
                "spec_version": spec.version},
        started_at=started_at, finished_at=finished_at, elapsed_ms=elapsed_ms,
        stages=stages, counters=history.counters(
            valid=len(quality["valid"]), estimate=len(quality["estimate"]),
            quarantine=len(quarantine), blank=ingested.blank_lines,
            intervals=len(intervals), allocations=len(allocated["allocations"]),
            bills=len(allocated["bills"])),
        reconcile=reconcile)

    readings_rows = [{
        "lineno": item["lineno"], "segment": item["segment"],
        "meter_id": item["meter_id"], "register": item["register"],
        "read_at": item["read_at"], "reading_text": item["reading_text"],
        "unit_raw": item["unit_raw"], "reading_norm": item["reading"],
        "unit_norm": item["unit_norm"], "read_type": item["read_type"],
        "status": "estimate" if item["read_type"] == "E" else "valid",
        "code": None, "flags": item.get("flags", []), "note": item.get("note", ""),
    } for item in (quality["valid"] + quality["estimate"])]

    store.save_run(
        batch={**batch, "status": "done", "finished_at": finished_at,
               "updated_at": finished_at},
        meters=contract_mod.meter_rows(contracts),
        readings=readings_rows,
        intervals=intervals,
        allocations=allocated["allocations"],
        bills=allocated["bills"],
        quarantine=quarantine,
        stages=stages,
        totals=totals,
        manifest_json=manifest_json,
        prices=prices)

    batch.update(status="done", finished_at=finished_at)
    return {
        "status": "done",
        "batch": batch,
        "idempotent_replay": False,
        "stages": stages,
        "totals": totals,
        "reconciliation": reconcile,
        "ingest": {
            "encoding": ingested.encoding,
            "bom_stripped": ingested.bom_stripped,
            "fallback": ingested.fallback,
            "segments": ingested.segments,
            "raw_lines": ingested.data_lines,
            "blank_lines": ingested.blank_lines,
            "parsed": len(ingested.rows),
            "diagnostics": ingested.diagnostics,
            "digest": ingested.digest,
            "bytes": ingested.byte_len,
        },
        "counts": store.count_rows(batch_id),
    }


def retry(settings, store, batch_id: str) -> dict:
    """强制重算：同一批次、同一规范、同一契约快照，绝不新建批次。"""
    plan = resume.retry_plan(store, batch_id)
    return run(settings, store, sample=plan["sample"], mode="force",
               batch_id=batch_id)


def quarantine_rows(store, batch_id: str) -> list[dict]:
    return store.load_quarantine(batch_id)


def reason_label(code: str) -> str:
    return reasons.label(code)
