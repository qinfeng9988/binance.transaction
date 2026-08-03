## Why

归因体系（position_entries / position_entry_closures）引入后，orders 表中的 position_id、signal_id、order_type 三个字段与 entries/closures 形成冗余，且 orders 与 entries/closures 之间缺少直接关联，无法从订单追溯到归因明细。同时，当前归因计算存在手续费丢失 bug：开仓/加仓手续费未纳入归因 PNL 计算，导致归因盈亏偏高。此外，持仓总手续费依赖 SUM(orders.fee) 实时计算，效率低且与归因体系不一致。

## What Changes

- **position_entries 新增 order_id 字段**：关联触发入场的订单，建立 Order → Entry 直接关联
- **position_entries 新增 entry_fee 字段**：记录本笔入场手续费，用于归因 PNL 计算
- **position_entry_closures 新增 order_id 字段**：关联触发平仓的订单，同一订单 FIFO 拆分产生的多条 closure 共享同一 order_id
- **positions 新增 total_fee 字段**：累计总手续费预聚合字段，= SUM(entries.entry_fee) + SUM(closures.fee)
- **BREAKING: orders 删除 position_id 字段**：冗余，通过 Entry/Closure 的 order_id 反查即可
- **BREAKING: orders 删除 signal_id 字段**：冗余，通过 Entry/Closure 的 signal_id 查询
- **BREAKING: orders 删除 order_type 字段**：冗余，open/add 通过 Entry.seq 推断，close 类通过 Closure.reason 查询
- **修正归因 PNL 计算**：closure 的归因 PNL 需扣除入场手续费分摊（entry_fee × close_qty / orig_qty）
- **修正持仓手续费统计**：改用 positions.total_fee 替代 SUM(orders.fee)
- **更新所有表和字段的 COMMENT**：消除歧义，明确各字段在归因体系中的定位

## Capabilities

### New Capabilities
- `order-attribution-link`: 订单与归因明细的直接关联（Entry/Closure 的 order_id 字段），支持从订单追溯到归因、从归因追溯到订单

### Modified Capabilities
- `trade-attribution`: 归因 PNL 计算需纳入入场手续费分摊，手续费统计改用 positions.total_fee
- `data-storage`: orders 表结构变更（删除 position_id/signal_id/order_type），positions 表新增 total_fee，entries/closures 新增 order_id 和 entry_fee

## Impact

- **数据库**: orders 表结构变更（删 3 字段、删 1 索引），positions 表加字段，entries 表加 2 字段，closures 表加 1 字段；需数据迁移脚本
- **data_store.py**: OrderRecord 删除 position_id/signal_id/order_type，EntryRecord/ClosureRecord 新增字段，insert/update 方法适配，归因查询 SQL 适配
- **paper_trading.py**: _to_order_record() 不再写 position_id/signal_id/order_type，_insert_entry() 写入 entry_fee+order_id，_close_entries_fifo() 修正归因 PNL 计算，restore_from_db() 改用 positions.total_fee
- **main.py**: 删除回填 orders.signal_id 的逻辑
- **fix_positions.py**: 适配新表结构
- **query_server.py**: 归因查询结果字段适配
- **API**: /api/v1/attribution 返回数据结构可能新增 order_id、entry_fee 字段
