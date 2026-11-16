"""流式按记录读取：用 csv 模块而不是 splitlines，保留每条记录的起始行号。

splitlines() + split(",") 会把「引号内逗号」切成两列、把「引号内换行」切成两条记录，
这是抄表导出文件最常见的两个坑。行号必须保留原始文件行号，隔离区才可复核。
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass


@dataclass
class RawRecord:
    lineno: int          # 记录起始行号（1 基，含表头占用的行）
    end_lineno: int      # 记录结束行号（引号内换行时大于起始行号）
    fields: list[str]

    @property
    def raw(self) -> str:
        return ",".join(self.fields)


def read_records(text: str) -> list[RawRecord]:
    stream = io.StringIO(text, newline="")
    reader = csv.reader(stream)
    records: list[RawRecord] = []
    previous_end = 0
    for fields in reader:
        start = previous_end + 1
        end = reader.line_num
        previous_end = end
        records.append(RawRecord(lineno=start, end_lineno=end, fields=list(fields)))
    return records


def count_data_lines(records: list[RawRecord], header_lines: int) -> int:
    """数据行数 = 记录数 − 表头行数（含中间重复表头）。"""
    return max(0, len(records) - header_lines)
