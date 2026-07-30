## 1. 数据库层：新增表和数据模型

- [x] 1.1 在 `data_store.py` 的 SCHEMA_SQL 中新增 `position_entries` 表建表语句
- [x] 1.2 在 `data_store.py` 的 SCHEMA_SQL 中新增 `position_entry_closures` 表建表语句
- [x] 1.3 新增 `EntryRecord` 数据类
- [x] 1.4 新增 `ClosureRecord` 数据类
- [x] 1.5 实现 `insert_entry()` 方法
- [x] 1.6 实现 `update_entry()` 方法（更新 remaining_qty 和 status）
- [x] 1.7 实现 `insert_closure()` 方法
- [x] 1.8 实现 `get_open_entries(position_id)` 方法（按 seq ASC 返回未平 entries）
- [x] 1.9 实现归因查询方法 `get_attribution_by_signal()`
- [x] 1.10 实现归因查询方法 `get_attribution_by_position()`

## 2. 模拟交易引擎：开仓/加仓流程

- [x] 2.1 `_update_position` 新仓分支：开仓时调用 `data_store.insert_entry()` 写入 entry (seq=1)
- [x] 2.2 `_update_position` 加仓分支：加仓时调用 `data_store.insert_entry()` 写入 entry (seq=递增)
- [x] 2.3 `MockPosition` 新增 `entry_seq_counter` 字段，追踪当前最大 seq

## 3. 模拟交易引擎：全平流程

- [x] 3.1 `close_position` 方法：全平时按 FIFO 遍历 entries，逐条扣减并写入 closures
- [x] 3.2 实现手续费按比例分摊到各 closure 记录
- [x] 3.3 更新 entries 的 remaining_qty=0, status='closed'

## 4. 模拟交易引擎：部分平仓流程

- [x] 4.1 `partial_close_position` 方法：部分平时按 FIFO 遍历 entries，逐条扣减并写入 closures
- [x] 4.2 处理跨 entry 扣减（一条 entry 扣完继续扣下一条）
- [x] 4.3 更新 entries 的 remaining_qty（部分扣减时保持 status='open'，全部扣完时变 'closed'）

## 5. 模拟交易引擎：止损触发流程

- [x] 5.1 `update_prices` 触发止损时，走 `close_position` 流程，reason 传入 "stop_loss_close"
- [x] 5.2 止损触发的 closure 记录 signal_id 为 NULL

## 6. 信号 ID 传递

- [x] 6.1 确保 `close_position` 方法接受并传递 signal_id 到 closure 记录
- [x] 6.2 确保 `partial_close_position` 方法接受并传递 signal_id 到 closure 记录
- [x] 6.3 确保 `update_prices` 止损触发时 signal_id 为 NULL

## 7. 程序重启恢复

- [x] 7.1 `restore_from_db` 方法：从 position_entries 表恢复 entries 的 seq 计数器
- [x] 7.2 验证 remaining_qty 与 positions.quantity 的一致性

## 8. 归因查询接口

- [x] 8.1 在 `QueryServer` 或 `DataStore` 中暴露归因查询 HTTP 接口
- [x] 8.2 支持按信号 ID 查询归因盈亏
- [x] 8.3 支持按持仓 ID 查询归因明细
- [x] 8.4 支持按时间段汇总归因盈亏
