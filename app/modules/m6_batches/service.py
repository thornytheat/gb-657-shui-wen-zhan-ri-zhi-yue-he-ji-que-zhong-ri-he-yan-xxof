"""批次编排：采信 → 契约 → 质量 → 还原 → 切分 → 对账 → 导出。

一个批次的所有行在一个事务里写入，失败整体回滚；业务失败（空文件、契约无效）
只落批次与阶段，方便页面上看到失败阶段并重试。
"""
from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path

from ... import config, errors, manifest, reasons
from ..m1_ingest import service as m1_service
from ..m2_contract import contract as contract_mod
from ..m2_contract import service as m2_service
from ..m3_quality import service as m3_service
from ..m4_intervals import restore as restore_mod
from ..m5_months import allocate as allocate_mod
from . import compare, history, resume


def _ms(start: float) -> int:
    return max(1, int((time.perf_counter() - start) * 1000))


def run(settings, store, *, sample: str | None = None, mode: str = "full") -> dict:
    name = sample or config.DEFAULT_SAMPLE
    path = m1_service.sample_path(settings, name)
    sample_bytes = path.read_bytes()
    meters_bytes = (Path(settings.sample_dir) / config.METERS_FILE).read_bytes()
    prices_bytes = (Path(settings.sample_dir) / config.PRICES_FILE).read_bytes()
    digest = manifest.input_digest(name, sample_bytes, meters_bytes, prices_bytes,
                                   settings.rule_version)
    key = manifest.batch_key(name, digest)

    if mode != "force":
        existing = resume.find_replay(store, key)
        if existing is not None:
            store.touch_batch(existing["batch_id"], history.iso(history.now_utc()))
            existing = store.get_batch(existing["batch_id"]) or existing
            return resume.replay_payload(existing, store)

    started_at = history.iso(history.now_utc())
    batch = history.build_batch(
        batch_key=key, run_id=history.make_run_id(), sample=name, status="running",
        digest=digest, rule_version=settings.rule_version,
        params={"sample": name, "mode": mode}, created_at=started_at,
        updated_at=started_at)
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
        exc.detail = dict(exc.detail, batch_id=batch_id, failed_stage=stage)
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
                   "note": f"{ingested.encoding} · {ingested.segments} 段"})

    # ---- M2 契约 ----
    mark = time.perf_counter()
    try:
        contracts = contract_mod.load(settings)
    except errors.MeterbackError as exc:
        raise fail("contract", exc, mark) from exc
    records, contract_q, _ = m2_service.interpret_rows(ingested.rows, contracts)
    stages.append({"stage": "contract", "in_count": len(ingested.rows),
                   "out_count": len(records), "quarantine_count": len(contract_q),
                   "status": "ok", "elapsed_ms": _ms(mark),
                   "note": f"{len(contracts)} 只表"})

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
    prices = contract_mod.load_prices(settings)
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
    manifest_json = manifest.build(
        run_id=batch["run_id"], batch_id=batch_id, batch_key_value=key,
        settings=settings, sample=name, digest=digest,
        inputs=[{"name": name, "sha256": ingested.digest,
                 "bytes": ingested.byte_len, "encoding": ingested.encoding},
                {"name": config.METERS_FILE,
                 "sha256": manifest.digest_bytes(meters_bytes),
                 "bytes": len(meters_bytes), "encoding": "utf-8"},
                {"name": config.PRICES_FILE,
                 "sha256": manifest.digest_bytes(prices_bytes),
                 "bytes": len(prices_bytes), "encoding": "utf-8"}],
        params={"sample": name, "mode": mode, "rule_version": settings.rule_version},
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
        meters=[{
            "meter_id": m.meter_id, "name": m.name, "capacity": m.capacity,
            "multiplier": m.multiplier, "unit": m.unit, "registers": list(m.registers),
            "replaced_at": m.replaced_at.isoformat() if m.replaced_at else None,
            "replaced_by": m.replaced_by, "tariff": m.tariff,
        } for m in contracts.values()],
        readings=readings_rows,
        intervals=intervals,
        allocations=allocated["allocations"],
        bills=allocated["bills"],
        quarantine=quarantine,
        stages=stages,
        totals=totals,
        manifest_json=manifest_json)

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
    plan = resume.retry_plan(store, batch_id)
    return run(settings, store, sample=plan["sample"], mode="force")


def quarantine_rows(store, batch_id: str) -> list[dict]:
    return store.load_quarantine(batch_id)


def reason_label(code: str) -> str:
    return reasons.label(code)
