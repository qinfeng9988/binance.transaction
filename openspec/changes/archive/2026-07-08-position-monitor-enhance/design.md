## Context

持仓监控模式通过 `query_server.py` 的 `/api/v1/query` 端点提供数据，由 `query_sr.py` CLI 格式化输出。当前结构化止损计算存在逻辑缺陷：`max(structured_stop, entry)` 在浮亏时将止损拉到入场价，导致止损位越过当前价。此外，持仓监控缺少止盈建议和趋势/S/R 详情展示。

数据流：
```
query_server.py (_query_handler)
  → analyze_sr_levels() 返回 analysis (含 levels, trend, adx, rsi, atr)
  → 计算 position_analysis (止损 + 止盈)
  → 返回 JSON

query_sr.py (_format_position)
  → 读取 position_analysis + 顶层字段
  → 格式化输出
```

## Goals / Non-Goals

**Goals:**
- 修复结构化止损在浮亏场景下的逻辑错误
- 新增基于 S/R 位质量分的止盈建议（TP1/TP2），ATR 备选
- 同时展示结构化止损和 ATR 止损
- 重设计持仓监控输出格式，增加趋势、S/R 位详情
- vp_detail 标签翻译为中文

**Non-Goals:**
- 不修改 `analyze_sr_levels()` 或 `identify_support_resistance()` 的核心分析逻辑
- 不修改 pre_trade 模式的输出格式
- 不修改 API 端点路径或请求参数

## Decisions

### D1: 止损安全侧约束

**决策**：止损必须始终在当前价的安全侧。保本约束（止损不低于入场价）仅在浮盈时生效。

**理由**：止损的核心语义是"价格到了这里就认输"。如果止损跑到当前价另一侧，意味着一开仓就触发止损，逻辑上不成立。

**替代方案**：浮亏时直接不设保本约束 → 采纳，但增加安全侧兜底。

```
做多止损逻辑:
  structured_stop = 最近支撑位 × (1 - buffer_pct)
  if current_price > entry:  # 浮盈
      structured_stop = max(structured_stop, entry)  # 保本止损
  else:  # 浮亏
      structured_stop = min(structured_stop, current_price)  # 确保低于当前价

做空止损逻辑:
  structured_stop = 最近压力位 × (1 + buffer_pct)
  if current_price < entry:  # 浮盈
      structured_stop = min(structured_stop, entry)  # 保本止损
  else:  # 浮亏
      structured_stop = max(structured_stop, current_price)  # 确保高于当前价
```

### D2: 止盈位选择策略

**决策**：按质量分（quality_score）降序排序，取前 2 个作为 TP1/TP2。位不足时用 ATR 倍数备选。

**理由**：质量分高的 S/R 位更可靠，止盈到那里的概率更高。按距离排序可能选到质量很低的位，止盈概率低。

**备选逻辑**：
```
做多:
  candidates = 阻力位中 price > current_price，按 quality_score 降序
  TP1 = candidates[0] (或 entry + 2×ATR)
  TP2 = candidates[1] (或 entry + 3×ATR)

做空:
  candidates = 支撑位中 price < current_price，按 quality_score 降序
  TP1 = candidates[0] (或 entry - 2×ATR)
  TP2 = candidates[1] (或 entry - 3×ATR)
```

### D3: vp_detail 中文翻译映射

**决策**：在 `_format_position()` 中使用映射表翻译，不修改分析函数返回值。

```
POC_near → POC近区
POC_far  → POC远区
HVN_near → HVN近区
HVN_far  → HVN远区
VAH_near → VAH近区
VAL_near → VAL近区
VA_inner → Value Area内
LVN      → LVN
none     → (不显示)
```

### D4: API 响应结构扩展

**决策**：`position_analysis` 新增字段，保持向后兼容（新字段为可选）。

```python
position_analysis = {
    # 原有字段
    "entry_price", "current_price", "side", "quantity",
    "unrealized_pnl", "profit_pct",
    "suggested_stop", "atr_stop_long", "atr_stop_short",
    # 新增字段
    "tp1": float | None,           # 止盈目标1价格
    "tp1_source": str | None,      # "sr" 或 "atr"
    "tp1_quality": float | None,   # S/R 位质量分（sr 来源时）
    "tp2": float | None,
    "tp2_source": str | None,
    "tp2_quality": float | None,
}
```

## Risks / Trade-offs

- **[止盈位可能过远]** → 质量分最高的阻力位可能距离当前价很远，止盈概率低。缓解：在输出中显示距离百分比，用户自行判断。
- **[ATR 备选止盈可能不合理]** → ATR 倍数是机械计算，不考虑市场结构。缓解：标注 source 为 "atr"，用户知道这是备选。
- **[浮亏时结构化止损可能很近]** → 最近支撑位可能就在当前价下方很近，止损空间极小。缓解：同时显示 ATR 止损供参考。
