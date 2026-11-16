"""M5 入口：从当前批次区间重算月度分摊与账单，页面按月份组展示。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...web import Ctx, get_ctx, render
from ..m6_batches import resume as resume_mod
from . import service
from .hydro import monthly, reconcile, select_daily

router = APIRouter(tags=["M5 月度切分"])


@router.post("/api/hydro/monthly")
def api_hydro(payload: dict) -> dict:
    selected = select_daily(payload.get("records") or [])
    result = monthly(selected["selected"], payload.get("expected_days") or {})
    accounts = reconcile(len(payload.get("records") or []), len(selected["selected"]),
                         len(selected["duplicates"]), len(selected["conflicts"]))
    return {"months": result, "selection": selected, "accounts": accounts}


@router.post("/api/months/allocate")
def api_allocate(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch_id = (payload or {}).get("batch_id")
    batch = resume_mod.require_batch(
        ctx.store, batch_id or (ctx.current_batch() or {}).get("batch_id", ""))
    result = service.run(ctx.settings, ctx.store, batch["batch_id"])
    body = service.payload(result["allocations"], result["bills"],
                           service.primary_map(ctx.settings))
    body["batch_id"] = result["batch_id"]
    body["stats"] = {key: str(value) if hasattr(value, "as_tuple") else value
                     for key, value in result["stats"].items()}
    return body


@router.get("/months")
def page_months(request: Request, batch_id: str | None = None,
                ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    groups: list[dict] = []
    bills: list[dict] = []
    adjustments = 0
    if batch is not None:
        allocations = ctx.store.load_allocations(batch["batch_id"])
        bills = ctx.store.load_bills(batch["batch_id"])
        groups = service.rows_by_month(allocations, service.primary_map(ctx.settings))
        adjustments = sum(1 for item in allocations if item["tou_adjusted"])
    demo_records = [
        {"station": "0012", "date": "2024-02-27", "rainfall_mm": "0.0", "version": 1},
        {"station": "0012", "date": "2024-02-28", "rainfall_mm": "12.0", "version": 1},
        {"station": "0012", "date": "2024-02-29", "rainfall_mm": "0.3", "version": 2},
        {"station": "0012", "date": "2024-02-29", "rainfall_mm": "8.0", "version": 1},
        {"station": "0031", "date": "2024-02-28", "rainfall_mm": "11.0", "version": 1},
    ]
    selected = select_daily(demo_records)
    hydro_months = monthly(selected["selected"], {"2024-02": 29})
    accounts = reconcile(len(demo_records), len(selected["selected"]),
                         len(selected["duplicates"]), len(selected["conflicts"]))
    return render(request, "months.html", page="months", page_title="月合计",
                  batch=batch, groups=groups, bills=bills, adjustments=adjustments,
                  hydro_months=hydro_months, hydro_selected=selected,
                  hydro_accounts=accounts)
