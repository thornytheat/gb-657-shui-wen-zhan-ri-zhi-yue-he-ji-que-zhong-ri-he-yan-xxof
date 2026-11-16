"""M1 入口：导入样例、看逐行诊断与采信阶段计数。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import config
from ...web import Ctx, get_ctx, render
from ..m3_quality import quarantine as quarantine_mod
from ..m6_batches import resume as resume_mod
from ..m6_batches import service as batch_service
from . import service

router = APIRouter(tags=["M1 读数采信"])


def stage_payload(item: dict) -> dict:
    label = item.get("label") or config.STAGE_LABELS.get(item["stage"], item["stage"])
    return {"stage": item["stage"], "label": label,
            "in_count": item["in_count"], "out_count": item["out_count"],
            "quarantine_count": item["quarantine_count"],
            "status": item["status"], "elapsed_ms": item["elapsed_ms"],
            "note": item.get("note", "")}


@router.post("/api/readings/import")
def api_import(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    body = payload or {}
    sample = str(body.get("sample") or config.DEFAULT_SAMPLE)
    result = batch_service.run(ctx.settings, ctx.store, sample=sample,
                              mode=str(body.get("mode") or "full"))
    ingest = result.get("ingest") or {}
    quarantine = ctx.store.load_quarantine(result["batch"]["batch_id"])
    return {
        "status": result["status"],
        "batch_id": result["batch"]["batch_id"],
        "run_id": result["batch"]["run_id"],
        "idempotent_replay": result["idempotent_replay"],
        "sample": sample,
        "encoding": ingest.get("encoding"),
        "bom_stripped": ingest.get("bom_stripped"),
        "fallback": ingest.get("fallback"),
        "segments": ingest.get("segments"),
        "raw_lines": ingest.get("raw_lines"),
        "blank_lines": ingest.get("blank_lines"),
        "parsed": ingest.get("parsed"),
        "bytes": ingest.get("bytes"),
        "digest": ingest.get("digest"),
        "diagnostics": ingest.get("diagnostics") or [],
        "stages": [stage_payload(item) for item in result["stages"]],
        "quarantine": quarantine_mod.summary(quarantine),
    }


@router.get("/api/readings/{batch_id}")
def api_readings(batch_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = resume_mod.require_batch(ctx.store, batch_id)
    ingest = ctx.store.load_stages(batch_id)
    readings = ctx.store.load_readings(batch_id)
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "stages": ingest,
        "readings": [{
            "lineno": item["lineno"], "segment": item["segment"],
            "meter_id": item["meter_id"], "register": item["register"],
            "read_at": item["read_at"], "status": item["status"],
            "reading": None if item["reading_norm"] is None
            else str(item["reading_norm"]),
        } for item in readings],
    }


@router.get("/readings")
def page_readings(request: Request, batch_id: str | None = None,
                  ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    readings: list[dict] = []
    quarantine: list[dict] = []
    stages: list[dict] = []
    if batch is not None:
        readings = ctx.store.load_readings(batch["batch_id"])
        quarantine = ctx.store.load_quarantine(batch["batch_id"])
        stages = ctx.store.load_stages(batch["batch_id"])
    return render(request, "readings.html", page="readings", page_title="读数诊断",
                  batch=batch, readings=readings, quarantine=quarantine,
                  stages=stages, samples=service.available_samples(ctx.settings),
                  distribution=quarantine_mod.distribution(quarantine),
                  qsummary=quarantine_mod.summary(quarantine))
