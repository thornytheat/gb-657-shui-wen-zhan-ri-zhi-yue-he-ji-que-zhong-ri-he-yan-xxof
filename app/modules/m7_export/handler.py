"""M7 入口：单一月报与运行证据导出。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from ...web import Ctx, get_ctx, render
from . import service

router = APIRouter(tags=["M7 导出"])


def _response(body: str, filename: str, media_type: str) -> Response:
    return Response(content=body, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/api/export/monthly.csv")
def export_monthly(batch_id: str | None = None, ctx: Ctx = Depends(get_ctx)) -> Response:
    body, filename = service.monthly_csv(ctx.settings, ctx.store, batch_id)
    return _response(body, filename, "text/csv; charset=utf-8")


@router.get("/report")
def page_export(request: Request, batch_id: str | None = None,
                ctx: Ctx = Depends(get_ctx)):
    batch = ctx.current_batch(batch_id)
    context = service.page_context(ctx.settings, ctx.store, batch)
    return render(request, "export.html", page="export", page_title="导出",
                  batch=batch, **context)
