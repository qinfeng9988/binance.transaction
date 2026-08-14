## ADDED Requirements

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
