"""模板渲染与共享上下文：过滤器、导航、当前批次。

页面只读 store，不做业务计算；数字格式化统一在这里，避免每页各写一套。
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import config, reasons

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"

NAV = (
    ("/overview", "作业总览", "index"),
    ("/readings", "日值采信", "readings"),
    ("/intervals", "缺重日", "intervals"),
    ("/months", "月合计", "months"),
    ("/batches", "批次对账", "batches"),
    ("/report", "月报导出", "export"),
)

STATUS_LABELS = {
    "done": "完成", "failed": "失败", "running": "运行中", "partial": "未完成",
    "valid": "有效", "estimate": "估抄", "quarantine": "隔离",
    "normal": "常规", "rollover": "满量翻转", "replacement_first": "新表首读",
    "replacement_last": "换表末读", "ok": "就绪", "skipped": "跳过",
}
METHOD_LABELS = {
    "normal": "常规", "rollover": "满量翻转",
    "replacement_first": "新表首读", "replacement_last": "换表末读",
}


@dataclass
class Ctx:
    settings: config.Settings
    store: object

    def current_batch(self, batch_id: str | None = None) -> dict | None:
        if batch_id:
            return self.store.get_batch(batch_id)
        return self.store.latest_batch()


def get_ctx(request: Request) -> Ctx:
    return request.app.state.ctx


def setup(app) -> None:
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    env = templates.env
    env.filters["kwh"] = fmt_kwh
    env.filters["yuan"] = fmt_yuan
    env.filters["localdt"] = fmt_local_dt
    env.filters["localdate"] = fmt_local_date
    env.filters["reg"] = lambda r: config.REGISTER_LABELS.get(r, r or "—")
    env.filters["method"] = lambda m: METHOD_LABELS.get(m, m or "—")
    env.filters["code"] = reasons.label
    env.filters["status"] = lambda s: STATUS_LABELS.get(s, s or "—")
    env.filters["dec"] = fmt_dec
    env.filters["unitnum"] = fmt_unit
    env.filters["days"] = fmt_days
    env.globals.update(
        nav=NAV,
        stage_flow=config.STAGE_FLOW,
        stage_labels=config.STAGE_LABELS,
        stage_keys=config.STAGES,
        reason_catalog=reasons.catalog(),
        register_labels=config.REGISTER_LABELS,
        usage_quant="0.001",
        money_quant="0.01",
        generated_at=now_local().strftime("%Y-%m-%d %H:%M"),
        timezone_label="Asia/Shanghai +08:00",
    )
    app.state.templates = templates


def render(request: Request, name: str, **context):
    ctx: Ctx = request.app.state.ctx
    batch = context.pop("batch", None)
    if batch is None:
        batch = ctx.current_batch(context.get("batch_id"))
    stages = context.pop("stages", None)
    if stages is None and batch is not None:
        stages = ctx.store.load_stages(batch["batch_id"])
    totals = context.pop("totals", None)
    if totals is None and batch is not None:
        totals = ctx.store.load_totals(batch["batch_id"])
    base = {
        "request": request,
        "ctx": ctx,
        "settings": ctx.settings,
        "batch": batch,
        "stages": stages or [],
        "totals": totals or {},
        "page": context.pop("page", ""),
        "rule_version": ctx.settings.rule_version,
        "print_ts": now_local().strftime("%Y-%m-%d %H:%M"),
    }
    base.update(context)
    return request.app.state.templates.TemplateResponse(
        request=request, name=name, context=base)


def now_local() -> dt.datetime:
    return dt.datetime.now(config.CHINA_TZ)


def to_local(value) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.UTC)
    return value.astimezone(config.CHINA_TZ)


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def fmt_kwh(value) -> str:
    number = _dec(value)
    if number is None:
        return "—"
    return f"{number:,.3f}"


def fmt_yuan(value) -> str:
    number = _dec(value)
    if number is None:
        return "—"
    return f"{number:,.2f}"


def fmt_dec(value, places: int = 3) -> str:
    number = _dec(value)
    if number is None:
        return "—"
    return f"{number:,.{places}f}"


def fmt_unit(value, unit: str = "") -> str:
    """按单位定小数位：行取整、kWh 三位、元两位。"""
    number = _dec(value)
    if number is None:
        return "—"
    places = {"行": 0, "kWh": 3, "元": 2}.get(unit, 3)
    return f"{number:,.{places}f}"


def fmt_days(value) -> str:
    number = _dec(value)
    if number is None:
        return "—"
    text = f"{number:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def fmt_local_dt(value) -> str:
    local = to_local(value)
    return local.strftime("%Y-%m-%d %H:%M") if local else "—"


def fmt_local_date(value) -> str:
    local = to_local(value)
    return local.strftime("%Y-%m-%d") if local else "—"


def month_label(month: str) -> str:
    return f"{month[:4]}年{month[5:7]}月" if len(month) == 7 else month
