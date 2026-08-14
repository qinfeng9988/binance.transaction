## Why

当前回测引擎（`BacktestEngine`）与实盘交易引擎（`PaperTradingEngine`）是两套并行实现，存在严重的行为差异：杠杆 PNL 计算方式不同、风控逻辑缺失、无交易归因支持。这导致回测结果无法真实反映实盘表现，且无法在回测中验证归因功能、利用归因数据优化策略参数。7 月 31 日上线的归因功能至今因行情清淡无法通过实盘充分测试，急需在回测中完成验证和策略迭代。

## What Changes

- **废弃 `BacktestEngine`**：删除 `scripts/backtest.py`、`scripts/backtest_range.py`、`quant_trader/backtest/runner.py` 中的独立回测引擎，统一使用 `PaperTradingEngine` 驱动回测
- **新增 `BacktestDataStore`**：继承 `DataStore`，覆盖写入方法注入 `run_id`，覆盖查询方法加 `run_id` 过滤。生产代码零侵入
- **新增回测专用 DB**：同一 PG 实例新增 `db_backtest` database，K 线表共享复用，交易表通过 `run_id` 隔离多组参数实验
- **新增 `BacktestKlineDriver`**：从 DB 读取历史 K 线逐根推进，替代实时 K 线事件源
- **新增统一回测入口**：`scripts/backtest_runner.py`，复用 `main.py` 的信号处理逻辑
- **新增归因分析脚本**：`scripts/analyze_attribution.py`，直连回测 DB，复用 `BacktestDataStore` 归因方法
- **风控参数化**：连续亏损冷静期、加仓降级、总风险上限从硬编码移至 `risk.yaml`
- **回测报告增强**：新增按 reason / entry_seq / 方向聚合的归因指标
- **BREAKING**：旧回测脚本（`backtest.py`、`backtest_range.py`）废弃，用户需迁移至新入口

## Capabilities

### New Capabilities
- `backtest-data-store`: 回测专用数据层，继承 DataStore 并通过 run_id 隔离多组参数实验数据，K 线表共享复用
- `backtest-kline-driver`: 历史 K 线驱动器，从 DB 读取 K 线逐根推进模拟 closed 事件，含 parent/grandparent 周期对齐和 funding rate JSON 缓存
- `backtest-attribution-analysis`: 回测归因分析，按 reason / entry_seq / 信号方向聚合归因 PNL，支持多 run_id 横向对比

### Modified Capabilities
- `backtesting`: 回测引擎从独立实现改为复用 PaperTradingEngine，信号处理逻辑与实盘统一，报告新增归因指标
- `trade-attribution`: 归因查询新增按 reason 和 entry_seq 聚合维度，BacktestDataStore 覆盖归因方法带 run_id 过滤
- `risk-management`: 连续亏损冷静期、加仓降级、总风险上限参数化至 risk.yaml，回测与实盘共享风控配置

## Impact

- **代码**：`quant_trader/core/` 新增 `backtest_data_store.py`、`backtest_kline_driver.py`；`scripts/` 新增 `backtest_runner.py`、`analyze_attribution.py`；`main.py` 抽取 `_handle_signal` 为可复用函数；`risk/manager.py` 参数化
- **数据库**：新增 `db_backtest` database，表结构含 `run_id` 字段；`filter_stats` 唯一键需含 `run_id`
- **配置**：新增 `backtest.yaml`（回测 DB 连接信息）；`risk.yaml` 扩展（冷静期/总风险参数）
- **废弃**：`scripts/backtest.py`、`scripts/backtest_range.py`、`quant_trader/backtest/runner.py`、两个 `BacktestEngine` 类
- **文档**：README.md 更新回测章节，新增归因分析说明
