## 1. 数据库 Schema 变更（Phase 1 - 加字段）

- [x] 1.1 position_entries 表新增 order_id VARCHAR(50) REFERENCES orders(order_id) ON DELETE SET NULL 字段及 COMMENT
- [x] 1.2 position_entries 表新增 entry_fee NUMERIC(20,8) NOT NULL DEFAULT 0 字段及 COMMENT
- [x] 1.3 position_entry_closures 表新增 order_id VARCHAR(50) REFERENCES orders(order_id) ON DELETE SET NULL 字段及 COMMENT
- [x] 1.4 positions 表新增 total_fee NUMERIC(20,8) DEFAULT 0 字段及 COMMENT
- [x] 1.5 为新字段创建索引：idx_entries_order ON position_entries(order_id)，idx_closures_order ON position_entry_closures(order_id)

## 2. 数据模型适配（data_store.py）

- [x] 2.1 EntryRecord dataclass 新增 order_id: str | None 和 entry_fee: float 字段
- [x] 2.2 ClosureRecord dataclass 新增 order_id: str | None 字段
- [x] 2.3 OrderRecord dataclass 删除 position_id、signal_id、order_type 字段
- [x] 2.4 insert_entry() 方法适配：INSERT 语句加入 order_id、entry_fee 字段
- [x] 2.5 insert_closure() 方法适配：INSERT 语句加入 order_id 字段
- [x] 2.6 get_open_entries() 方法适配：SELECT 加入 order_id、entry_fee 字段
- [x] 2.7 insert_order() 方法适配：INSERT 语句移除 position_id、signal_id、order_type
- [x] 2.8 open_position() 方法适配：新增 total_fee 参数，INSERT 加入 total_fee
- [x] 2.9 update_position() 方法适配：新增 total_fee 参数，支持累加更新
- [x] 2.10 close_position() 方法适配：新增 total_fee 参数，最终累加更新
- [x] 2.11 归因查询 SQL 适配：get_attribution_by_signal/by_position/by_time_range 加入 order_id、entry_fee 字段

## 3. 归因 PNL 计算修正（paper_trading.py）

- [x] 3.1 _insert_entry() 方法适配：传入 order_id 和 entry_fee，调用 data_store.insert_entry() 写入新字段
- [x] 3.2 _close_entries_fifo() 方法修正：归因 PNL 公式加入 entry_fee 分摊（entry_fee × close_qty / orig_qty）
- [x] 3.3 _close_entries_fifo() 方法适配：传入 order_id，调用 data_store.insert_closure() 写入 order_id
- [x] 3.4 close_position() 方法适配：调用 _close_entries_fifo() 时传入 order_id，调用 data_store.close_position() 时累加 total_fee
- [x] 3.5 partial_close_position() 方法适配：调用 _close_entries_fifo() 时传入 order_id，调用 data_store.update_position()/close_position() 时累加 total_fee
- [x] 3.6 _update_position() 方法适配：开仓时调用 data_store.open_position() 传入 total_fee=entry_fee，加仓时调用 data_store.update_position() 累加 total_fee
- [x] 3.7 _to_order_record() 方法适配：不再写入 position_id、signal_id、order_type 到 OrderRecord
- [x] 3.8 restore_from_db() 方法修正：改用 positions.total_fee 替代 SUM(orders.fee) 计算已平仓手续费

## 4. 主流程适配（main.py）

- [x] 4.1 删除 main.py:435-443 回填 orders.signal_id 的逻辑
- [x] 4.2 place_order() 调用链适配：确保 order_id 在 _insert_entry() 和 _close_entries_fifo() 中正确传递

## 5. Comments 更新

- [x] 5.1 orders 表级 COMMENT 更新：补充"归因走 entries/closures"定位说明
- [x] 5.2 orders.fee COMMENT 更新：说明仅流水记录，指向归因体系
- [x] 5.3 positions 表级 COMMENT 更新：改"关联 orders"为"通过 entries/closures 关联"
- [x] 5.4 positions.pnl COMMENT 更新：补充"已扣除手续费"
- [x] 5.5 position_entry_closures.fee COMMENT 更新：明确"平仓手续费分摊"，补充归因 PNL 计算公式
- [x] 5.6 position_entry_closures.pnl COMMENT 更新：明确"已扣平仓手续费和入场手续费"

## 6. 辅助脚本适配

- [x] 6.1 fix_positions.py 适配：检查并修正对 orders.position_id/signal_id/order_type 的依赖 — 无需修改，该脚本不依赖这些字段
- [x] 6.2 reset.py 适配：确认表清理顺序兼容新字段 — 无需修改，清理顺序已正确

## 7. 数据库 Schema 变更（Phase 4 - 删字段）

- [x] 7.1 删除 orders.position_id 字段及 COMMENT
- [x] 7.2 删除 orders.signal_id 字段及 COMMENT
- [x] 7.3 删除 orders.order_type 字段及 COMMENT
- [x] 7.4 删除 idx_orders_position 索引
- [x] 7.5 更新 data_store.py 中 CREATE TABLE IF NOT EXISTS orders 的 DDL，移除已删字段

## 8. 数据迁移脚本（跳过：数据库无历史交易数据）

- [x] 8.1 编写迁移脚本：从 orders 表反推补全 position_entries.order_id（按 position_id + timestamp 匹配）— 跳过，无历史数据
- [x] 8.2 编写迁移脚本：计算并回填 position_entries.entry_fee（按 fee_rate × entry_price × orig_qty）— 跳过，无历史数据
- [x] 8.3 编写迁移脚本：从 orders 表反推补全 position_entry_closures.order_id（按 position_id + timestamp 匹配 close 类订单）— 跳过，无历史数据
- [x] 8.4 编写迁移脚本：重算 position_entry_closures.pnl（用新公式扣除 entry_fee 分摊）— 跳过，无历史数据
- [x] 8.5 编写迁移脚本：计算并回填 positions.total_fee（SUM entries.entry_fee + SUM closures.fee）— 跳过，无历史数据
