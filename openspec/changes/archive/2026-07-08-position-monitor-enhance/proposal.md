## Why

持仓监控模式存在两个问题：1) 结构化止损在浮亏场景下被保本约束错误地拉到当前价另一侧，导致止损位高于当前价（做多时）或低于当前价（做空时），完全失去止损意义；2) 持仓监控输出缺少止盈建议、趋势信息、S/R 位详情，信息量不足以辅助手动交易决策。

## What Changes

- **修复结构化止损 bug**：浮亏时止损必须始终在当前价的安全侧（做多止损低于当前价，做空止损高于当前价），保本约束仅在浮盈时生效
- **新增止盈建议**：按质量分排序取前 2 个阻力/支撑位作为 TP1/TP2，位不足时用 ATR 倍数备选（TP1 = entry ± 2×ATR, TP2 = entry ± 3×ATR）
- **止损显示多个**：同时显示结构化止损和 ATR 止损，供用户选择
- **持仓监控输出格式重设计**：紧凑显示入场/盈亏/趋势/结构化建议/最近 S/R 位
- **S/R 位标签中文翻译**：vp_detail 字段翻译为中文显示

## Capabilities

### New Capabilities
- `position-take-profit`: 持仓止盈建议计算与展示，基于 S/R 位质量分排序 + ATR 备选

### Modified Capabilities
- `position-stop-loss`: 修复浮亏场景下结构化止损逻辑，止损必须始终在当前价安全侧；同时展示结构化止损和 ATR 止损
- `position-output-format`: 持仓监控输出格式重设计，增加趋势信息、止盈建议、S/R 位详情及中文标签

## Impact

- `quant_trader/core/query_server.py`：position_analysis 计算逻辑（止损修复 + 新增止盈字段）
- `quant_trader/query_sr.py`：`_format_position()` 输出格式重写 + vp_detail 中文翻译
- API 响应结构：`position_analysis` 新增 `tp1`, `tp2`, `tp1_source`, `tp2_source` 字段
