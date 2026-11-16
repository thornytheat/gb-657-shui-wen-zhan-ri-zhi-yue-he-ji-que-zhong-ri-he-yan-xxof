"""规范版本注册表：新批次按现行规范采信，旧批次按各自绑定的版本重建。

两版规范的唯一采信差异是「单位是否必须逐行明示」：
- 1.0.0 旧规范：`unit` 为空时按表计契约单位补齐，留 `K_UNIT_ASSUMED`；
- 2.0.0 新规范：`unit` 为空或哨兵时整行隔离，给 `Q_MISSING_UNIT`。

规范版本一旦写入批次就不可改：批量重算、区间重建、重试都按批次绑定的版本执行，
现行规范只影响之后新开的批次。契约（倍率、单位、换表关系）同样按批次快照读取，
不随 `meters.csv` 的后续修改而变。
"""
from __future__ import annotations

from dataclasses import dataclass

LEGACY = "hydrocalendar/1.0.0"
CURRENT = "hydrocalendar/2.0.0"


@dataclass(frozen=True)
class Spec:
    version: str
    label: str
    unit_required: bool
    note: str


SPECS: dict[str, Spec] = {
    LEGACY: Spec(
        version=LEGACY,
        label="旧规范 · 空单位按契约补齐",
        unit_required=False,
        note="空单位读数按表计契约单位补齐，留 K_UNIT_ASSUMED；只用于已绑定旧账。",
    ),
    CURRENT: Spec(
        version=CURRENT,
        label="新规范 · 逐行明示单位",
        unit_required=True,
        note="单位缺失或为哨兵的读数整行隔离，给 Q_MISSING_UNIT。",
    ),
}

DEFAULT = CURRENT


def resolve(version: str | None) -> str:
    """已知版本原样返回；空值与迁移前的老版本号一律按旧规范处理。

    老版本号（或迁移前的空绑定）说明这批账诞生在新规之前，缺单位是按契约补齐的，
    不能用现行规范去重新解释。
    """
    if version in SPECS:
        return version
    return LEGACY


def bound(version: str | None) -> Spec:
    return get(resolve(version))


class UnknownSpec(ValueError):
    def __init__(self, version: str):
        super().__init__(f"规范版本未知：{version!r}")
        self.version = version


def get(version: str | None) -> Spec:
    spec = SPECS.get(version or "")
    if spec is None:
        raise UnknownSpec(version or "")
    return spec


def require(version: str | None) -> Spec:
    return get(version)


def unit_required(version: str | None) -> bool:
    return bound(version).unit_required


def is_legacy(version: str | None) -> bool:
    return bound(version).version == LEGACY


def label(version: str | None) -> str:
    spec = SPECS.get(version or "")
    return spec.label if spec else (version or "—")


def catalog() -> list[dict]:
    return [{"version": spec.version, "label": spec.label,
             "unit_required": spec.unit_required, "note": spec.note}
            for spec in SPECS.values()]
