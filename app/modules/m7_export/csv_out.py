"""CSV 输出：UTF-8 BOM + CRLF，Excel 直接打开不乱码；Decimal 用定长文本。

导出值必须与页面逐位一致：同一份 Decimal 字符串化，不在导出里再舍入一次。
"""
from __future__ import annotations

from decimal import Decimal

BOM = "\ufeff"
NEWLINE = "\r\n"


def text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def render(header: list[str], rows: list[list[str]]) -> str:
    lines = [",".join(_cell(item) for item in header)]
    for row in rows:
        lines.append(",".join(_cell(item) for item in row))
    return BOM + NEWLINE.join(lines) + NEWLINE


def _cell(value) -> str:
    content = text(value)
    if any(char in content for char in (",", '"', "\n", "\r")):
        return '"' + content.replace('"', '""') + '"'
    return content


def kwh(value: Decimal) -> str:
    return f"{value:.3f}"


def yuan(value: Decimal) -> str:
    return f"{value:.2f}"
