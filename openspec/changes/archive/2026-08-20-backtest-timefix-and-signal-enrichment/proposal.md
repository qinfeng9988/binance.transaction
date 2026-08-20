## 为什么

回测中 position_entries / position_entry_closures / orders / positions / signals 的时间字段全部使用回测运行时的墙钟时间（`NOW()`），而非 K 线周期对应的业务时间（bar 收盘时间）。这导致回测数据的时间列对复盘无意义，无法按时间维度做归因分析。同时 signals 表缺少策略关键指标字段（quality_score、ADX、RSI、ATR 等），这些数据虽在 Signal.metadata 中计算，但未落库，无法用于"指标分桶 × 归因 PNL"分析。

## 变更内容

- **回测时间修复**：回测时所有业务时间（signals.timestamp、orders.timestamp、positions.entry_time/exit_time、entries.entry_time、closures.close_time）改为 K 线收盘时间（= kline 开盘时间 + 周期时长）。通过给 PaperTradingEngine 注入 `current_time`（时钟注入）实现。
- **signals 表新增 12 个指标字段**：quality_score、adx、plus_di、minus_di、rsi、volume_ratio、parent_trend、direction_type、is_counter_trend、atr、entry_threshold_pct、distance_pct。从 Signal metadata 提取为显式字段落库。
- **Signal 模型扩展**：Signal dataclass 新增上述 12 个可选字段，不再依赖 metadata 传递。
- **entries 表新增 entry_time 列**：显式记录入场 bar 收盘时间。
- **closures 表新增 create_time/update_time 列**：与项目其他表保持结构一致；close_time 改为显式传值。
- **归因分桶分析**：analyze_attribution.py 增加"指标分桶 × 归因 PNL"维度，支持按 quality_score / adx / direction_type 等分桶统计胜率和 PNL。
- **Signal.timestamp 时区修复**：`datetime.utcnow`（naive）统一为 `datetime.now(tz=timezone.utc)`（aware），消除 +8h 偏移隐患。
- **回测库数据重建**：db_backtest 交易数据全部删除重新回测。生产库用 ALTER 加列，保留历史数据。

## 功能

### 新增功能
- `signal-metrics-storage`: 信号指标落库（12 个字段从 metadata 提取为显式列）
- `attribution-metrics-bucketing`: 归因分析按指标分桶统计 PNL 和胜率

### 修改功能
- `backtesting`: 回测时间从墙钟改为 bar 收盘时间（时钟注入机制）
- `data-storage`: entries 表加 entry_time 列；closures 表加 create_time/update_time；signals 表加 12 个指标列；时间字段显式传值
- `trade-attribution`: closures.close_time 改为业务时间，归因查询可按真实时间排序
- `backtest-attribution-analysis`: 增加指标分桶分析维度

## 影响

- **核心代码**：paper_trading.py（所有 `datetime.now()` 替换为 `self._now()`）、signal_router.py、backtest_runner.py、data_store.py、backtest_data_store.py
- **策略代码**：s_r_levels.py（补 atr / entry_threshold_pct / distance_pct 到 metadata）、bollinger_bands.py（补缺失指标）
- **模型**：models/__init__.py Signal dataclass、data_store.py EntryRecord / ClosureRecord / SignalRecord
- **DDL**：data_store.py、backtest_data_store.py、init_backtest_db.sql（三处同步）
- **迁移**：data_store.py _migrate 方法（生产库 ALTER）、回测库 DROP + 重建
- **分析脚本**：scripts/analyze_attribution.py
