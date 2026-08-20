## 需求

### 需求:系统必须提供回测 K 线驱动器
系统必须提供 `BacktestKlineDriver`，从回测 database 读取历史 K 线数据，按时间顺序逐根产出 K 线事件，替代实时 K 线事件源。

#### 场景:逐根推进产出 K 线事件
- **当** 回测脚本启动并遍历 K 线
- **那么** 驱动器必须按时间正序逐根返回 K 线，每根 K 线对应一个 closed 事件

#### 场景:补全缺失的 K 线数据
- **当** 回测 database 中指定时间范围的 K 线数据缺失率超过容忍阈值
- **那么** 驱动器必须通过 Binance API 补全数据后写入回测 database，再启动回测

#### 场景:K 线数据复用
- **当** 多个 run_id 在同一 symbol/timeframe 上回测同一时间范围
- **那么** 后续回测必须直接复用 database 中已有的 K 线，不重复拉取

### 需求:BacktestKlineDriver 必须提供 parent 周期对齐
系统必须在回测循环中为每根当前周期 K 线提供对应的 parent 周期滑动窗口，模拟实盘的多周期趋势确认。

#### 场景:parent 周期窗口对齐
- **当** 回测遍历到当前周期 K 线 K
- **那么** 驱动器必须返回 K 时刻之前的 parent 周期滑动窗口（最新在前），窗口大小符合 parent_lookback 配置

#### 场景:parent 周期数据不足时返回 None
- **当** parent 周期数据不足以构成有效窗口
- **那么** 驱动器必须返回 None，策略层需处理此情况

### 需求:BacktestKlineDriver 必须支持 funding rate 匹配
系统必须在回测循环中为每根 K 线匹配对应时间点的 funding rate，从本地 JSON 缓存读取。

#### 场景:按时间戳匹配 funding rate
- **当** 遍历到 K 线 K（时间戳 T）
- **那么** 驱动器必须返回不超过 T 的最近一条 funding rate 记录；无缓存时返回 None
