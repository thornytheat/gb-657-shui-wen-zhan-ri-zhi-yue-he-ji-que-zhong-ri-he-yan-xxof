"""hydrocalendar 全局配置：单位换算、空值哨兵、阶段、Decimal 刻度。

规则唯一来源，其他模块只读：
- 用量 Decimal，单位 kWh，最小刻度 0.001；
- 金额 Decimal，单位元，最小刻度 0.01；
- 时间入库 UTC，月桶与展示按 Asia/Shanghai（+08:00）；
- 月桶左闭右开 [月初 00:00, 下月初 00:00)。
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHINA_TZ = dt.timezone(dt.timedelta(hours=8), "Asia/Shanghai")
UTC = dt.timezone.utc

RULE_VERSION = "hydrocalendar/1.0.0"

USAGE_QUANT = Decimal("0.001")
MONEY_QUANT = Decimal("0.01")
# 满量翻转判定：上一条读数需达到满量的这个比例，否则按倒走隔离
ROLLOVER_MIN_RATIO = Decimal("0.9")

REGISTERS = ("TOTAL", "PEAK", "FLAT", "VALLEY")
REGISTER_LABELS = {"TOTAL": "总", "PEAK": "峰", "FLAT": "平", "VALLEY": "谷"}
TOU_REGISTERS = ("PEAK", "FLAT", "VALLEY")

# 单位 → kWh 换算系数（读数先归一到契约单位，用量再折 kWh）
UNIT_TO_KWH = {
    "kwh": Decimal("1"), "度": Decimal("1"), "千瓦时": Decimal("1"),
    "mwh": Decimal("1000"), "兆瓦时": Decimal("1000"),
}

# 空值哨兵：集中定义；None / 0 / 空串三态分开
SENTINELS = frozenset(
    {"", "-", "--", "—", "——", "/", "n/a", "na", "null", "none", "nil",
     "未知", "无", "暂无", "待定", "空", "未抄"}
)

READ_TYPE_ALIASES = {
    "n": "N", "正常": "N", "实抄": "N",
    "e": "E", "估抄": "E", "估算": "E",
}

STAGES = ("ingest", "contract", "quality", "restore", "allocate", "reconcile", "export")
STAGE_LABELS = {
    "ingest": "采信",
    "contract": "契约",
    "quality": "质量",
    "restore": "还原",
    "allocate": "切分",
    "reconcile": "对账",
    "export": "导出",
}
STAGE_FLOW = "采信 → 契约 → 质量 → 还原 → 切分 → 对账 → 导出"

# 列契约：规范列名 → 可接受的别名（GBK 导出的表头是中文）
COLUMN_ALIASES = {
    "meter_id": ("meter_id", "表号", "电能表", "电表编号", "meter"),
    "register": ("register", "寄存器", "分量", "费率"),
    "read_at": ("read_at", "抄表时间", "抄见时间", "时间"),
    "reading": ("reading", "读数", "示数", "累计读数"),
    "unit": ("unit", "单位", "计量单位"),
    "read_type": ("read_type", "抄表类型", "类型", "方式"),
    "note": ("note", "备注", "说明"),
}
REQUIRED_COLUMNS = ("meter_id", "register", "read_at", "reading")

# 可演示样例（白名单，避免任意路径读取）
SAMPLES = {
    "readings_dirty.csv": "脏样例 · 13 类问题",
    "readings_clean.csv": "干净样例",
    "readings_gbk.csv": "GBK 中文表头",
    "readings_truncated.csv": "末行截断",
    "readings_empty.csv": "空文件",
    "readings_header_only.csv": "仅表头",
    "readings_blank.csv": "全空行",
    "readings_bad_header.csv": "表头缺列",
}
DEFAULT_SAMPLE = "readings_dirty.csv"
METERS_FILE = "meters.csv"
PRICES_FILE = "prices.csv"

# 单一制电价兜底（prices.csv 缺失时）
DEFAULT_PRICES = {"TOTAL": Decimal("0.65"), "PEAK": Decimal("1.05"),
                  "FLAT": Decimal("0.65"), "VALLEY": Decimal("0.32")}


@dataclass(frozen=True)
class Settings:
    db_path: str = str(PROJECT_ROOT / "var" / "hydrocalendar.sqlite")
    sample_dir: str = str(PROJECT_ROOT / "sample_data")
    rule_version: str = RULE_VERSION

    @classmethod
    def from_env(cls) -> "Settings":
        base = cls()
        return replace(
            cls(),
            db_path=os.getenv("HYDROCALENDAR_DB") or base.db_path,
            sample_dir=os.getenv("HYDROCALENDAR_SAMPLE_DIR") or base.sample_dir,
        )

    @property
    def data_dir(self) -> Path:
        return Path(self.db_path).resolve().parent
