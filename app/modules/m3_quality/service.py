"""质量编排：行级校验 → 同键冲突 → 序列单调，坏行进隔离区，好行继续。"""
from __future__ import annotations

from ... import reasons
from ..m2_contract import contract as contract_mod
from . import quarantine as quarantine_mod
from . import rules


def evaluate(records: list[dict],
             contracts: dict[str, contract_mod.MeterContract]) -> dict:
    quarantine: list[dict] = []
    diagnostics: list[dict] = []
    candidates: list[dict] = []

    for record in records:
        meter = contracts.get(record["meter_id"])
        if meter is None:
            quarantine.append(quarantine_mod.build_row(
                lineno=record["lineno"], segment=record["segment"],
                code="Q_UNKNOWN_METER", stage="quality", field="meter_id",
                raw_value=record["meter_id"], meter_id=record["meter_id"],
                detail="表计不在契约"))
            continue
        problem = rules.check_row(record, meter)
        if problem:
            quarantine.append(quarantine_mod.build_row(
                lineno=record["lineno"], segment=record["segment"],
                code=problem["code"], stage="quality", field=problem["field"],
                raw_value=record["reading_text"], meter_id=record["meter_id"],
                detail=problem["detail"]))
            continue
        candidates.append(record)

    kept, conflicted, merged = rules.split_conflicts(candidates)
    for item in conflicted:
        quarantine.append(quarantine_mod.build_row(
            lineno=item["lineno"], segment=item["segment"],
            code="Q_DUPLICATE_CONFLICT", stage="quality", field="reading",
            raw_value=item["reading_text"], meter_id=item["meter_id"],
            detail="同键取值不一致：" + " / ".join(item["_conflict_values"])))
    for item in merged:
        diagnostics.append({"code": "K_DUPLICATE_IDENTICAL", "lineno": item["lineno"],
                            "detail": f"与第 {item['_merged_into']} 行完全相同，已合并"})

    kept, reversed_rows = rules.monotonic_quarantine(kept, contracts)
    for item in reversed_rows:
        quarantine.append(quarantine_mod.build_row(
            lineno=item["lineno"], segment=item["segment"],
            code="Q_REVERSE_READING", stage="quality", field="reading",
            raw_value=item["reading_text"], meter_id=item["meter_id"],
            detail=(f"读数 {item['reading']} 小于第 {item['_prev_lineno']} 行的 "
                    f"{item['_prev']}，且未达满量翻转线")))

    valid = [item for item in kept if item["read_type"] == "N"]
    estimate = [item for item in kept if item["read_type"] == "E"]
    return {"valid": valid, "estimate": estimate, "quarantine": quarantine,
            "diagnostics": diagnostics}


def summary(quarantine: list[dict]) -> dict:
    return quarantine_mod.summary(quarantine)


def label(code: str) -> str:
    return reasons.label(code)
