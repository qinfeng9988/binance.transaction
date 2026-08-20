## MODIFIED Requirements

### 需求:系统必须支持策略回测
系统必须使用 `PaperTradingEngine`（生产 paper 模式引擎）对策略进行历史数据回测，写入独立的回测 database，输出含归因指标的绩效报告。

#### 场景:单策略回测
- **当** 用户对 support_resistance 策略运行回测
- **那么** 系统使用 `PaperTradingEngine` 执行交易，所有订单/持仓/归因数据写入 `db_backtest`，返回收益率、最大回撤、胜率、盈亏比、按 reason 聚合的归因 PNL 占比

#### 场景:参数优化
- **当** 用户指定参数范围进行多组回测
- **那么** 系统为每组参数生成独立 `run_id`，多组结果共存于回测 database，可通过 `run_id` 横向对比

#### 场景:回测信号处理路径与实盘一致
- **当** 回测中策略产出信号（开仓/平仓/部分平仓/止损更新）
- **那么** 信号路由必须复用与实盘相同的处理函数（含风控检查、冷却期），保证行为一致

#### 场景:回测报告包含归因指标
- **当** 单次回测完成
- **那么** 报告必须输出按 reason 聚合（partial_tp_*/stop_loss_close/close 等）的笔数和 PNL 占比，以及按 entry seq 聚合（首次开仓 vs 加仓）的 PNL 贡献

## REMOVED Requirements

### 需求:系统必须支持从 Binance 下载历史 K 线数据
**Reason**: 此需求原属于 K 线拉取能力（已由 `kline_fetcher`、`fetch_history.py` 实现），不属于回测核心能力。回测仅消费已落库的 K 线，K 线下载在数据准备阶段完成。
**Migration**: K 线数据准备由回测入口内部触发（缺失时自动补全），独立的下载脚本继续保留为运维工具。

### 需求:系统必须支持回测数据可视化导出
系统必须将回测结果输出为可读的报告格式，包含交易明细和性能指标。
**Reason**: 已被新需求"回测报告包含归因指标"和归因分析脚本覆盖。
**Migration**: 使用新的 `scripts/backtest_runner.py` 和 `scripts/analyze_attribution.py` 替代旧的可视化导出。
