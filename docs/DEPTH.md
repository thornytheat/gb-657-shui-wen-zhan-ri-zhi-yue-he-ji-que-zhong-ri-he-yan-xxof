# 深度登记 hydrocalendar（水文站日值月合计缺重日核验）

- 规模：中型（7 个功能模块 / 6 个应用页 / 77 条测试）
- 核心规则：站点日值按版本选择而非后写覆盖，零雨与缺测分开；月合计用 Decimal，并以当地自然日日历核对完整度。
- 使用者与工作流：流域数据员复核多站逐日雨量、缺重日、月合计与相邻站异常。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 读数采信 | 导入样例，看逐行诊断、编码与分段 | POST /api/readings/import、页面 /readings | `app/modules/m1_ingest/service.py` `app/modules/m1_ingest/reader.py` `app/modules/m1_ingest/decode.py` `app/modules/m1_ingest/sniff.py` | `tests/test_m1_ingest.py` |
| M2 表计契约 | 看容量、倍率、寄存器、单位与电价，试跑一行解析 | GET /api/contract、POST /api/contract/preview | `app/modules/m2_contract/contract.py` `app/modules/m2_contract/coerce.py` `app/modules/m2_contract/units.py` | `tests/test_m2_contract.py` |
| M3 质量隔离 | 看原因码分布与隔离行，导出隔离区 CSV | GET /api/quarantine、GET /api/quarantine.csv | `app/modules/m3_quality/rules.py` `app/modules/m3_quality/quarantine.py` | `tests/test_m3_quality.py` |
| M4 区间还原 | 重算区间，看翻转、换表、估抄标记 | POST /api/intervals/rebuild、页面 /intervals | `app/modules/m4_intervals/restore.py` `app/modules/m4_intervals/rollover.py` `app/modules/m4_intervals/swaps.py` | `tests/test_m4_intervals.py` |
| M5 月量计算 | 按版本选择日值并计算月合计与缺日 | POST /api/hydro/monthly、页面 /months | `app/modules/m5_months/hydro.py` `app/modules/m5_months/allocate.py` `app/modules/m5_months/weights.py` | `tests/test_m5_months.py` `tests/test_hydro_rules.py` |
| M6 批次与对账 | 跑全流程、查批次历史、看四账、失败重试 | POST /api/runs、GET /api/batches、GET /api/reconcile、页面 /batches | `app/modules/m6_batches/service.py` `app/modules/m6_batches/compare.py` `app/modules/m6_batches/history.py` `app/modules/m6_batches/resume.py` | `tests/test_m6_batches.py` |
| M7 导出 | 导出单一月报与运行证据 | GET /api/export/monthly.csv、页面 /report | `app/modules/m7_export/evidence.py` `app/modules/m7_export/csv_out.py` `app/modules/m7_export/manifest_out.py` | `tests/test_m7_export.py` |

端到端：`tests/test_e2e.py`（脏样例 → 六页 200 → 四账 → 导出，含 4xx 失败输入与幂等重跑）。
不变量：`tests/test_invariants.py`（区间账 = 月度账、幂等重跑行数不变、月桶划分、账单闭合、读数分区）。

## 陷阱登记

| TRAP | 现象 | 朴素实现会怎样 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 国产抄表软件导出的 GB18030 文件被声明成 UTF-8 | `open()` 直接抛 UnicodeDecodeError，或中文表头全乱码 | 声明只作首个候选，严格解码失败按 utf-8-sig → utf-8 → gb18030 回退，留 `K_CHARSET_FALLBACK` | `app/modules/m1_ingest/decode.py` | `tests/test_m1_ingest.py::test_gbk_headers_decoded_by_fallback` |
| TRAP-02 | Excel 另存的文件头带 UTF-8 BOM | 首列名变成 `\ufeffmeter_id`，按列名取值全空 | 解码即剥离 BOM，并对首格再 strip 一次，留 `K_BOM_STRIPPED` | `app/modules/m1_ingest/decode.py`、`app/modules/m1_ingest/sniff.py` | `tests/test_m1_ingest.py::test_bom_stripped_before_header_mapping` |
| TRAP-04 | 两次导出拼接，中间又出现一行表头 | 表头被当成一条读数，列数校验误报 | 逐格比对表头签名，命中即分段 +1 并跳过该行 | `app/modules/m1_ingest/sniff.py` | `tests/test_m1_ingest.py::test_repeated_header_split_into_segments` |
| TRAP-05 | 行尾多一个分隔符、列数与表头不一致 | 静默补空值，或后续列全部错位 | 严格列数校验，整行进隔离区并保留原始行文本 | `app/modules/m1_ingest/service.py` | `tests/test_m1_ingest.py::test_column_count_mismatch_isolated_with_lineno` |
| TRAP-06 | `1,212.5`、`３，０５０.0` 这类千分位与全角写法 | `Decimal()` 抛异常，或整列变成空值 | NFKC 折半角 + 去千分位 + 去单位后缀，再 Decimal 化 | `app/modules/m2_contract/coerce.py` | `tests/test_m2_contract.py::test_thousands_and_fullwidth_normalized` |
| TRAP-07 | `-` / `N/A` / `未知` 混在读数里 | 被当成 0 参与区间计算，凭空多一段零 | 哨兵清单集中定义，返回 None；`0` 仍是有效读数 | `app/modules/m2_contract/coerce.py`、`app/config.py` | `tests/test_m2_contract.py::test_sentinel_reading_is_three_state` |
| TRAP-08 | 表号 `007` 被当成数字 7 | 对不上契约，或换表链断掉 | 全链路按字符串保形，`007` 与 `7` 是两个表号 | `app/modules/m2_contract/contract.py` | `tests/test_m2_contract.py::test_meter_id_leading_zero_preserved`、`tests/test_m3_quality.py::test_unknown_meter_and_leading_zero_mismatch` |
| TRAP-10 | 0.001 kWh 刻度上累加几十行 | float 累加掉尾差，月度合计差 0.001~0.01 | 全链路 Decimal，量化只在切分与账单两处显式发生 | `app/modules/m5_months/weights.py` | `tests/test_m5_months.py::test_decimal_totals_exact` |
| TRAP-11 | 同一时刻写成 `2026-01-01T00:00:00+08:00` 与 `2025-12-31T16:00:00Z` | 落进两个时间桶，区间多一段零用量 | 先归一 UTC 再排序，无偏移按 +08:00 处理 | `app/modules/m2_contract/coerce.py` | `tests/test_m2_contract.py::test_timezone_offsets_collapse_to_same_instant` |
| TRAP-14 | 同一 (表号, 寄存器, 时间) 两条不同读数 | 后写覆盖，前一条消失且不留痕 | 两条都进隔离区，带各自原值与行号，不猜哪条为准 | `app/modules/m3_quality/rules.py` | `tests/test_m3_quality.py::test_duplicate_key_conflict_isolates_both_rows` |
| TRAP-15 | 同一块表一条 kWh 一条 MWh | 差值差 1000 倍，区间用量离谱 | 读数先折成契约单位，用量再折 kWh，单位跟着数字走 | `app/modules/m2_contract/units.py` | `tests/test_m2_contract.py::test_unit_mixing_normalized_to_contract_unit` |
| TRAP-16 | 传输截断，末行只剩半截且没有换行符 | 半行被当有效读数，凭空多一段 | 末行无换行 + 列数不齐 → `Q_TRUNCATED_ROW`，文件级留 `K_TRUNCATED_TAIL` | `app/modules/m1_ingest/service.py` | `tests/test_m1_ingest.py::test_truncated_tail_row_isolated` |
| TRAP-18 | 0 字节文件、只有表头、数据行全是空行 | 三种都返回「成功导入 0 行」，或直接除零 | 三个文件级原因码分开返回，不落半批数据 | `app/modules/m1_ingest/service.py` | `tests/test_m1_ingest.py::test_empty_states_return_distinct_codes` |
| TRAP-23 | 区间止点与下一段起点都算一次 | 相邻区间重复计入一天，月度用量偏高 | 区间与月桶都声明左闭右开 `[起, 止)`，相邻段首尾相接 | `app/modules/m5_months/weights.py`、`app/modules/m4_intervals/restore.py` | `tests/test_m5_months.py::test_month_slices_are_half_open`、`tests/test_m4_intervals.py::test_intervals_are_half_open_and_contiguous` |
| TRAP-24 | 各月/各分量各自四舍五入后合计不等 | 分完差 0.001~0.02，账永远差几分 | 最大余额法分月；分量残差并入最大分量；账单先合计再舍入，分项补差 | `app/modules/m5_months/weights.py`、`app/modules/m5_months/tou.py` | `tests/test_m5_months.py::test_largest_remainder_keeps_total_exact`、`tests/test_m5_months.py::test_tou_components_closed_to_total` |

## 对账规则

- 读数账：原始数据行 = 有效读数 + 估抄 + 隔离行 + 空行（样例 60 = 45 + 1 + 14 + 0）。
- 区间账：主寄存器（总表优先）区间用量合计 = 月度分摊用量合计（0.001 kWh，逐位相等）。
- 月度账：同月同表「总表 = 峰 + 平 + 谷」，闭合后的残差必须为 0，闭合前的尾差条数单独计数。
- 账单账：账单金额 = 分项金额之和（分项先截尾再补分），四账在 `/` 与 `/batches` 页、`/api/reconcile`、运行清单里同源显示。

## 反例与不变量

- 反例（朴素实现会挂）：`tests/test_m4_intervals.py::test_naive_subtraction_would_drop_rollover`（直接相减得负数，翻转段被丢）、`tests/test_m5_months.py::test_bill_rounding_closed_not_naive`（逐项四舍五入多 1 分）、`tests/test_m1_ingest.py::test_naive_splitlines_would_break_records`（带千分位的单元格被切成两列）。
- 不变量：`tests/test_invariants.py::test_conservation_intervals_equal_months`（守恒）、`tests/test_invariants.py::test_idempotent_run_keeps_rows`（幂等）、`tests/test_invariants.py::test_month_slices_partition_every_interval`（划分）、`tests/test_invariants.py::test_bill_components_sum_equals_amount`（账单闭合）、`tests/test_invariants.py::test_readings_partition_holds`（分区）。
- 端到端：`tests/test_e2e.py::test_full_pipeline_over_http`。

## 已知不覆盖

- 只支持单表计的单向有功读数；双向表（正/反向分别累计）、需量寄存器、无功寄存器一律按 `Q_BAD_REGISTER` 隔离。
- 电价固定读 `sample_data/prices.csv`（峰 1.05 / 平 0.65 / 谷 0.32 元每 kWh），没有分时日历、阶梯电价与容量电费。
- 换表链只支持一段（旧表 → 新表）；链式多次换表能识别但只在最后一段起算，中间段会缺口。
- 时区按固定 +08:00 处理，不引入 DST；跨时区台区不支持。
- 并行抄表同一时刻多条读数只按「取值是否一致」判定冲突，没有设备侧时钟偏移校正。
- 页面只做展示与触发，没有登录与权限；批次历史最多展示 20 条。
- 大文件未做分块导入（样例 60 行；按 320 只表月均 700 行估算，单文件仍在内存可接受范围）。
