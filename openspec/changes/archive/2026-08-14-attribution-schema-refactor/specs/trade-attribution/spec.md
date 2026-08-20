## MODIFIED Requirements

### 需求:系统必须将每笔平仓盈亏精确追溯到触发入场的信号
系统必须按 FIFO 顺序扣减 entries 生成 closures，归因 PNL 计算必须同时扣除平仓手续费分摊和入场手续费分摊。

#### 场景:全平归因计算
- **当** 系统执行全平操作，按 FIFO 顺序扣减 entries
- **那么** 每条 closure 的归因 PNL = (exit_price - entry_price) × close_qty × direction - closure.fee - entry.entry_fee × (close_qty / orig_qty)

#### 场景:部分平仓归因计算
- **当** 系统执行部分平仓操作，按 FIFO 顺序扣减 entries
- **那么** 每条 closure 的归因 PNL 必须按 close_qty / orig_qty 比例分摊对应 entry 的 entry_fee

#### 场景:止损平仓归因计算
- **当** 系统执行止损平仓操作
- **那么** 归因 PNL 计算同样必须扣除入场手续费分摊

#### 场景:归因 PNL 汇总验证
- **当** 查询某持仓的归因汇总
- **那么** SUM(closures.pnl) + SUM(closures.fee) + SUM(entries.entry_fee) 必须等于持仓的原始盈亏（不含手续费的毛盈亏）

### 需求:系统必须记录每笔入场的开仓手续费
系统必须在 position_entries 表中记录 entry_fee 字段，存储本笔入场的开仓/加仓手续费。

#### 场景:开仓时记录 entry_fee
- **当** 系统执行开仓操作，创建 position_entry 记录
- **那么** 系统将开仓手续费（entry_price × orig_qty × fee_rate）写入 position_entries.entry_fee

#### 场景:加仓时记录 entry_fee
- **当** 系统执行加仓操作，创建 position_entry 记录
- **那么** 系统将加仓手续费（entry_price × orig_qty × fee_rate）写入 position_entries.entry_fee

### 需求:持仓总手续费必须预聚合存储
系统必须在 positions 表中维护 total_fee 字段，等于该持仓下所有 entries.entry_fee 之和加上所有 closures.fee 之和。

#### 场景:开仓时更新 total_fee
- **当** 系统执行开仓操作
- **那么** positions.total_fee 设为 entry_fee

#### 场景:加仓时更新 total_fee
- **当** 系统执行加仓操作
- **那么** positions.total_fee 累加 entry_fee

#### 场景:平仓时更新 total_fee
- **当** 系统执行平仓操作（全平或部分平仓）
- **那么** positions.total_fee 累加本次平仓手续费

#### 场景:查询持仓总手续费
- **当** 需要查询某持仓的累计总手续费
- **那么** 系统直接读取 positions.total_fee，禁止通过 SUM(orders.fee) 计算

#### 场景:total_fee 一致性验证
- **当** 查询某持仓的 total_fee
- **那么** positions.total_fee 必须等于 SUM(position_entries.entry_fee WHERE position_id = X) + SUM(position_entry_closures.fee JOIN position_entries WHERE position_id = X)
