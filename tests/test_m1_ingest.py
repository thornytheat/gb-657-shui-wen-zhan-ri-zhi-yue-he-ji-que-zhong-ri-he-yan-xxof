"""M1 读数采信：编码回退、BOM、重复表头、列数、截断、空态三态。"""
from __future__ import annotations

import pytest

from app import errors
from app.modules.m1_ingest import service
from fixtures import expected


def test_dirty_sample_rows_keep_original_lineno(settings):
    result = service.ingest(settings, "readings_dirty.csv")
    assert result.data_lines == expected.RAW_LINES
    assert len(result.rows) == expected.RAW_LINES - 1
    assert result.segments == expected.SEGMENTS
    assert min(row["lineno"] for row in result.rows) >= 2
    assert [row["lineno"] for row in result.rows] == sorted(
        row["lineno"] for row in result.rows)


def test_gbk_headers_decoded_by_fallback(settings):
    """TRAP-01：文件声明 UTF-8 但字节是 GB18030，中文表头要能读出来。"""
    result = service.ingest(settings, "readings_gbk.csv")
    assert result.encoding == "gb18030"
    assert result.fallback is True
    header = {name: index for name, index in result.header_map.items()}
    assert "meter_id" in header and "reading" in header
    assert result.rows[0]["cells"]["meter_id"] == "007"
    assert result.rows[0]["cells"]["read_type"] == "正常"
    assert result.rows[0]["cells"]["note"] == "号井"


def test_bom_stripped_before_header_mapping(settings):
    """TRAP-02：首列 BOM 未剥离时首列名会变成 \\ufeffmeter_id。"""
    result = service.ingest(settings, "readings_dirty.csv")
    assert result.bom_stripped is True
    assert result.header[0] == "meter_id"
    assert "\ufeff" not in result.header[0]
    assert result.header_map["meter_id"] == 0


def test_repeated_header_split_into_segments(settings):
    """TRAP-04：中间再插一行表头不能被当成数据行。"""
    result = service.ingest(settings, "readings_dirty.csv")
    assert result.segments == expected.SEGMENTS
    assert result.data_lines == expected.RAW_LINES
    codes = [item["code"] for item in result.diagnostics]
    assert "K_SEGMENT_SPLIT" in codes


def test_column_count_mismatch_isolated_with_lineno(settings):
    """TRAP-05：多一列的行进隔离区，带原始行号与原值。"""
    result = service.ingest(settings, "readings_dirty.csv")
    assert len(result.quarantine) == 1
    item = result.quarantine[0]
    assert item["code"] == "Q_COLUMN_COUNT"
    assert item["field"] == "列数"
    assert item["raw_value"].startswith("M0001,TOTAL,")
    assert item["lineno"] > 1


def test_truncated_tail_row_isolated(settings):
    """TRAP-16：末行被截断（且文件末尾没有换行符）单独给码。"""
    result = service.ingest(settings, "readings_truncated.csv")
    assert result.tail_without_newline is True
    codes = [item["code"] for item in result.diagnostics]
    assert "K_TRUNCATED_TAIL" in codes
    assert [item["code"] for item in result.quarantine] == ["Q_TRUNCATED_ROW"]
    assert result.data_lines == 3


def test_empty_states_return_distinct_codes(settings):
    """TRAP-18：0 字节 / 仅表头 / 全空行是三个不同结论。"""
    cases = [
        ("readings_empty.csv", "E_EMPTY_FILE"),
        ("readings_header_only.csv", "E_HEADER_ONLY"),
        ("readings_blank.csv", "E_ALL_BLANK"),
    ]
    seen = set()
    for name, code in cases:
        with pytest.raises(errors.UnprocessableInput) as excinfo:
            service.ingest(settings, name)
        assert excinfo.value.code == code
        seen.add(excinfo.value.code)
    assert len(seen) == 3


def test_quoted_thousands_keeps_single_field(settings):
    result = service.ingest(settings, "readings_clean.csv")
    values = [row["cells"]["reading"] for row in result.rows]
    assert "1,212.5" in values
    assert "3,050.0" in values


def test_naive_splitlines_would_break_records(settings):
    """反例：splitlines+split(",") 会把带千分位的单元格切成两列。"""
    text = (settings and "meter_id,register,read_at,reading,unit,read_type,note\n"
            "M0002,TOTAL,2026-02-05T00:00:00+08:00,\"1,212.5\",kWh,N,\n")
    naive = [field for field in text.splitlines()[1].split(",")]
    assert len(naive) == 8
    records = __import__("app.modules.m1_ingest.reader", fromlist=["read_records"]
                         ).read_records(text)
    assert len(records[1].fields) == 7
    assert records[1].fields[3] == "1,212.5"
