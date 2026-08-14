## ADDED Requirements

### 需求:系统必须提供回测专用数据层
系统必须提供 `BacktestDataStore` 类，继承 `DataStore`，通过 `run_id` 标签在同一回测 database 中隔离多组参数实验数据，且不得修改生产 `DataStore` 的任何代码。

#### 场景:回测写入数据时自动注入 run_id
- **当** 回测脚本使用 `BacktestDataStore(run_id="bt_xxx")` 执行开仓
- **那么** positions、orders、signals、position_entries、position_entry_closures、filter_stats 表的写入操作必须自动注入当前 `run_id` 值

#### 场景:回测查询数据时自动过滤 run_id
- **当** 回测进程查询 open positions 或调用归因方法
- **那么** 查询必须自动加 `WHERE run_id = <当前 run_id>` 过滤，仅返回当前回测实例的数据

#### 场景:生产 DataStore 不感知 run_id
- **当** 生产代码 `PaperTradingEngine` 调用 `data_store.insert_position()` 等方法
- **那么** 生产 `DataStore` 的方法签名和实现必须保持不变，不出现任何 `run_id` 分支判断

#### 场景:K 线表跨 run_id 共享
- **当** 同一 symbol/timeframe 的多个 run_id 回测写入或读取 K 线数据
- **那么** klines 表必须不带 `run_id` 字段，所有 run_id 共享同一份 K 线数据

### 需求:BacktestDataStore 必须正确处理 positions 表的隐式唯一性
系统必须覆盖 `BacktestDataStore` 中所有依赖"同一 symbol 仅一个 open position"假设的查询方法，确保多 run_id 共存时不发生数据污染。

#### 场景:update_position 仅更新当前 run_id 的仓位
- **当** 多个 run_id 的 BTC/USDT open position 共存于 positions 表
- **那么** `update_position("BTC/USDT", ...)` 必须仅更新当前 `run_id` 的对应行

#### 场景:get_open_positions 仅返回当前 run_id 的持仓
- **当** 回测进程调用 `get_open_positions()`
- **那么** 结果必须仅包含当前 `run_id` 的 open positions

#### 场景:get_position_count 仅统计当前 run_id
- **当** RiskManager 调用 `get_position_count()` 检查持仓上限
- **那么** 返回值必须仅是当前 `run_id` 的 open positions 数量

### 需求:回测 database 必须使用独立的 filter_stats 唯一键
系统必须在 `db_backtest` 中为 `filter_stats` 表使用包含 `run_id` 的唯一约束，避免多 run_id 数据冲突。

#### 场景:upsert_filter_stats 在多 run_id 共存时不冲突
- **当** 多个 run_id 对同一 (strategy, symbol, timeframe, direction, filter_type) 写入 filter_stats
- **那么** 每个 run_id 必须拥有独立的行，不互相覆盖
