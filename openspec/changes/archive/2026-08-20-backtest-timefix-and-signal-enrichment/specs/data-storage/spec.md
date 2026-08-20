## 修改需求

### 需求:系统必须记录交易信号
系统必须在 PostgreSQL 中创建并维护 signals 表，记录策略产生的所有交易信号。signals 表必须包含 12 个策略指标字段（quality_score、adx、plus_di、minus_di、rsi、volume_ratio、parent_trend、direction_type、is_counter_trend、atr、entry_threshold_pct、distance_pct），所有指标字段可为 NULL。

#### 场景:记录策略信号
- **当** 策略 on_bar 返回 Signal 对象
- **那么** 系统记录 (strategy_name, symbol, timeframe, signal_type, price, stop_loss, timestamp, leverage) 以及 12 个指标字段的值

#### 场景:指标字段为 NULL
- **当** 策略未为某个指标字段赋值（如 Bollinger 策略无 quality_score）
- **那么** 对应列写入 NULL

### 需求:系统必须记录交易订单
系统必须在 PostgreSQL 中创建并维护 orders 表，记录所有下单的成交流水。orders 表禁止包含 position_id、signal_id、order_type 字段，这些信息通过 position_entries 和 position_entry_closures 的 order_id 关联查询获取。

#### 场景:记录下单信息
- **当** 系统执行一笔交易
- **那么** 系统记录 (symbol, price, quantity, leverage, timestamp, mode, fee, status, order_id)，不记录 position_id、signal_id、order_type

#### 场景:记录订单状态更新
- **当** 订单状态发生变化（成交/取消）
- **那么** 系统更新 orders 表中的对应记录

#### 场景:查询订单关联的持仓
- **当** 需要查询某订单关联的持仓
- **那么** 系统通过 position_entries.order_id 或 position_entry_closures.order_id 关联查询 position_entries.position_id，再关联 positions 表

#### 场景:查询订单的信号来源
- **当** 需要查询某订单的触发信号
- **那么** 系统通过 position_entries.order_id 关联查询 position_entries.signal_id（开仓/加仓单），或通过 position_entry_closures.order_id 关联查询 position_entry_closures.signal_id（平仓单）

#### 场景:查询订单类型
- **当** 需要判断某订单是开仓/加仓/平仓
- **那么** 系统通过 position_entries.order_id 查询 entry 的 seq（seq=1→open, seq>1→add），或通过 position_entry_closures.order_id 查询 closure 的 reason

### 需求:系统必须记录入场明细
系统必须在 position_entries 表中记录每笔入场明细，必须包含 entry_time 字段记录入场的业务时间（K 线收盘时间）。

#### 场景:入场时记录 entry_time
- **当** 系统创建 position_entry 记录（开仓或加仓）
- **那么** 系统必须将当前业务时间写入 entry_time 列

#### 场景:回测 entry_time 为 bar 收盘时间
- **当** 回测模式下创建 position_entry
- **那么** entry_time 必须为入场信号所在 K 线的收盘时间

#### 场景:实盘 entry_time 为真实时间
- **当** 实盘模式下创建 position_entry
- **那么** entry_time 必须为当前 UTC 时间

### 需求:系统必须记录出场明细
系统必须在 position_entry_closures 表中记录每笔出场明细。close_time 必须显式传值（业务时间），禁止依赖 DB DEFAULT NOW()。表必须包含 create_time 和 update_time 列与其他表结构一致。

#### 场景:出场时记录 close_time
- **当** 系统创建 closure 记录
- **那么** 系统必须将当前业务时间显式写入 close_time 列

#### 场景:回测 close_time 为 bar 收盘时间
- **当** 回测模式下创建 closure
- **那么** close_time 必须为平仓信号所在 K 线的收盘时间

#### 场景:实盘 close_time 为真实时间
- **当** 实盘模式下创建 closure
- **那么** close_time 必须为当前 UTC 时间
