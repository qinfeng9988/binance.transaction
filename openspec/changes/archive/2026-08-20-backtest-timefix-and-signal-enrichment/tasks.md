## 1. 时钟注入（PaperTradingEngine）

- [x] 1.1 PaperTradingEngine 新增 `_current_time` 属性和 `_now()` / `set_current_time()` 方法，替换所有 `datetime.now(tz=timezone.utc)` 为 `self._now()`
- [x] 1.2 backtest_runner.py 主循环中每根 K 线调用 `paper_engine.set_current_time(bar_close_time)`，并 patch `sig.timestamp = bar_close_time`
- [x] 1.3 实现 `bar_close_time` 计算工具函数：`kline["timestamp"] + parse_timeframe(timeframe)`

## 2. Signal 模型与 metadata 补充

- [x] 2.1 Signal dataclass 新增 12 个可选字段（quality_score、adx、plus_di、minus_di、rsi、volume_ratio、parent_trend、direction_type、is_counter_trend、atr、entry_threshold_pct、distance_pct），默认 None
- [x] 2.2 Signal.timestamp 默认值从 `datetime.utcnow`（naive）改为 `datetime.now(tz=timezone.utc)`（aware）
- [x] 2.3 s_r_levels.py 补充 metadata 中缺失的 atr、entry_threshold_pct、distance_pct；并在 Signal 构造时显式赋值 12 个指标字段
- [x] 2.4 bollinger_bands.py 在 Signal 构造时显式赋值可用的指标字段（rsi、atr）

## 3. Record 模型扩展

- [x] 3.1 EntryRecord dataclass 新增 `entry_time: datetime` 字段
- [x] 3.2 ClosureRecord dataclass 新增 `close_time: datetime` 字段
- [x] 3.3 SignalRecord dataclass 新增 12 个指标字段

## 4. DDL 变更（三处同步）

- [x] 4.1 data_store.py DDL：signals 表加 12 个指标列；position_entries 加 entry_time 列；position_entry_closures 加 create_time/update_time 列
- [x] 4.2 backtest_data_store.py DDL：同步 4.1 变更
- [x] 4.3 init_backtest_db.sql：同步 4.1 变更

## 5. INSERT 语句扩展

- [x] 5.1 data_store.py insert_signal：加入 12 个指标字段
- [x] 5.2 data_store.py insert_entry：加入 entry_time 字段，显式传值
- [x] 5.3 data_store.py insert_closure：加入 close_time 字段，显式传值
- [x] 5.4 backtest_data_store.py insert_signal / insert_entry / insert_closure：同步 5.1-5.3

## 6. 生产库迁移

- [x] 6.1 data_store.py _migrate 方法：ALTER TABLE signals 加 12 列；ALTER position_entries 加 entry_time 列；ALTER position_entry_closures 加 create_time/update_time 列

## 7. 写入侧传值

- [x] 7.1 paper_trading.py place_order：创建 EntryRecord 时传入 `entry_time=self._now()`
- [x] 7.2 paper_trading.py _close_entries_fifo：创建 ClosureRecord 时传入 `close_time=self._now()`
- [x] 7.3 paper_trading.py insert_order：timestamp 使用 `self._now()`
- [x] 7.4 paper_trading.py open_position：entry_time 使用 `self._now()`
- [x] 7.5 paper_trading.py close_position / partial_close_position：exit_time 使用 `self._now()`
- [x] 7.6 signal_router.py handle_signal：创建 SignalRecord 时从 signal 对象提取 12 个指标字段

## 8. 归因分桶分析

- [x] 8.1 BacktestDataStore 新增 `get_attribution_by_metrics_bucket(run_id, metric_field, buckets)` 查询方法
- [x] 8.2 analyze_attribution.py 新增 `--bucket` 参数，支持 quality_score / direction_type / is_counter_trend / adx 分桶输出
- [x] 8.3 默认输出（无 --bucket）时自动展示四个维度的分桶统计

## 9. 回测库重建与验证

- [x] 9.1 TRUNCATE db_backtest 交易数据表（保留 klines），重跑回测验证所有时间字段为 bar 收盘时间
- [x] 9.2 验证 signals 表 12 个指标字段正确落库
- [x] 9.3 验证归因分桶输出正确
