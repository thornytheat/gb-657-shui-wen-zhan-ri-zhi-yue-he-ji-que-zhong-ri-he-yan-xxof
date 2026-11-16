"""采信编排：字节 → 文本 → 记录 → 单元格，逐行保留原始行号与分段号。

空态三态（0 字节 / 仅表头 / 全空行）在这里各给一个不同的文件级原因码，
不返回「成功导入 0 行」。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ... import config, errors, reasons
from . import decode, reader, sniff


@dataclass
class IngestResult:
    sample: str
    encoding: str
    bom_stripped: bool
    fallback: bool
    segments: int
    header: list[str]
    header_map: dict[str, int]
    rows: list[dict] = field(default_factory=list)
    quarantine: list[dict] = field(default_factory=list)
    blank_lines: int = 0
    data_lines: int = 0
    diagnostics: list[dict] = field(default_factory=list)
    digest: str = ""
    byte_len: int = 0
    tail_without_newline: bool = False

    @property
    def raw_lines(self) -> int:
        return self.data_lines


def sample_path(settings, name: str) -> Path:
    if name not in config.SAMPLES:
        raise errors.BadRequest(
            f"样例 {name!r} 不在白名单内", code="E_SAMPLE_UNKNOWN")
    path = Path(settings.sample_dir) / name
    if not path.is_file():
        raise errors.NotFound(f"样例文件缺失：{name}", code="E_SAMPLE_UNKNOWN")
    return path


def available_samples(settings) -> list[dict]:
    out = []
    for name, label in config.SAMPLES.items():
        out.append({"name": name, "label": label,
                    "exists": (Path(settings.sample_dir) / name).is_file()})
    return out


def ingest(settings, name: str, *, data: bytes | None = None) -> IngestResult:
    path = sample_path(settings, name)
    payload = path.read_bytes() if data is None else data
    if not payload.strip():
        raise errors.UnprocessableInput("文件是 0 字节", code="E_EMPTY_FILE")
    try:
        decoded = decode.decode_bytes(payload)
    except decode.DecodeError as exc:
        raise errors.UnprocessableInput(str(exc), code="E_DECODE") from exc
    text = decoded["text"]
    records = reader.read_records(text)
    if not records:
        raise errors.UnprocessableInput("文件没有任何记录", code="E_EMPTY_FILE")

    header = records[0].fields
    header_map = sniff.build_header_map(header)
    missing = sniff.missing_columns(header_map)
    if missing:
        raise errors.UnprocessableInput(
            "表头缺少必需列：" + "、".join(missing), code="E_HEADER_INVALID")

    result = IngestResult(
        sample=name,
        encoding=decoded["encoding"],
        bom_stripped=decoded["bom_stripped"],
        fallback=decoded["fallback"],
        segments=1,
        header=header,
        header_map=header_map,
        digest=hashlib.sha256(payload).hexdigest(),
        byte_len=len(payload),
        tail_without_newline=sniff.tail_without_newline(text),
    )
    if decoded["fallback"]:
        result.diagnostics.append({"code": "K_CHARSET_FALLBACK",
                                   "detail": "按 GB18030 回退解码"})
    if result.bom_stripped:
        result.diagnostics.append({"code": "K_BOM_STRIPPED", "detail": "首列 BOM 已剥离"})
    if result.tail_without_newline:
        result.diagnostics.append({"code": "K_TRUNCATED_TAIL",
                                   "detail": "文件末尾没有换行符"})

    data_lines = 0
    for record in records[1:]:
        fields = record.fields
        if sniff.is_repeated_header(fields, header):
            result.segments += 1
            result.diagnostics.append({
                "code": "K_SEGMENT_SPLIT",
                "detail": f"第 {record.lineno} 行重复表头，计入第 {result.segments} 段",
            })
            continue
        if sniff.is_blank_row(fields):
            result.blank_lines += 1
            continue
        data_lines += 1
        if len(fields) != len(header):
            is_tail = (result.tail_without_newline
                       and record is records[-1])
            code = "Q_TRUNCATED_ROW" if is_tail else "Q_COLUMN_COUNT"
            result.quarantine.append({
                "lineno": record.lineno, "segment": result.segments, "code": code,
                "stage": "ingest", "level": reasons.level(code),
                "field": "列数", "raw_value": record.raw,
                "meter_id": sniff.row_value(fields, header_map, "meter_id"),
                "detail": f"期望 {len(header)} 列，实际 {len(fields)} 列",
            })
            continue
        result.rows.append({
            "lineno": record.lineno,
            "segment": result.segments,
            "cells": {name_: sniff.row_value(fields, header_map, name_)
                      for name_ in header_map},
            "raw": record.raw,
        })
    result.data_lines = data_lines
    if not result.rows:
        if result.blank_lines:
            raise errors.UnprocessableInput("数据行全部为空", code="E_ALL_BLANK")
        raise errors.UnprocessableInput("文件只有表头，没有数据行", code="E_HEADER_ONLY")
    return result


def stage_row(*, stage: str, in_count: int, out_count: int, quarantine_count: int,
              status: str, elapsed_ms: int, note: str = "") -> dict:
    return {"stage": stage, "in_count": in_count, "out_count": out_count,
            "quarantine_count": quarantine_count, "status": status,
            "elapsed_ms": elapsed_ms, "note": note}
