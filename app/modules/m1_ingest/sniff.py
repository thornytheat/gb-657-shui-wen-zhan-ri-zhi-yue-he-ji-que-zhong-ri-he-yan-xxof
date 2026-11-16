"""表头识别：列别名映射、重复表头分段、列数校验、末行截断判定。

表头是数据契约的第一道门：GBK 导出件用中文表头，重复导出会在中间再插一行表头，
Excel 另存会丢掉最后一行的换行符。三件事都在这里判定，不做静默修补。
"""
from __future__ import annotations

from ... import config


def normalize_cell(value: str) -> str:
    return (value or "").strip().strip("\ufeff").strip().lower()


def build_header_map(header: list[str]) -> dict[str, int]:
    """规范列名 → 列下标。别名不区分大小写，先到先得。"""
    lowered = {}
    for alias, canonical in _alias_pairs():
        lowered.setdefault(alias, canonical)
    mapping: dict[str, int] = {}
    for index, cell in enumerate(header):
        canonical = lowered.get(normalize_cell(cell))
        if canonical and canonical not in mapping:
            mapping[canonical] = index
    return mapping


def _alias_pairs():
    for canonical, aliases in config.COLUMN_ALIASES.items():
        for alias in aliases:
            yield normalize_cell(alias), canonical


def missing_columns(header_map: dict[str, int]) -> list[str]:
    return [name for name in config.REQUIRED_COLUMNS if name not in header_map]


def header_signature(header: list[str]) -> tuple[str, ...]:
    return tuple(normalize_cell(cell) for cell in header)


def is_repeated_header(fields: list[str], header: list[str]) -> bool:
    """中间重复出现的表头行：整行与首行表头逐格相同（去空白、忽略大小写）。"""
    if len(fields) != len(header):
        return False
    signature = header_signature(header)
    return header_signature(fields) == signature and bool(header)


def is_blank_row(fields: list[str]) -> bool:
    return all(not (cell or "").strip() for cell in fields)


def row_value(fields: list[str], header_map: dict[str, int], name: str) -> str:
    index = header_map.get(name)
    if index is None or index >= len(fields):
        return ""
    return (fields[index] or "").strip()


def tail_without_newline(text: str) -> bool:
    """文件末尾没有换行符：Excel 另存或传输截断的典型痕迹。"""
    return bool(text) and not text.endswith(("\n", "\r"))
