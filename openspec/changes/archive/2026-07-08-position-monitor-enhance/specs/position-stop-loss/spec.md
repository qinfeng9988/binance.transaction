## MODIFIED Requirements

### 需求:结构化止损计算
系统必须基于 S/R 位计算结构化止损，且止损必须始终在当前价的安全侧（做多止损低于当前价，做空止损高于当前价）。保本约束（止损不低于/高于入场价）仅在浮盈时生效。

#### 场景:做多浮盈时结构化止损
- **当** 持仓方向为 long 且 current_price > entry_price
- **那么** 结构化止损 = max(最近支撑位 × (1 - buffer_pct), entry_price)，确保不低于入场价（保本止损）

#### 场景:做多浮亏时结构化止损
- **当** 持仓方向为 long 且 current_price < entry_price
- **那么** 结构化止损 = min(最近支撑位 × (1 - buffer_pct), current_price)，确保止损低于当前价

#### 场景:做空浮盈时结构化止损
- **当** 持仓方向为 short 且 current_price < entry_price
- **那么** 结构化止损 = min(最近压力位 × (1 + buffer_pct), entry_price)，确保不高于入场价（保本止损）

#### 场景:做空浮亏时结构化止损
- **当** 持仓方向为 short 且 current_price > entry_price
- **那么** 结构化止损 = max(最近压力位 × (1 + buffer_pct), current_price)，确保止损高于当前价

### 需求:止损展示
系统必须同时展示结构化止损和 ATR 止损，供用户选择。

#### 场景:持仓监控输出包含两种止损
- **当** 持仓监控模式返回结果
- **那么** 输出必须包含结构化止损（基于 S/R 位）和 ATR 止损两个值
