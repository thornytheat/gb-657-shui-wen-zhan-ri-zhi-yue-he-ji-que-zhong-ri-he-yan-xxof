"""M3 入口：原因码分布、隔离行明细、隔离区 CSV。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response

from ... import config, reasons
from ...web import Ctx, get_ctx
from ..m6_batches import resume as resume_mod
from ..m7_export import csv_out, evidence
from . import quarantine

router = APIRouter(tags=["M3 质量隔离"])


@router.get("/api/quarantine")
def api_quarantine(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        return {"batch_id": None, "rows": [], "distribution": [], "summary": None}
    rows = ctx.store.load_quarantine(batch["batch_id"])
    return {
        "batch_id": batch["batch_id"],
        "count": len(rows),
        "distribution": quarantine.distribution(rows),
        "summary": quarantine.summary(rows),
        "rows": [quarantine.to_dict(item) for item in rows],
    }


@router.get("/api/quarantine.csv")
def api_quarantine_csv(batch_id: str | None = None,
                       ctx: Ctx = Depends(get_ctx)) -> Response:
    batch = ctx.current_batch(batch_id)
    if batch is None:
        resume_mod.require_batch(ctx.store, batch_id or "")
    rows = ctx.store.load_quarantine(batch["batch_id"])
    body = csv_out.render(evidence.QUARANTINE_HEADER,
                          evidence.quarantine_rows(rows))
    filename = f"quarantine-{batch['batch_id']}.csv"
    return Response(content=body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/reasons")
def api_reasons() -> dict:
    return {"reasons": reasons.catalog(),
            "usage_quant": str(config.USAGE_QUANT),
            "money_quant": str(config.MONEY_QUANT)}
