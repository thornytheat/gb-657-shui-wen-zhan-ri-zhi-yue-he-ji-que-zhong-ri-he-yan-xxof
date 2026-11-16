"""M4 入口：从当前批次重算区间，页面展示判定方法与标记。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ... import config
from ...web import Ctx, get_ctx, render
from ..m6_batches import resume as resume_mod
from . import service

router = APIRouter(tags=["M4 区间还原"])


@router.post("/api/intervals/rebuild")
def api_rebuild(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    batch_id = (payload or {}).get("batch_id")
    batch = resume_mod.require_batch(
        ctx.store, batch_id or (ctx.current_batch() or {}).get("batch_id", ""))
    result = service.rebuild(ctx.settings, ctx.store, batch["batch_id"])
    return {
        "batch_id": result["batch_id"],
        "intervals": service.payload(result["intervals"]),
        "totals": {key: str(value) for key, value in result["totals"].items()},
        "method_counts": result["method_counts"],
        "swaps": result["swaps"],
    }


@router.get("/intervals")
def page_intervals(request: Request, batch_id: str | None = None,
                   ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    intervals: list[dict] = []
    method_counts: dict = {}
    swaps: list[dict] = []
    if batch is not None:
        intervals = sorted(
            ctx.store.load_intervals(batch["batch_id"]),
            key=lambda item: (item["meter_id"],
                              config.REGISTERS.index(item["register"])
                              if item["register"] in config.REGISTERS else 9,
                              item["start_at"]))
        for item in intervals:
            method_counts[item["method"]] = method_counts.get(item["method"], 0) + 1
        swaps = service.swaps_payload(ctx.settings)
    return render(request, "intervals.html", page="intervals", page_title="区间还原",
                  batch=batch, intervals=intervals, method_counts=method_counts,
                  swaps=swaps, flagged=service.flagged(intervals))
