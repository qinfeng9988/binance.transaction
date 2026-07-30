# Trade Attribution

交易归因系统：将每笔平仓盈亏精确追溯到触发入场的信号。

## 数据模型

- `position_entries`: 每笔入场记录（开仓/加仓），关联 signal_id
- `position_entry_closures`: 每笔出场记录（平仓/止盈/止损），按 FIFO 扣减 entries 生成

## 核心规则

- **FIFO**: 平仓时按入场顺序（seq）先进先出扣减
- **不覆盖**: 同一 entry 可有多条 closure 记录（部分平仓场景）
- **手续费分摊**: 按各 entry 被扣减数量比例分摊
- **归因精度**: 每条 closure 记录包含 entry_price、exit_price、pnl、reason、signal_id

## 查询能力

- 按信号归因：某信号触发的所有入场，其后续出场的盈亏汇总
- 按持仓归因：某持仓下所有 entries 的入场/出场明细
- 按时间段汇总：指定时间范围内的归因盈亏
