"""样例的期望值：全部由手算给出，再从实现里核对，不在测试里用容差。

金额与用量都是 Decimal；任何一处改成 float 累加，下面的断言就会失败。
"""
from __future__ import annotations

from decimal import Decimal

# 读数账
RAW_LINES = 60
VALID = 45
ESTIMATE = 1
QUARANTINE = 14
BLANK = 0
SEGMENTS = 2

# 区间与切分
INTERVALS = 32
ALLOCATIONS = 40
BILLS = 16
MONTHS = 5
TOTAL_USAGE = Decimal("4499.900")
TOTAL_AMOUNT = Decimal("3261.96")

# 主寄存器（总表优先）月度用量
MONTH_TOTALS = {
    "2025-12": Decimal("944.445"),
    "2026-01": Decimal("1399.928"),
    "2026-02": Decimal("1505.527"),
    "2026-03": Decimal("408.065"),
    "2026-04": Decimal("241.935"),
}

# 关键区间
M0001_DEC = Decimal("600.000")        # 跨年首段
M0001_MAR_SPLIT = (Decimal("258.065"), Decimal("241.935"))
M0002_USAGE = Decimal("500.000")      # 12.5 × 倍率 40
M0002_MONTHS = ("2026-01", "2026-02")
M0002_SPLIT = (Decimal("435.484"), Decimal("64.516"))
M0003_ROLLOVER = Decimal("249.900")   # 9999.9 − 9900 + 150
M0004_LAST = Decimal("60.000")        # 换表前末段
M0005_FIRST = Decimal("45.000")       # 新表首读从零起算
M0006_USAGE = Decimal("200.000")      # 0.2 MWh
M0008_TOTAL = Decimal("1000.000")

# 三分量闭合后的 M0008（尾差并入平段）
M0008_JAN = {"TOTAL": Decimal("344.444"), "PEAK": Decimal("137.778"),
             "FLAT": Decimal("154.999"), "VALLEY": Decimal("51.667")}
M0008_FEB = {"TOTAL": Decimal("311.111"), "PEAK": Decimal("124.444"),
             "FLAT": Decimal("140.001"), "VALLEY": Decimal("46.666")}
M0008_DEC = {"TOTAL": Decimal("344.445"), "PEAK": Decimal("137.778"),
             "FLAT": Decimal("155.000"), "VALLEY": Decimal("51.667")}

# 账单：分项各自舍入后再补尾差
M0001_MAR_BILL = (Decimal("310.33"),
                  {"PEAK": Decimal("171.39"), "FLAT": Decimal("119.36"),
                   "VALLEY": Decimal("19.58")}, 2)
M0001_MAR_NAIVE = Decimal("310.34")   # 朴素逐项四舍五入的结果
M0002_JAN_BILL = Decimal("283.06")
M0008_JAN_BILL = (Decimal("261.95"), 2)

# 原因码分布
QUARANTINE_CODES = {
    "Q_BAD_READING", "Q_BAD_REGISTER", "Q_BAD_TIME", "Q_BAD_UNIT",
    "Q_COLUMN_COUNT", "Q_DUPLICATE_CONFLICT", "Q_EXCEEDS_CAPACITY",
    "Q_METER_RETIRED", "Q_MISSING_READING", "Q_NEGATIVE_READING",
    "Q_REVERSE_READING", "Q_UNKNOWN_METER",
}
