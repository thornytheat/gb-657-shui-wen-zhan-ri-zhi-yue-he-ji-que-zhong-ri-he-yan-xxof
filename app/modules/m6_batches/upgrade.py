"""规范升级：把旧批次从旧规范迁到现行规范，原地覆盖、可续跑、证据不足明确拒绝。

设计要点
--------
1. **绑定优先**：升级前批次按 `spec_version`（旧规范）+ 契约快照重建；升级把旧账
   的行数与合计冻结进 `from_digest`，再逐阶段用现行规范重跑。
2. **不新增批次**：升级总是作用在既有 `batch_id` 上。重试 / 续跑用同一个
   `upgrade_id`（`upg-<batch_id>-<目标版本尾号>`），不追加批次行。
3. **中断可恢复**：阶段进度写进 `spec_upgrades.phase`；所有重算都在内存完成，
   最后 `save_run` 一次事务提交。中断时旧账原样保留，再调用同一升级即从断点重跑，
   结果幂等。
4. **证据不足拒绝**：按旧规范预演采信时，出现 `Q_MISSING_UNIT` 的批次（即旧账
   本来就靠契约补齐单位但缺少可核对证据），或读数字段无法解析、契约快照缺失，
   整体拒绝迁移，批次保持旧规范不变，并记下拒绝原因。
5. **在途保护**：升级读取的是升级开始时冻结的契约与电价快照；现行规范切换只影响
   新开的批次。同一批次已有 `running` 升级时，不并发再开一条。
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from ... import config, errors, manifest, specs
from ..m1_ingest import service as m1_service
from ..m2_contract import contract as contract_mod
from ..m2_contract import service as m2_service
from ..m3_quality import service as m3_service
from ..m4_intervals import restore as restore_mod
from ..m5_months import allocate as allocate_mod
from ..m6_batches import compare, history

PHASES = ("freeze", "ingest", "contract", "quality", "restore", "allocate",
          "reconcile", "commit")


def upgrade_id_for(batch_id: str, to_version: str) -> str:
    return f"upg-{batch_id}-{to_version.rsplit('/', 1)[-1]}"


def _now() -> str:
    return history.iso(history.now_utc())


def _digest_of(payload) -> str:
    return manifest.digest_parts([json.dumps(payload, ensure_ascii=False,
                                             sort_keys=True, default=str)])


def _item(stage: str, status: str, code: str | None = None,
          detail: str = "") -> dict:
    return {"stage": stage, "status": status, "code": code, "detail": detail}


def _new_upgrade(batch: dict, from_version: str, to_version: str) -> dict:
    return {"upgrade_id": upgrade_id_for(batch["batch_id"], to_version),
            "batch_id": batch["batch_id"], "from_version": from_version,
            "to_version": to_version, "status": "running", "phase": "freeze",
            "started_at": _now(), "finished_at": None,
            "from_digest": "", "to_digest": None, "note": "", "detail": {}}


def evidence_gaps(record: dict) -> list[str]:
    """旧账迁到新规范的底线证据：单位缺失的行必须能查到唯一契约单位。

    `reading`、`unit_norm` 为空说明这一行在旧规范下也没算出来，迁移会凭空多出
    一段用量或金额，宁可拒绝也不猜。读数文本自带单位后缀（如 `12.5MWh`）时，
    这个后缀会被数字解析静默剥掉，属于原始证据被覆盖，同样拒绝。
    """
    gaps: list[str] = []
    unit_raw = (record.get("unit_raw") or "").strip()
    if unit_raw:
        if not record.get("unit_norm"):
            gaps.append("契约单位缺失")
        return gaps
    text = (record.get("reading_text") or "").strip()
    if not text:
        gaps.append("原始读数缺失")
    elif any(text.lower().endswith(suffix) for suffix in
             ("kwh", "mwh", "度", "千瓦时", "兆瓦时")):
        gaps.append("读数文本自带单位而单位列留空，原值不可唯一解释")
    if not record.get("unit_norm"):
        gaps.append("单位缺失且无契约单位可核对")
    if record.get("reading") is None:
        gaps.append("折合读数缺失")
    return gaps


def _dry_run(settings, store, batch: dict, spec) -> dict:
    """只读预演：旧规范 + 快照契约重算一遍，只判断证据是否够迁移。

    预演用「固化空单位」模式跑现行规范。判断标准是**旧账已采信的行**能否原样
    迁过去：两版规范都隔离的行（如单位写成 `GJ`）不影响迁移，不构成拒绝理由。
    迁不过去的旧账行会落在 `Q_MISSING_UNIT` 或证据缺口里，这些就是拒绝理由。
    """
    name = batch["sample"]
    path = Path(settings.sample_dir) / name
    if not path.is_file():
        return {"ok": False, "reason": f"原始样例缺失：{name}", "gaps": []}
    payload = path.read_bytes()
    recorded = store.load_manifest(batch["batch_id"]) or {}
    inputs = {item.get("name"): item.get("sha256") for item in recorded.get("inputs") or []}
    known = inputs.get(name)
    if known and known != manifest.digest_bytes(payload):
        return {"ok": False, "reason": "原始样例与批次输入摘要不一致，拒绝按新规范重建",
                "gaps": [{"lineno": 0, "code": "E_INPUT_CHANGED",
                          "detail": f"{name} 的 sha256 与运行清单不符"}]}
    try:
        ingested = m1_service.ingest(settings, name, data=payload)
    except errors.MeterbackError as exc:
        return {"ok": False, "reason": f"原始样例无法采信：{exc.code}", "gaps": []}
    contracts = contract_mod.from_meter_rows(store.load_meters(batch["batch_id"]))
    if not contracts:
        return {"ok": False, "reason": "批次没有契约快照，无法核对旧单位",
                "gaps": [{"lineno": 0, "code": "E_CONTRACT_INVALID",
                          "detail": "契约快照缺失"}]}

    legacy_records, legacy_q, _ = m2_service.interpret_rows(
        ingested.rows, contracts, spec.version)
    target_records, target_q, _ = m2_service.interpret_rows(
        ingested.rows, contracts, specs.CURRENT, materialize_missing_unit=True)
    accepted_legacy = {record["lineno"] for record in legacy_records}
    accepted_target = {record["lineno"] for record in target_records}
    lost = sorted(accepted_legacy - accepted_target)

    gaps = [{"lineno": record["lineno"], "code": "K_UNIT_EVIDENCE", "detail": gap}
            for record in legacy_records for gap in evidence_gaps(record)]
    gaps += [{"lineno": item["lineno"], "code": item["code"],
              "detail": item.get("detail") or ""}
             for item in target_q
             if item["code"] in ("Q_MISSING_UNIT", "Q_BAD_UNIT")
             and item["lineno"] in lost]
    materialized = sum(1 for record in target_records
                       if record.get("unit_materialized"))
    blank_rows = sum(1 for record in legacy_records
                     if not (record.get("unit_raw") or "").strip())
    reason = "" if not gaps else "旧账单位证据不足，拒绝按现行规范重建"
    return {"ok": not gaps, "reason": reason, "gaps": gaps,
            "materialized": materialized, "blank_unit_rows": blank_rows,
            "legacy_quarantine": len(legacy_q), "target_quarantine": len(target_q)}


def plan(settings, store, batch_id: str, to_version: str | None = None) -> dict:
    """升级前诊断：能不能升、会拒绝在哪一行、目标版本是什么。"""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_BATCH_NOT_FOUND")
    target = specs.get(to_version or settings.rule_version)
    current = specs.bound(batch.get("spec_version"))
    result = {"batch_id": batch_id, "from_version": current.version,
              "to_version": target.version, "to_label": target.label,
              "can_upgrade": False, "reason": "", "gaps": []}
    if current.version == target.version:
        result["reason"] = "批次已绑定现行规范，无需升级"
        return result
    dry = _dry_run(settings, store, batch, current)
    result.update(can_upgrade=dry["ok"], reason=dry["reason"], gaps=dry["gaps"],
                  materialized=dry.get("materialized", 0),
                  blank_unit_rows=dry.get("blank_unit_rows", 0))
    return result


def upgrade(settings, store, batch_id: str, to_version: str | None = None,
            *, resume: bool = True) -> dict:
    """把既有批次迁到目标规范；同一批次同一目标只保留一条升级记录。"""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_BATCH_NOT_FOUND")
    target = specs.get(to_version or settings.rule_version)
    current = specs.bound(batch.get("spec_version"))
    uid = upgrade_id_for(batch_id, target.version)
    existing = store.get_upgrade(uid)
    base = {"upgrade_id": uid, "batch_id": batch_id,
            "from_version": current.version, "to_version": target.version}

    if existing is not None and existing["status"] == "done":
        return {**base, "status": "done", "idempotent_replay": True,
                "items": store.load_upgrade_items(uid)}
    if current.version == target.version:
        return {**base, "status": "noop", "idempotent_replay": True, "items": []}
    if existing is not None and existing["status"] == "refused" and not resume:
        return {**base, "status": "refused", "reason": existing["note"],
                "unchanged": True, "items": store.load_upgrade_items(uid)}
    if existing is not None and existing["status"] == "running" and not resume:
        raise errors.Conflict(f"升级 {uid} 正在进行", code="E_UPGRADE_IN_PROGRESS")

    dry = _dry_run(settings, store, batch, current)
    record = existing or _new_upgrade(batch, current.version, target.version)
    if not dry["ok"]:
        items = [_item("contract", "refused", "K_SPEC_UPGRADE_REFUSED", dry["reason"])]
        items += [_item("evidence", "refused", gap["code"], gap["detail"])
                  for gap in dry["gaps"]]
        return _refuse(store, record, base, dry["reason"], items)

    resumed = existing is not None and existing["status"] == "running"
    if existing is None:
        # 首次升级：原子占位。并发调用里只有一个能拿到 running，其余走续跑分支。
        record["from_digest"] = _digest_of(_snapshot(store, batch_id))
        if not store.claim_upgrade(record):
            existing = store.get_upgrade(uid)
            if existing is not None and existing["status"] == "done":
                return {**base, "status": "done", "idempotent_replay": True,
                        "items": store.load_upgrade_items(uid)}
            if existing is not None:
                record = existing
            resumed = True
    record.update(status="running", phase="freeze", finished_at=None, note="",
                  from_digest=record["from_digest"], to_digest=None)
    if not resumed:
        record["started_at"] = _now()
    store.save_upgrade(record)
    items: list[dict] = [_item("freeze", "ok", None,
                               f"冻结旧账 {record['from_digest'][:12]}"
                               + ("（续跑沿用）" if resumed else ""))]

    # ---- 从断点续跑：所有阶段只读冻结的输入与快照 ----
    name = batch["sample"]
    payload = (Path(settings.sample_dir) / name).read_bytes()
    ingested = m1_service.ingest(settings, name, data=payload)
    contracts = contract_mod.from_meter_rows(store.load_meters(batch_id))
    prices = store.load_prices(batch_id) or contract_mod.load_prices(settings)
    record["phase"] = "contract"
    store.save_upgrade(record)

    records, contract_q, _ = m2_service.interpret_rows(
        ingested.rows, contracts, target.version, materialize_missing_unit=True)
    missing = [item for item in contract_q if item["code"] == "Q_MISSING_UNIT"]
    if missing:
        items.append(_item("contract", "refused", "K_SPEC_UPGRADE_REFUSED",
                           "升级时发现单位证据缺失"))
        return _refuse(store, record, base, "升级时发现单位证据缺失", items)
    materialized = sum(1 for record_ in records
                       if record_.get("unit_materialized"))
    items.append(_item("contract", "ok", "K_UNIT_MATERIALIZED" if materialized else None,
                       f"现行规范采信 {len(records)} 行，固化空单位 {materialized} 行"))
    record["phase"] = "quality"
    store.save_upgrade(record)

    quality = m3_service.evaluate(records, contracts)
    items.append(_item("quality", "ok", None,
                       f"有效 {len(quality['valid'])} / 估抄 {len(quality['estimate'])}"))
    record["phase"] = "restore"
    store.save_upgrade(record)

    intervals = restore_mod.build_intervals(quality["valid"], quality["estimate"],
                                            contracts)
    stats = restore_mod.totals(intervals, contracts)
    items.append(_item("restore", "ok", None, f"{stats['primary_usage_kwh']} kWh"))
    record["phase"] = "allocate"
    store.save_upgrade(record)

    allocated = allocate_mod.allocate(intervals, contracts, prices)
    items.append(_item("allocate", "ok", None,
                       f"{allocated['stats']['month_count']} 个月"))
    record["phase"] = "reconcile"
    store.save_upgrade(record)

    quarantine = ingested.quarantine + contract_q + quality["quarantine"]
    month_usage = allocated["stats"]["usage_total"]
    bill_total = sum((bill["amount_yuan"] for bill in allocated["bills"]), Decimal("0"))
    component_total = sum(
        (sum((Decimal(item["amount"]) for item in bill["components"]), Decimal("0"))
         for bill in allocated["bills"]), Decimal("0"))
    reconcile = compare.build(
        raw_lines=ingested.data_lines, valid=len(quality["valid"]),
        estimate=len(quality["estimate"]), quarantine=len(quarantine),
        blank=ingested.blank_lines,
        interval_usage=stats["primary_usage_kwh"], month_usage=month_usage,
        tou_before=allocated["stats"]["tou_residual_before"],
        tou_after=allocated["stats"]["tou_residual_after"],
        tou_adjustments=allocated["stats"]["tou_adjustments"],
        bill_total=bill_total, component_total=component_total,
        bill_count=len(allocated["bills"]))
    if not reconcile["all_balanced"]:
        items.append(_item("reconcile", "refused", "K_SPEC_UPGRADE_REFUSED",
                           "新规范下四账不平，拒绝覆盖旧账"))
        return _refuse(store, record, base, "新规范下四账不平", items)
    items.append(_item("reconcile", "ok", None, "四账平衡"))
    record["phase"] = "commit"
    store.save_upgrade(record)

    _commit(settings, store, batch, ingested, contracts, prices, quality, intervals,
            allocated, quarantine, reconcile, stats, month_usage, bill_total,
            record, target)
    items.append(_item("commit", "ok", "K_SPEC_UPGRADED",
                       f"已就地升级到 {target.version}"))
    store.replace_upgrade_items(uid, items)
    record.update(status="done", phase="commit", finished_at=_now(),
                  to_digest=_digest_of(_snapshot(store, batch_id)), note="")
    store.save_upgrade(record)
    return {**base, "status": "done", "resumed": resumed, "items": items}


def _refuse(store, record: dict, base: dict, reason: str, items: list[dict]) -> dict:
    store.replace_upgrade_items(record["upgrade_id"], items)
    record.update(status="refused", phase="contract", finished_at=_now(),
                  note=reason, detail={"reason": reason})
    store.save_upgrade(record)
    return {**base, "status": "refused", "reason": reason, "unchanged": True,
            "items": items}


def _snapshot(store, batch_id: str) -> dict:
    return {"batch_id": batch_id, "counts": store.count_rows(batch_id),
            "totals": store.load_totals(batch_id)}


def _commit(settings, store, batch, ingested, contracts, prices, quality, intervals,
            allocated, quarantine, reconcile, stats, month_usage, bill_total,
            record, target) -> None:
    """复用主流程的落库形状；manifest 里的规范版本换成升级后的目标版本。"""
    finished_at = _now()
    totals = {
        "raw_lines": ingested.data_lines,
        "valid": len(quality["valid"]),
        "estimate": len(quality["estimate"]),
        "quarantine": len(quarantine),
        "blank": ingested.blank_lines,
        "interval_usage": stats["primary_usage_kwh"],
        "month_usage": month_usage,
        "bill_amount": bill_total.quantize(config.MONEY_QUANT),
        "interval_count": len(intervals),
        "allocation_count": len(allocated["allocations"]),
        "bill_count": len(allocated["bills"]),
        "month_count": allocated["stats"]["month_count"],
        "meter_count": len(contracts),
        "tou_adjustments": allocated["stats"]["tou_adjustments"],
        "segments": ingested.segments,
        "encoding": ingested.encoding,
        "balanced": "是" if reconcile["all_balanced"] else "否",
        "reconcile_json": json.dumps(reconcile, ensure_ascii=False, default=str),
        "spec_version": target.version,
    }
    stages = [{"stage": key, "in_count": 0, "out_count": 0, "quarantine_count": 0,
               "status": "ok", "elapsed_ms": 1,
               "note": f"{target.version} 升级"} for key in config.STAGES]
    manifest_json = manifest.build(
        run_id=batch["run_id"], batch_id=batch["batch_id"],
        batch_key_value=batch["batch_key"], settings=settings, sample=batch["sample"],
        digest=batch["input_digest"], rule_version=target.version,
        inputs=[{"name": batch["sample"], "sha256": ingested.digest,
                 "bytes": ingested.byte_len, "encoding": ingested.encoding},
                {"name": config.METERS_FILE, "sha256": "snapshot",
                 "bytes": 0, "encoding": "utf-8"},
                {"name": config.PRICES_FILE, "sha256": "snapshot",
                 "bytes": 0, "encoding": "utf-8"}],
        params={"sample": batch["sample"], "mode": "upgrade",
                "rule_version": target.version, "spec_version": target.version,
                "upgrade_id": record["upgrade_id"],
                "from_version": record["from_version"]},
        started_at=record["started_at"], finished_at=finished_at, elapsed_ms=0,
        stages=stages, counters=history.counters(
            valid=len(quality["valid"]), estimate=len(quality["estimate"]),
            quarantine=len(quarantine), blank=ingested.blank_lines,
            intervals=len(intervals), allocations=len(allocated["allocations"]),
            bills=len(allocated["bills"])),
        reconcile=reconcile)
    readings_rows = [{
        "lineno": item["lineno"], "segment": item["segment"],
        "meter_id": item["meter_id"], "register": item["register"],
        "read_at": item["read_at"], "reading_text": item["reading_text"],
        "unit_raw": item["unit_raw"], "reading_norm": item["reading"],
        "unit_norm": item["unit_norm"], "read_type": item["read_type"],
        "status": "estimate" if item["read_type"] == "E" else "valid",
        "code": None, "flags": item.get("flags", []), "note": item.get("note", ""),
    } for item in (quality["valid"] + quality["estimate"])]
    store.save_run(
        batch={**batch, "status": "done", "finished_at": finished_at,
               "updated_at": finished_at, "rule_version": target.version,
               "spec_version": target.version,
               "params": {**(batch.get("params") or {}),
                          "spec_version": target.version,
                          "upgraded_from": record["from_version"]}},
        meters=contract_mod.meter_rows(contracts),
        readings=readings_rows,
        intervals=intervals,
        allocations=allocated["allocations"],
        bills=allocated["bills"],
        quarantine=quarantine,
        stages=stages,
        totals=totals,
        manifest_json=manifest_json,
        prices=prices)


def resume_pending(settings, store, batch_id: str) -> dict:
    """续跑：同一 upgrade_id 从断点继续；没有未完成记录时返回当前状态。"""
    batch = store.get_batch(batch_id)
    if batch is None:
        raise errors.NotFound(f"批次 {batch_id} 不存在", code="E_BATCH_NOT_FOUND")
    pending = [u for u in store.list_upgrades(batch_id) if u["status"] == "running"]
    if not pending:
        return {"batch_id": batch_id, "status": "idle", "resumed": False,
                "spec_version": specs.bound(batch.get("spec_version")).version}
    return upgrade(settings, store, batch_id, pending[0]["to_version"], resume=True)


def status(store, batch_id: str | None = None) -> dict:
    batch = store.get_batch(batch_id) if batch_id else (store.latest_batch() or {})
    if not batch:
        return {"batch_id": None, "spec_version": None, "spec_label": None,
                "upgrades": []}
    def _items(record: dict) -> list[dict]:
        items = store.load_upgrade_items(record["upgrade_id"])
        if record["status"] == "running":
            items = items + [_item("resume", "pending", "K_SPEC_UPGRADE_PARTIAL",
                                   f"停在 {record['phase']}，可续跑")]
        return items

    return {"batch_id": batch["batch_id"],
            "spec_version": specs.bound(batch.get("spec_version")).version,
            "spec_label": specs.label(batch.get("spec_version")),
            "upgrades": [{
                "upgrade_id": u["upgrade_id"], "from_version": u["from_version"],
                "to_version": u["to_version"], "status": u["status"],
                "phase": u["phase"], "started_at": u["started_at"],
                "finished_at": u["finished_at"], "note": u["note"],
                "items": _items(u),
            } for u in store.list_upgrades(batch["batch_id"])]}


__all__ = ["plan", "upgrade", "resume_pending", "status", "evidence_gaps",
           "upgrade_id_for", "PHASES"]
