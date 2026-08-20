## ADDED Requirements

### 需求:系统必须记录入场明细的关联订单
系统必须在 position_entries 表中记录 order_id 字段，关联触发本笔入场的订单，建立 Order → Entry 的直接关联。

#### 场景:开仓时写入 entry 的 order_id
- **当** 系统执行开仓操作，创建 position_entry 记录
- **那么** 系统将对应订单的 id 写入 position_entries.order_id

#### 场景:加仓时写入 entry 的 order_id
- **当** 系统执行加仓操作，创建 position_entry 记录
- **那么** 系统将对应加仓订单的 id 写入 position_entries.order_id

#### 场景:从 entry 反查订单
- **当** 需要查询某笔入场对应的订单详情（订单编号、手续费等）
- **那么** 系统通过 position_entries.order_id 关联 orders 表查询

### 需求:系统必须记录出场明细的关联订单
系统必须在 position_entry_closures 表中记录 order_id 字段，关联触发本次平仓的订单。

#### 场景:全平时写入 closure 的 order_id
- **当** 系统执行全平操作，FIFO 生成一条或多条 closure 记录
- **那么** 每条 closure 记录的 order_id 必须指向触发本次平仓的订单

#### 场景:部分平仓时写入 closure 的 order_id
- **当** 系统执行部分平仓操作，FIFO 生成 closure 记录
- **那么** 每条 closure 记录的 order_id 必须指向触发本次部分平仓的订单

#### 场景:止损平仓时写入 closure 的 order_id
- **当** 系统执行止损平仓操作
- **那么** 每条 closure 记录的 order_id 必须指向触发止损的订单

#### 场景:同一订单产生多条 closure
- **当** 一个平仓订单按 FIFO 拆分到多个 entry
- **那么** 所有产生的 closure 记录共享同一个 order_id

#### 场景:从 closure 反查订单
- **当** 需要查询某笔出场对应的订单详情
- **那么** 系统通过 position_entry_closures.order_id 关联 orders 表查询

### 需求:系统必须支持从订单追溯到归因明细
系统必须支持通过 order_id 从 orders 表反向查询到 position_entries 和 position_entry_closures。

#### 场景:开仓/加仓订单追溯到入场明细
- **当** 查询某笔开仓或加仓订单的归因信息
- **那么** 系统通过 position_entries.order_id 找到对应的 entry 记录

#### 场景:平仓订单追溯到出场明细
- **当** 查询某笔平仓订单的归因信息
- **那么** 系统通过 position_entry_closures.order_id 找到对应的所有 closure 记录
