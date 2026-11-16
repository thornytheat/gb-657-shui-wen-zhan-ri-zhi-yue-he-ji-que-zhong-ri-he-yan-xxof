"""M6 入口：跑一次全流程、查批次历史、看四账、失败重试、作业总览页。"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request

from ... import config
from ...web import Ctx, get_ctx, render
from ..m1_ingest import service as m1_service
from ..m2_contract import contract as contract_mod
from ..m3_quality import quarantine as quarantine_mod
from . import compare, resume, service

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
        "idempotent_replay": result["idempotent_replay"],
        "stages": [_stage_payload(item) for item in result["stages"]],
        "totals": {key: str(value) for key, value in result["totals"].items()},
        "reconcile": compare.payload(reconcile),
        "counts": result["counts"],
    }


@router.get("/api/batches")
def api_batches(limit: int = 20, ctx: Ctx = Depends(get_ctx)) -> dict:
    batches = ctx.store.list_batches(limit=limit)
    return {"batches": [{
        "batch_id": item["batch_id"], "run_id": item["run_id"],
        "sample": item["sample"], "status": item["status"],
        "created_at": item["created_at"], "finished_at": item["finished_at"],
        "input_digest": item["input_digest"], "rule_version": item["rule_version"],
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
            "latest_batch": latest["batch_id"] if latest else None}


def page_index(request: Request, batch_id: str | None = None,
               ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    totals: dict = {}
    reconcile = None
    quarantine: list[dict] = []
    contracts = contract_mod.load(ctx.settings)
    if batch is not None:
        totals = ctx.store.load_totals(batch["batch_id"])
        reconcile = json.loads(totals.get("reconcile_json") or "null")
        quarantine = ctx.store.load_quarantine(batch["batch_id"])
    return render(request, "index.html", page="index", page_title="作业总览",
                  batch=batch, totals=totals,
                  reconcile=compare.page_rows(reconcile),
                  balanced=None if reconcile is None else reconcile["all_balanced"],
                  qsummary=quarantine_mod.summary(quarantine),
                  samples=m1_service.available_samples(ctx.settings),
                  meters=contract_mod.payload(contracts),
                  swaps=[item for item in contracts.values() if item.replaced_by],
                  the_batches=ctx.store.list_batches(limit=5))


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
                  the_batches=ctx.store.list_batches(limit=20))
