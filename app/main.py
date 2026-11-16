"""应用装配：配置 → 存储 → 模板 → 七个功能模块路由。

用工厂函数而不是模块级 app，测试可以给每个用例一个临时库。
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, errors, web
from .store import Store
from .modules.m1_ingest import handler as m1_handler
from .modules.m2_contract import handler as m2_handler
from .modules.m3_quality import handler as m3_handler
from .modules.m4_intervals import handler as m4_handler
from .modules.m5_months import handler as m5_handler
from .modules.m6_batches import handler as m6_handler
from .modules.m7_export import handler as m7_handler

MODULES = (m1_handler, m2_handler, m3_handler, m4_handler, m5_handler,
           m6_handler, m7_handler)

PAGES = ("/", "/readings", "/intervals", "/months", "/batches", "/export")


def create_app(settings: config.Settings | None = None) -> FastAPI:
    settings = settings or config.Settings.from_env()
    app = FastAPI(
        title="hydrocalendar 水文站日值月合计缺重日核验",
        description="日值采信 → 站点日历 → 缺重日 → 月量计算 → 跨站复核 → 批次导出",
        version=settings.rule_version,
    )
    app.state.settings = settings
    app.state.store = Store(settings.db_path)
    app.state.ctx = web.Ctx(settings=settings, store=app.state.store)
    web.setup(app)
    app.mount("/static", StaticFiles(directory=str(web.STATIC_DIR)), name="static")
    for module in MODULES:
        app.include_router(module.router)
    errors.install_handlers(app)

    @app.get("/")
    def home():
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/overview", status_code=307)

    @app.get("/overview")
    def overview(request: Request, batch_id: str | None = None):
        return m6_handler.page_index(request, batch_id, web.get_ctx(request))

    def _head_ok() -> Response:
        return Response(status_code=200, media_type="text/html; charset=utf-8")

    for path in PAGES + ("/api/health",):
        app.add_api_route(path, _head_ok, methods=["HEAD"], include_in_schema=False)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):  # noqa: ANN001
        return JSONResponse(status_code=400,
                            content={"error": "E_BAD_REQUEST", "message": str(exc)})

    return app
