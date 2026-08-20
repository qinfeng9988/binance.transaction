## 修改需求

### 需求:系统必须支持策略回测
系统必须使用 `PaperTradingEngine`（生产 paper 模式引擎）对策略进行历史数据回测，写入独立的回测 database，输出含归因指标的绩效报告。回测时所有业务时间必须使用 K 线收盘时间（= kline 开盘时间 + 周期时长），禁止使用回测运行时的墙钟时间。

#### 场景:单策略回测
- **当** 用户对 support_resistance 策略运行回测
- **那么** 系统使用 `PaperTradingEngine` 执行交易，所有订单/持仓/归因数据写入 `db_backtest`，返回收益率、最大回撤、胜率、盈亏比、按事件类型聚合的归因 PNL 占比

#### 场景:参数优化
- **当** 用户指定参数范围进行多组回测
- **那么** 系统为每组参数生成独立 `run_id`，多组结果共存于回测 database，可通过 `run_id` 横向对比

#### 场景:回测信号处理路径与实盘一致
- **当** 回测中策略产出信号（开仓/平仓/部分平仓/止损更新）
- **那么** 信号路由必须复用与实盘相同的处理函数（含风控检查、冷却期），保证行为一致

#### 场景:回测报告包含归因指标
- **当** 单次回测完成
- **那么** 报告必须输出按事件类型×触发原因聚合（CLOSE_PARTIAL/STOP_LOSS/CLOSE_FULL/END_OF_DATA 等）的笔数和 PNL 占比，以及按 entry seq 聚合（首次开仓 vs 加仓）的 PNL 贡献

#### 场景:回测时间使用 bar 收盘时间
- **当** 回测处理第 N 根 K 线（开盘时间 T，周期 5m）
- **那么** 本轮所有写入的 signals.timestamp、orders.timestamp、positions.entry_time/exit_time、entries.entry_time、closures.close_time 必须为 T+5min（bar 收盘时间），禁止为回测运行时的墙钟时间

#### 场景:实盘时间不受影响
- **当** 实盘运行（paper/live 模式）且未设置 current_time
- **那么** 所有业务时间仍使用 `datetime.now(tz=timezone.utc)`，行为与变更前完全一致

#### 场景:止损触发时间
- **当** 回测中 update_prices 触发止损平仓
- **那么** closures.close_time 和 orders.timestamp 必须为当前 K 线的 bar 收盘时间
