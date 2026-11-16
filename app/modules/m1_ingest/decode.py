"""编码探测与回退链：声明只作候选，严格解码失败就往下试，并留下依据。

回退链顺序 UTF-8(BOM) → UTF-8 → GB18030：gb18030 是 gbk/gb2312 的超集，
国产抄表软件导出的文件常声明 utf-8 而字节是 GBK。
"""
from __future__ import annotations

import codecs

ENCODING_CHAIN = ("utf-8-sig", "utf-8", "gb18030")
BOM = codecs.BOM_UTF8


class DecodeError(ValueError):
    """全部候选编码都解不开。"""


def has_bom(data: bytes) -> bool:
    return data.startswith(BOM)


def decode_bytes(data: bytes) -> dict:
    """返回 {text, encoding, bom_stripped, fallback, attempts}。"""
    attempts: list[str] = []
    bom = has_bom(data)
    for index, encoding in enumerate(ENCODING_CHAIN):
        attempts.append(encoding)
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return {
            "text": text,
            # utf-8-sig 解出的文本已剥离 BOM，对外统一报 utf-8
            "encoding": "utf-8" if encoding == "utf-8-sig" else encoding,
            "bom_stripped": bom,
            "fallback": index > 0,
            "attempts": attempts,
        }
    raise DecodeError("候选编码 " + " / ".join(attempts) + " 都无法解码")
