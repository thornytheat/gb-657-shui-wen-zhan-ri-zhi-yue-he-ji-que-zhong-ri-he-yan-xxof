"""契约解释：单元格文本 → 带单位的读数记录，逐行给出原因码。

读数一律折成契约单位后再交给下游，换算系数只在 units.py 里定义一次。
"""
from __future__ import annotations

from decimal import Decimal

from ... import config, reasons
from . import coerce, contract, units


def interpret_rows(rows: list[dict], contracts: dict[str, contract.MeterContract],
                   ) -> tuple[list[dict], list[dict], list[dict]]:
    records: list[dict] = []
    quarantine: list[dict] = []
    diagnostics: list[dict] = []
    for row in rows:
        cells = row["cells"]
        meter_id = coerce.normalize_text(cells.get("meter_id"))
        found: contract.MeterContract | None = contracts.get(meter_id)
        if found is None:
            quarantine.append(_q(row, "Q_UNKNOWN_METER",
                                 f"表号 {meter_id!r} 不在表计契约", meter_id,
                                 "meter_id"))
            continue
        try:
            register = coerce.parse_register(cells.get("register"))
        except coerce.CoerceError as exc:
            quarantine.append(_q(row, exc.code, exc.detail, meter_id, "register"))
            continue
        if register not in found.registers:
            quarantine.append(_q(row, "Q_BAD_REGISTER",
                                 f"表 {meter_id} 契约里没有 {register}", meter_id,
                                 "register"))
            continue
        try:
            read_at = coerce.parse_instant(cells.get("read_at"))
        except coerce.CoerceError as exc:
            quarantine.append(_q(row, exc.code, exc.detail, meter_id, "read_at"))
            continue
        raw_reading = cells.get("reading", "")
        try:
            reading = coerce.parse_decimal(raw_reading)
        except coerce.CoerceError as exc:
            quarantine.append(_q(row, exc.code, exc.detail, meter_id, "reading"))
            continue
        if reading is None:
            quarantine.append(_q(row, "Q_MISSING_READING",
                                 f"读数是空值哨兵 {raw_reading!r}", meter_id, "reading"))
            continue
        unit_raw = coerce.normalize_text(cells.get("unit"))
        flags: list[str] = []
        if unit_raw:
            canonical = units.canonical_unit(unit_raw)
            if canonical is None:
                quarantine.append(_q(row, "Q_BAD_UNIT",
                                     f"单位无法识别 {unit_raw!r}", meter_id, "unit"))
                continue
            try:
                reading = units.convert(reading, canonical, found.unit)
            except units.UnknownUnit:
                quarantine.append(_q(row, "Q_BAD_UNIT",
                                     f"单位无法识别 {unit_raw!r}", meter_id, "unit"))
                continue
            if canonical != found.unit:
                flags.append("K_UNIT_CONVERTED")
        else:
            flags.append("K_UNIT_ASSUMED")
        try:
            read_type = coerce.parse_read_type(cells.get("read_type"))
        except coerce.CoerceError as exc:
            quarantine.append(_q(row, exc.code, exc.detail, meter_id, "read_type"))
            continue
        records.append({
            "lineno": row["lineno"],
            "segment": row["segment"],
            "meter_id": meter_id,
            "register": register,
            "read_at": coerce.iso_utc(read_at),
            "reading": reading,
            "reading_text": (raw_reading or "").strip(),
            "unit_raw": unit_raw,
            "unit_norm": found.unit,
            "read_type": read_type,
            "raw": row.get("raw", ""),
            "note": coerce.normalize_text(cells.get("note")),
            "flags": flags,
        })
    return records, quarantine, diagnostics


def _q(row: dict, code: str, detail: str, meter_id: str, field: str) -> dict:
    return {"lineno": row["lineno"], "segment": row["segment"], "code": code,
            "stage": "contract", "level": reasons.level(code), "field": field,
            "raw_value": row["cells"].get(field, ""), "meter_id": meter_id,
            "detail": detail}


def preview(cells: dict[str, str], contracts: dict[str, contract.MeterContract]) -> dict:
    """单行试解析：接口用来给使用者看「这行会被读成什么」。"""
    out: dict = {"input": dict(cells), "ok": True, "code": None, "detail": ""}
    meter_id = coerce.normalize_text(cells.get("meter_id"))
    found = contracts.get(meter_id)
    if found is None:
        out.update(ok=False, code="Q_UNKNOWN_METER", detail=f"表号 {meter_id!r} 不在契约")
        return out
    try:
        register = coerce.parse_register(cells.get("register"))
        read_at = coerce.parse_instant(cells.get("read_at"))
        reading = coerce.parse_decimal(cells.get("reading"))
        read_type = coerce.parse_read_type(cells.get("read_type"))
        unit_raw = coerce.normalize_text(cells.get("unit"))
        canonical = units.canonical_unit(unit_raw) if unit_raw else found.unit
        if canonical is None:
            raise coerce.CoerceError("Q_BAD_UNIT", f"单位无法识别 {unit_raw!r}")
        value = None if reading is None else units.convert(reading, canonical, found.unit)
    except coerce.CoerceError as exc:
        out.update(ok=False, code=exc.code, detail=exc.detail)
        return out
    out.update({
        "meter_id": meter_id,
        "register": register,
        "read_at": read_at.isoformat(),
        "reading_text": coerce.normalize_text(cells.get("reading")),
        "reading": None if value is None else str(value),
        "unit": found.unit,
        "read_type": read_type,
        "is_estimate": read_type == "E",
        "capacity": str(found.capacity),
        "multiplier": str(found.multiplier),
        "register_label": config.REGISTER_LABELS.get(register, register),
    })
    if reading is None:
        out.update(ok=False, code="Q_MISSING_READING", detail="读数是空值哨兵")
    return out


def usage_quantize(value: Decimal) -> Decimal:
    return value.quantize(config.USAGE_QUANT)
