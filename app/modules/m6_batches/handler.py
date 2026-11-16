"""M6 入口：跑一次全流程、查批次历史、看四账、失败重试、作业总览页。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from ... import config, specs
from ...web import Ctx, get_ctx, render
from ..m1_ingest import service as m1_service
from ..m2_contract import contract as contract_mod
from ..m3_quality import quarantine as quarantine_mod
from . import compare, resume, service, upgrade

router = APIRouter(tags=["M6 批次与对账"])


def _stage_payload(item: dict) -> dict:
    label = item.get("label") or config.STAGE_LABELS.get(item["stage"], item["stage"])
    return {"stage": item["stage"], "label": label,
            "in_count": item["in_count"], "out_count": item["out_count"],
            "quarantine_count": item["quarantine_count"],
            "status": item["status"], "elapsed_ms": item["elapsed_ms"],
            "note": item.get("note", "")}


@router.post("/api/runs")
def api_run(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    body = payload or {}
    sample = str(body.get("sample") or config.DEFAULT_SAMPLE)
    # 新批次一律绑定现行规范：HTTP 入口不接受把新数据塞回旧规范。
    # 旧规范只存在于升级入口针对既有批次的处理里。
    result = service.run(ctx.settings, ctx.store, sample=sample,
                         mode=str(body.get("mode") or "full"))
    batch = result["batch"]
    reconcile = result.get("reconciliation")
    if reconcile is None:
        totals = ctx.store.load_totals(batch["batch_id"])
        reconcile = json.loads(totals.get("reconcile_json") or "null")
    return {
        "status": result["status"],
        "batch_id": batch["batch_id"],
        "run_id": batch["run_id"],
        "batch_key": batch["batch_key"],
        "input_digest": batch["input_digest"],
        "spec_version": batch.get("spec_version") or batch["rule_version"],
        "idempotent_replay": result["idempotent_replay"],
        "stages": [_stage_payload(item) for item in result["stages"]],
        "totals": {key: str(value) for key, value in result["totals"].items()},
        "reconcile": compare.payload(reconcile),
        "counts": result["counts"],
    }


@router.get("/api/specs")
def api_specs(ctx: Ctx = Depends(get_ctx)) -> dict:
    return {"current": specs.CURRENT, "default": specs.DEFAULT,
            "specs": specs.catalog(),
            "batches": [{
                "batch_id": item["batch_id"], "sample": item["sample"],
                "status": item["status"],
                "spec_version": specs.resolve(item.get("spec_version")),
                "spec_label": specs.label(item.get("spec_version")),
            } for item in ctx.store.list_batches(limit=50)]}


@router.get("/api/specs/upgrades")
def api_upgrades(batch_id: str | None = None,
                 ctx: Ctx = Depends(get_ctx)) -> dict:
    return upgrade.status(ctx.store, batch_id)


@router.get("/api/batches/{batch_id}/upgrade/plan")
def api_upgrade_plan(batch_id: str, to: str | None = None,
                     ctx: Ctx = Depends(get_ctx)) -> dict:
    return upgrade.plan(ctx.settings, ctx.store, batch_id, to)


@router.post("/api/batches/{batch_id}/upgrade")
def api_upgrade(batch_id: str, payload: dict | None = None,
                ctx: Ctx = Depends(get_ctx)) -> dict:
    body = payload or {}
    result = upgrade.upgrade(ctx.settings, ctx.store, batch_id,
                             body.get("to_version"),
                             resume=bool(body.get("resume", True)))
    result["spec_version"] = upgrade.status(ctx.store, batch_id)["spec_version"]
    return result


@router.post("/api/batches/{batch_id}/upgrade/resume")
def api_upgrade_resume(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    result = upgrade.resume_pending(ctx.settings, ctx.store, batch_id)
    result["spec_version"] = upgrade.status(ctx.store, batch_id)["spec_version"]
    return result


@router.get("/api/batches")
def api_batches(limit: int = 20, ctx: Ctx = Depends(get_ctx)) -> dict:
    batches = ctx.store.list_batches(limit=limit)
    return {"batches": [{
        "batch_id": item["batch_id"], "run_id": item["run_id"],
        "sample": item["sample"], "status": item["status"],
        "created_at": item["created_at"], "finished_at": item["finished_at"],
        "input_digest": item["input_digest"],
        "rule_version": item["rule_version"],
        "spec_version": specs.resolve(item.get("spec_version")),
        "spec_label": specs.label(item.get("spec_version")),
        "note": item["note"],
    } for item in batches]}


@router.get("/api/reconcile")
def api_reconcile(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        return {"batch_id": None, "accounts": [], "all_balanced": None}
    totals = ctx.store.load_totals(batch["batch_id"])
    reconcile = json.loads(totals.get("reconcile_json") or "null")
    body = compare.payload(reconcile)
    body["batch_id"] = batch["batch_id"]
    return body


@router.post("/api/batches/{batch_id}/retry")
def api_retry(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    resume.require_batch(ctx.store, batch_id)
    result = service.retry(ctx.settings, ctx.store, batch_id)
    return {"status": result["status"], "batch_id": result["batch"]["batch_id"],
            "run_id": result["batch"]["run_id"],
            "idempotent_replay": result["idempotent_replay"],
            "stages": [_stage_payload(item) for item in result["stages"]]}


@router.get("/api/health")
def api_health(ctx: Ctx = Depends(get_ctx)) -> dict:
    latest = ctx.store.latest_batch()
    return {"status": "ok", "rule_version": ctx.settings.rule_version,
            "current_spec": specs.CURRENT,
            "latest_batch": latest["batch_id"] if latest else None}


def _upgrade_context(ctx: Ctx, batch: dict | None) -> dict:
    if batch is None:
        return {"spec_catalog": specs.catalog(), "current_spec": specs.CURRENT,
                "batch_spec": None, "upgrade_plan": None, "upgrade_history": []}
    plan = None
    current = specs.bound(batch.get("spec_version"))
    if current.version != specs.CURRENT:
        plan = upgrade.plan(ctx.settings, ctx.store, batch["batch_id"])
    return {"spec_catalog": specs.catalog(), "current_spec": specs.CURRENT,
            "batch_spec": current, "upgrade_plan": plan,
            "upgrade_history": upgrade.status(ctx.store, batch["batch_id"])["upgrades"]}


def page_index(request: Request, batch_id: str | None = None,
               ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    totals: dict = {}
    reconcile = None
    quarantine: list[dict] = []
    contracts = None
    if batch is not None:
        totals = ctx.store.load_totals(batch["batch_id"])
        reconcile = json.loads(totals.get("reconcile_json") or "null")
        quarantine = ctx.store.load_quarantine(batch["batch_id"])
        contracts = contract_mod.from_meter_rows(ctx.store.load_meters(batch["batch_id"]))
    if not contracts:
        contracts = contract_mod.load(ctx.settings)
    return render(request, "index.html", page="index", page_title="作业总览",
                  batch=batch, totals=totals,
                  reconcile=compare.page_rows(reconcile),
                  balanced=None if reconcile is None else reconcile["all_balanced"],
                  qsummary=quarantine_mod.summary(quarantine),
                  samples=m1_service.available_samples(ctx.settings),
                  meters=contract_mod.payload(contracts),
                  swaps=[item for item in contracts.values() if item.replaced_by],
                  the_batches=ctx.store.list_batches(limit=5),
                  **_upgrade_context(ctx, batch))


@router.get("/batches")
def page_batches(request: Request, batch_id: str | None = None,
                 ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    totals: dict = {}
    reconcile = None
    counts: dict = {}
    manifest_summary: list[dict] = []
    if batch is not None:
        totals = ctx.store.load_totals(batch["batch_id"])
        reconcile = json.loads(totals.get("reconcile_json") or "null")
        counts = ctx.store.count_rows(batch["batch_id"])
        from ..m7_export import manifest_out
        manifest_summary = manifest_out.summary(
            manifest_out.build(ctx.store, batch,
                               ctx.store.load_manifest(batch["batch_id"])))
    return render(request, "batches.html", page="batches", page_title="批次对账",
                  batch=batch, totals=totals,
                  reconcile=compare.page_rows(reconcile),
                  balanced=None if reconcile is None else reconcile["all_balanced"],
                  counts=counts, manifest_summary=manifest_summary,
                  the_batches=ctx.store.list_batches(limit=20),
                  **_upgrade_context(ctx, batch))
