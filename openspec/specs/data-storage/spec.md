## 新增需求

### 需求:系统必须将 K 线数据存储到 PostgreSQL
系统必须在 PostgreSQL 中创建并维护 klines 表，存储 OHLCV（开高低收量）数据。

#### 场景:存储新 K 线
- **当** 系统获取到新的 K 线数据
- **那么** 系统将 (symbol, timeframe, timestamp, open, high, low, close, volume) 写入 klines 表，重复记录自动跳过

#### 场景:查询历史 K 线
- **当** 策略需要获取最近 N 根 K 线
- **那么** 系统从 klines 表按 symbol、timeframe 和时间倒序查询指定数量记录

### 需求:系统必须记录交易订单
系统必须在 PostgreSQL 中创建并维护 orders 表，记录所有下单（含 paper 和真实）的详细信息。

#### 场景:记录下单信息
- **当** 系统执行一笔交易
- **那么** 系统记录 (symbol, side, price, quantity, leverage, timestamp, mode, order_id)

#### 场景:记录订单状态更新
- **当** 订单状态发生变化（成交/取消）
- **那么** 系统更新 orders 表中的对应记录

### 需求:系统必须记录交易信号
系统必须在 PostgreSQL 中创建并维护 signals 表，记录策略产生的所有交易信号。

#### 场景:记录策略信号
- **当** 策略 on_bar 返回 Signal 对象
- **那么** 系统记录 (strategy_name, symbol, timeframe, signal_type, price, stop_loss, timestamp)

### 需求:系统必须记录持仓信息
系统必须在 PostgreSQL 中维护 positions 表，追踪当前活跃持仓。

#### 场景:开仓记录
- **当** 执行买入操作
- **那么** 系统在 positions 表创建新记录

#### 场景:平仓更新
- **当** 持仓被平仓
- **那么** 系统更新 positions 表的平仓时间和盈亏
