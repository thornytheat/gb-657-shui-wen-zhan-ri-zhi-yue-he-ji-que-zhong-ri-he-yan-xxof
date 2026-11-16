"""单元格归一：全角转半角、千分位、空值哨兵、时区偏移、抄表类型。

三态在这里分开：哨兵（`-` / `N/A` / `未知`）返回 None，真空串返回 None，
`0` 是有效读数。把哨兵当 0 参与合计是最常见的错。
"""
from __future__ import annotations

import datetime as dt
import unicodedata
from decimal import Decimal, InvalidOperation

from ... import config

REGISTER_ALIASES = {
    "total": "TOTAL", "总": "TOTAL", "有功总": "TOTAL", "总加": "TOTAL",
    "peak": "PEAK", "峰": "PEAK", "尖峰": "PEAK",
    "flat": "FLAT", "平": "FLAT", "平段": "FLAT",
    "valley": "VALLEY", "谷": "VALLEY", "低谷": "VALLEY",
}

UNIT_SUFFIXES = ("kwh", "mwh", "度", "千瓦时", "兆瓦时")


class CoerceError(ValueError):
    def __init__(self, code: str, detail: str, raw: str = ""):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.raw = raw


def normalize_text(raw: str | None) -> str:
    """NFKC 把全角数字/标点折成半角，再统一空白。"""
    text = unicodedata.normalize("NFKC", raw or "")
    return text.replace("\u3000", " ").strip()


def is_sentinel(raw: str | None) -> bool:
    text = normalize_text(raw)
    return text == "" or text.lower() in config.SENTINELS


def parse_decimal(raw: str | None) -> Decimal | None:
    """返回 Decimal 或 None（哨兵/空串）。解析不出来抛 CoerceError。"""
    text = normalize_text(raw)
    if is_sentinel(text):
        return None
    lowered = text.lower()
    for suffix in UNIT_SUFFIXES:
        if lowered.endswith(suffix):
            text = text[: -len(suffix)].strip()
            lowered = text.lower()
            break
    cleaned = text.replace(",", "").replace(" ", "")
    if cleaned in ("", "-", "+", "."):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise CoerceError("Q_BAD_READING", f"读数无法解析：{raw!r}", raw or "") from exc


def parse_register(raw: str | None) -> str:
    text = normalize_text(raw)
    if not text:
        raise CoerceError("Q_BAD_REGISTER", "寄存器为空", raw or "")
    key = REGISTER_ALIASES.get(text.lower()) or REGISTER_ALIASES.get(text)
    if key is None:
        raise CoerceError("Q_BAD_REGISTER", f"寄存器无法识别：{raw!r}", raw or "")
    return key


def parse_instant(raw: str | None) -> dt.datetime:
    """统一成 UTC：带偏移按偏移换算，纯日期/无偏移按 +08:00 当日起点。"""
    text = normalize_text(raw)
    if not text:
        raise CoerceError("Q_BAD_TIME", "抄表时间为空", raw or "")
    text = text.replace("Z", "+00:00").replace("z", "+00:00").replace("/", "-")
    try:
        value = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise CoerceError("Q_BAD_TIME", f"抄表时间无法解析：{raw!r}", raw or "") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=config.CHINA_TZ)
    return value.astimezone(config.UTC)


def parse_read_type(raw: str | None) -> str:
    text = normalize_text(raw).lower()
    if not text:
        return "N"
    kind = config.READ_TYPE_ALIASES.get(text)
    if kind is None:
        raise CoerceError("Q_BAD_READ_TYPE", f"抄表类型无法识别：{raw!r}", raw or "")
    return kind


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(config.UTC).isoformat()
