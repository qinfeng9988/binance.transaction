## MODIFIED Requirements

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

### 需求:系统必须记录持仓信息
系统必须在 PostgreSQL 中维护 positions 表，追踪当前活跃持仓。positions 表必须包含 total_fee 字段记录累计总手续费。

#### 场景:开仓记录
- **当** 执行买入操作
- **那么** 系统在 positions 表创建新记录，total_fee 设为开仓手续费

#### 场景:加仓更新
- **当** 执行加仓操作
- **那么** 系统更新 positions 表的数量和均价，total_fee 累加加仓手续费

#### 场景:平仓更新
- **当** 持仓被平仓
- **那么** 系统更新 positions 表的平仓时间和盈亏，total_fee 累加平仓手续费

## REMOVED Requirements

### 需求:orders 表必须记录 position_id
**Reason**: position_id 与归因体系冗余，通过 position_entries.order_id + position_entries.position_id 可间接关联，且 orders.position_id 的唯一查询场景（SUM fee WHERE position_id）已由 positions.total_fee 替代
**Migration**: 通过 position_entries.order_id 关联查询；MockOrder 内存对象的 position_id 属性保留，仅 DB 字段删除

### 需求:orders 表必须记录 signal_id
**Reason**: signal_id 与 position_entries.signal_id 和 position_entry_closures.signal_id 冗余，且 orders.signal_id 是事后回填的（main.py:435-443），删除后回填逻辑一并删除
**Migration**: 通过 position_entries.order_id → position_entries.signal_id 查询入场信号，通过 position_entry_closures.order_id → position_entry_closures.signal_id 查询平仓信号

### 需求:orders 表必须记录 order_type
**Reason**: order_type 与 position_entries.seq 和 position_entry_closures.reason 冗余；MockOrder 内存对象的 order_type 属性保留用于运行时逻辑判断，仅 DB 字段删除
**Migration**: 开仓/加仓类型通过 position_entries.order_id → position_entries.seq 推断（seq=1→open, seq>1→add）；平仓类型通过 position_entry_closures.order_id → position_entry_closures.reason 查询
