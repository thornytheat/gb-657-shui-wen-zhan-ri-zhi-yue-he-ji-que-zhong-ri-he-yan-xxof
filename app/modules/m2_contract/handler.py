"""M2 入口：看表计契约与电价，试跑一行解析。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ... import config
from ...web import Ctx, get_ctx
from . import contract, service

router = APIRouter(tags=["M2 表计契约"])


@router.get("/api/contract")
def api_contract(ctx: Ctx = Depends(get_ctx)) -> dict:
    contracts = contract.load(ctx.settings)
    prices = contract.load_prices(ctx.settings)
    return {
        "rule_version": ctx.settings.rule_version,
        "columns": {name: list(aliases) for name, aliases in config.COLUMN_ALIASES.items()},
        "required": list(config.REQUIRED_COLUMNS),
        "registers": [{"key": key, "label": config.REGISTER_LABELS[key]}
                      for key in config.REGISTERS],
        "units": {key: str(value) for key, value in config.UNIT_TO_KWH.items()},
        "meters": contract.payload(contracts),
        "swaps": [{"meter_id": item.meter_id, "replaced_by": item.replaced_by,
                   "replaced_at": item.replaced_at.isoformat()
                   if item.replaced_at else None}
                  for item in contracts.values() if item.replaced_by],
        "prices": {key: str(value) for key, value in prices.items()},
    }


@router.post("/api/contract/preview")
def api_preview(payload: dict | None = None, ctx: Ctx = Depends(get_ctx)) -> dict:
    contracts = contract.load(ctx.settings)
    return service.preview(payload or {}, contracts)
