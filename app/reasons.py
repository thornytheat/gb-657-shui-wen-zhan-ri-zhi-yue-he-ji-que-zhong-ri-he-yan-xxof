"""原因码唯一来源：错误级进隔离区，提示级留标记。

约定：
- `E_` 开头是文件级失败（整批不落数据，返回 4xx）；
- `Q_` 开头是行级隔离（好行继续，坏行带原值与行号进隔离区）；
- `K_` 开头是行级标记（不进隔离区，只在证据里留痕）。
"""
from __future__ import annotations

REASONS: dict[str, dict[str, str]] = {
    "E_EMPTY_FILE": {"label": "空文件", "level": "error"},
    "E_HEADER_ONLY": {"label": "仅表头", "level": "error"},
    "E_ALL_BLANK": {"label": "全空行", "level": "error"},
    "E_HEADER_INVALID": {"label": "表头缺列", "level": "error"},
    "E_DECODE": {"label": "编码无法识别", "level": "error"},
    "E_SAMPLE_UNKNOWN": {"label": "样例不存在", "level": "error"},
    "E_CONTRACT_INVALID": {"label": "表计契约无效", "level": "error"},
    "E_BATCH_NOT_FOUND": {"label": "批次不存在", "level": "error"},
    "Q_COLUMN_COUNT": {"label": "列数与表头不一致", "level": "error"},
    "Q_TRUNCATED_ROW": {"label": "末行被截断", "level": "error"},
    "Q_MISSING_READING": {"label": "读数为空值哨兵", "level": "error"},
    "Q_BAD_READING": {"label": "读数无法解析", "level": "error"},
    "Q_BAD_TIME": {"label": "抄表时间无法解析", "level": "error"},
    "Q_UNKNOWN_METER": {"label": "表计不在契约", "level": "error"},
    "Q_BAD_REGISTER": {"label": "寄存器不在契约", "level": "error"},
    "Q_BAD_UNIT": {"label": "单位无法识别", "level": "error"},
    "Q_BAD_READ_TYPE": {"label": "抄表类型无法识别", "level": "error"},
    "Q_NEGATIVE_READING": {"label": "读数为负数", "level": "error"},
    "Q_EXCEEDS_CAPACITY": {"label": "读数超过满量", "level": "error"},
    "Q_REVERSE_READING": {"label": "读数倒走", "level": "error"},
    "Q_DUPLICATE_CONFLICT": {"label": "同键读数不一致", "level": "error"},
    "Q_METER_RETIRED": {"label": "抄表晚于换表时点", "level": "warn"},
    "K_ROLLOVER": {"label": "满量翻转已还原", "level": "info"},
    "K_REPLACEMENT_FIRST": {"label": "新表首读起算", "level": "info"},
    "K_ESTIMATE_SKIPPED": {"label": "估抄不作锚点", "level": "info"},
    "K_CHARSET_FALLBACK": {"label": "编码回退成功", "level": "info"},
    "K_BOM_STRIPPED": {"label": "已剥离 BOM", "level": "info"},
    "K_SEGMENT_SPLIT": {"label": "重复表头分段", "level": "info"},
    "K_TRUNCATED_TAIL": {"label": "文件末行无换行", "level": "warn"},
    "K_TOU_ADJUSTED": {"label": "分量尾差并入最大分量", "level": "info"},
    "K_BILL_ROUNDED": {"label": "账单分项尾差归整", "level": "info"},
    "K_UNIT_ASSUMED": {"label": "单位按契约补齐", "level": "info"},
    "K_UNIT_CONVERTED": {"label": "单位已折算", "level": "info"},
    "K_DUPLICATE_IDENTICAL": {"label": "重复行已合并", "level": "info"},
}


def catalog() -> list[dict[str, str]]:
    return [{"code": code, **meta} for code, meta in sorted(REASONS.items())]


def label(code: str | None) -> str:
    if not code:
        return "—"
    return REASONS.get(code, {}).get("label", code)


def level(code: str | None) -> str:
    return REASONS.get(code or "", {}).get("level", "error")


def is_error(code: str | None) -> bool:
    return level(code) == "error"


def codes_at(level_name: str) -> list[str]:
    return [c for c, m in REASONS.items() if m["level"] == level_name]
