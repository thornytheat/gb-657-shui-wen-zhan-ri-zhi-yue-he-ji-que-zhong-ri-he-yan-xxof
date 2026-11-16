"""换表事件：旧表停抄、新表从零起算，换表区间必须拆成两段。

朴素实现把同一块表的上读与下读相减；换表后两个示数来自不同物理表，
相减得到负数，于是整段被丢掉或倒扣。这里把换表点显式建模。
"""
from __future__ import annotations

from . import rollover
from ..m2_contract import contract as contract_mod


def replacement_pairs(contracts: dict[str, contract_mod.MeterContract]) -> list[dict]:
    pairs = []
    for contract in contracts.values():
        if contract.replaced_by:
            pairs.append({
                "meter_id": contract.meter_id,
                "replaced_by": contract.replaced_by,
                "replaced_at": contract.replaced_at.isoformat()
                if contract.replaced_at else None,
            })
    return sorted(pairs, key=lambda item: item["meter_id"])


def predecessor_map(contracts: dict[str, contract_mod.MeterContract],
                    ) -> dict[str, contract_mod.MeterContract]:
    """新表 → 旧表契约。"""
    out: dict[str, contract_mod.MeterContract] = {}
    for contract in contracts.values():
        if not contract.replaced_by:
            continue
        if contract.replaced_by not in contracts:
            continue
        out.setdefault(contract.replaced_by, contract)
    return out


def detect_cycles(contracts: dict[str, contract_mod.MeterContract]) -> list[list[str]]:
    """换表链成环：A→B→A 会让「新表首读起算」无限追下去。"""
    cycles: list[list[str]] = []
    for start in contracts:
        seen: list[str] = []
        cursor = start
        while cursor in contracts and contracts[cursor].replaced_by:
            if cursor in seen:
                cycles.append(seen[seen.index(cursor):] + [cursor])
                break
            seen.append(cursor)
            cursor = contracts[cursor].replaced_by
    return cycles


def is_first_reading_after_swap(meter: contract_mod.MeterContract,
                                predecessor: contract_mod.MeterContract | None,
                                read_at) -> bool:
    return predecessor is not None and predecessor.replaced_at is not None


def swap_note(meter_id: str, predecessor: contract_mod.MeterContract | None) -> str:
    if predecessor is None or predecessor.replaced_at is None:
        return ""
    return f"承接 {predecessor.meter_id}，换表时点 {predecessor.replaced_at.isoformat()}"


__all__ = ["replacement_pairs", "predecessor_map", "detect_cycles",
           "is_first_reading_after_swap", "swap_note", "rollover"]
