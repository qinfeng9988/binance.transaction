## 1. 止损 Bug 修复

- [x] 1.1 修复 `query_server.py` 中做多浮亏场景的结构化止损逻辑：将 `max(structured_stop, entry)` 改为浮盈时 max(entry)、浮亏时 min(current_price)
- [x] 1.2 修复 `query_server.py` 中做空浮亏场景的结构化止损逻辑：将 `min(structured_stop, entry)` 改为浮盈时 min(entry)、浮亏时 max(current_price)

## 2. 止盈计算

- [x] 2.1 在 `query_server.py` 的 position_analysis 计算中新增止盈逻辑：做多从阻力位按 quality_score 降序取 TP1/TP2，做空从支撑位按 quality_score 降序取 TP1/TP2
- [x] 2.2 实现 ATR 备选止盈：位不足时 TP1 = entry ± 2×ATR, TP2 = entry ± 3×ATR
- [x] 2.3 在 position_analysis 响应中新增 tp1, tp1_source, tp1_quality, tp2, tp2_source, tp2_quality 字段

## 3. 输出格式重设计

- [x] 3.1 在 `query_sr.py` 中新增 vp_detail 中文翻译映射表
- [x] 3.2 重写 `_format_position()` 函数，按新格式输出：标题/价格/入场/盈亏/趋势/结构化建议(止损+止盈)/最近S/R位
- [x] 3.3 在持仓监控输出中同时展示结构化止损和 ATR 止损

## 4. 验证

- [x] 4.1 语法检查：确认 query_server.py 和 query_sr.py 无语法错误
- [x] 4.2 CLI 帮助验证：`python -m quant_trader.query_sr --help` 正常输出
