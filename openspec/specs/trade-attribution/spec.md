# Trade Attribution

交易归因系统：将每笔平仓盈亏精确追溯到触发入场的信号。

## 数据模型

- `position_entries`: 每笔入场记录（开仓/加仓），关联 signal_id
- `position_entry_closures`: 每笔出场记录（平仓/止盈/止损），按 FIFO 扣减 entries 生成

## 核心规则

- **FIFO**: 平仓时按入场顺序（seq）先进先出扣减
- **不覆盖**: 同一 entry 可有多条 closure 记录（部分平仓场景）
- **平仓手续费分摊**: 按各 entry 被扣减数量占本次总平仓数量比例分摊（close_qty / total_close_qty）
- **入场手续费分摊**: 按各 entry 被扣减数量占该 entry 原始数量比例分摊（close_qty / orig_qty）
- **归因 PNL 公式**: `(exit_price - entry_price) × close_qty × direction - closure.fee - entry.entry_fee × (close_qty / orig_qty)`
- **归因精度**: 每条 closure 记录包含 entry_price、exit_price、pnl、reason、order_id、signal_id

## 手续费体系

- `orders.fee`: 仅流水记录对账用，不作为计算来源
- `position_entries.entry_fee`: 本笔入场手续费，归因计算按比例分摊
- `position_entry_closures.fee`: 本笔平仓手续费分摊
- `positions.total_fee`: 预聚合字段 = SUM(entries.entry_fee) + SUM(closures.fee)

## 查询能力

- 按信号归因：某信号触发的所有入场，其后续出场的盈亏汇总
- 按持仓归因：某持仓下所有 entries 的入场/出场明细
- 按时间段汇总：指定时间范围内的归因盈亏

## 新增需求（attribution-schema-refactor 变更）

### 需求:系统必须将每笔平仓盈亏精确追溯到触发入场的信号
系统必须按 FIFO 顺序扣减 entries 生成 closures，归因 PNL 计算必须同时扣除平仓手续费分摊和入场手续费分摊。closures 的 close_time 必须使用业务时间（K 线收盘时间），禁止使用墙钟时间。

#### 场景:全平归因计算
- **当** 系统执行全平操作，按 FIFO 顺序扣减 entries
- **那么** 每条 closure 的归因 PNL = (exit_price - entry_price) × close_qty × direction - closure.fee - entry.entry_fee × (close_qty / orig_qty)，且 close_time 为业务时间

#### 场景:部分平仓归因计算
- **当** 系统执行部分平仓操作，按 FIFO 顺序扣减 entries
- **那么** 每条 closure 的归因 PNL 必须按 close_qty / orig_qty 比例分摊对应 entry 的 entry_fee

#### 场景:止损平仓归因计算
- **当** 系统执行止损平仓操作
- **那么** 归因 PNL 计算同样必须扣除入场手续费分摊，close_time 为止损触发 K 线的收盘时间

#### 场景:归因 PNL 汇总验证
- **当** 查询某持仓的归因汇总
- **那么** SUM(closures.pnl) + SUM(closures.fee) + SUM(entries.entry_fee) 必须等于持仓的原始盈亏（不含手续费的毛盈亏）

#### 场景:归因查询按业务时间排序
- **当** 按时间范围查询归因数据
- **那么** 系统必须使用 close_time（业务时间）排序和过滤，禁止使用 create_time（记录写入时间）

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

## 新增需求（unify-backtest-engine 变更）

### 需求:出场明细必须区分事件类型与触发原因
系统必须在 `position_entry_closures` 表中将 `reason`（事件类型）与 `trigger_reason`（策略触发原因）分为两个字段，禁止用单一 reason 字段承载两层语义。

reason 枚举（机械事实）：`CLOSE_PARTIAL`（部分平仓）/ `CLOSE_FULL`（信号全平）/ `STOP_LOSS`（止损触发）/ `END_OF_DATA`（回测结束强平）。
trigger_reason 取值（业务语义）：`2.0R`/`3.5R`（止盈级别）、`ema_cross_down` 等（策略 close 信号 metadata.reason）、`reverse_signal`（反向信号）、`stop_loss`（止损）、`end_of_backtest`（回测结束）、`signal_close`（默认）。

#### 场景:部分止盈触发记录级别
- **当** 策略以 partial_tp_level="2.0R" 触发部分平仓
- **那么** closure 记录 reason='CLOSE_PARTIAL'、trigger_reason='2.0R'

#### 场景:归因方向不由 pnl 符号推断
- **当** 同一次部分平仓对不同 entry 的归因 pnl 一正一负（加仓场景）
- **那么** 两条 closure 的 reason 和 trigger_reason 相同（同一事件），方向由各自 pnl 符号表达

#### 场景:反向信号平仓
- **当** 持多仓收到反向信号触发平仓
- **那么** closure 记录 reason='CLOSE_FULL'、trigger_reason='reverse_signal'

#### 场景:回测结束强平
- **当** 回测数据耗尽强制平仓所有 open positions
- **那么** closure 记录 reason='END_OF_DATA'、trigger_reason='end_of_backtest'

### 需求:归因聚合查询必须按事件类型聚合而非 pnl 符号过滤
系统的多 run_id 对比查询必须按 reason 枚举聚合各事件类型的 PNL 贡献，禁止用 `pnl > 0` 之类过滤冒充"止盈 PNL"（同一触发事件的归因对不同 entry 可正可负）。

#### 场景:对比表列语义
- **当** 用户执行多 run_id 对比
- **那么** 输出列为止损PNL（STOP_LOSS）、部分平仓PNL（CLOSE_PARTIAL）、全平PNL（CLOSE_FULL）、强平PNL（END_OF_DATA），每列为该事件类型的归因总和（含正负）

### 需求:系统必须支持按出场原因聚合归因
系统必须在交易归因查询中支持按出场原因（reason）聚合，展示不同出场类型对总 PNL 的贡献。

#### 场景:按 reason 查询归因
- **当** 用户或归因分析脚本调用 `get_attribution_by_reason()`
- **那么** 系统返回每个 reason 的笔数、总归因 PNL、占比、胜率，按 PNL 降序排列

### 需求:系统必须支持按入场序号聚合归因
系统必须在交易归因查询中支持按入场序号（entry seq）聚合，区分首次开仓与加仓的 PNL 贡献。

#### 场景:按 entry_seq 查询归因
- **当** 用户或归因分析脚本调用 `get_attribution_by_entry_seq()`
- **那么** 系统返回每个 seq 的笔数、总归因 PNL、平均 PNL、胜率，按 seq 升序排列

### 需求:BacktestDataStore 的归因方法必须自动过滤 run_id
系统必须在 `BacktestDataStore` 中覆盖所有归因查询方法（`get_attribution_by_signal`、`get_attribution_by_position`、`get_attribution_by_time_range`），自动加 `WHERE run_id = %s` 过滤。

#### 场景:回测归因查询带 run_id 过滤
- **当** 回测脚本或归因分析脚本通过 `BacktestDataStore` 调用归因方法
- **那么** 查询结果必须仅包含当前 `run_id` 的归因数据，不混入其他 run_id 的数据
