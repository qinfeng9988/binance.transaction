## Context

当前量化交易系统存在两套数据关联体系：早期设计的 orders 表通过 position_id/signal_id/order_type 直接关联持仓和信号，新引入的归因体系通过 position_entries / position_entry_closures 管理入场出场明细。两套体系之间存在三个问题：

1. **关联断裂**：Order 与 Entry/Closure 无直接关联，只能通过 position_id 间接跳转，查询脆弱
2. **手续费丢失**：归因 PNL 计算只扣除了平仓手续费分摊，开仓/加仓手续费未纳入，导致归因盈亏偏高
3. **冗余字段**：orders 表的 position_id、signal_id、order_type 在归因体系中都有对应，且 orders.position_id 的唯一查询场景（SUM fee）已有替代方案

当前数据流：

```
Order ──position_id──► Position ◄──position_id── Entry
                                              │
         间接关联，需 position_id + 时间戳匹配  │
```

## Goals / Non-Goals

**Goals:**
- 建立 Order ↔ Entry/Closure 的直接关联（order_id），支持双向追溯
- 修正归因 PNL 计算，纳入入场手续费分摊
- 删除 orders 表冗余字段（position_id、signal_id、order_type）
- 在 positions 表预聚合 total_fee，替代 SUM(orders.fee) 实时计算
- 更新所有表/字段 COMMENT，消除歧义

**Non-Goals:**
- 不重构归因查询 API 的返回格式（仅新增字段，不改变现有字段含义）
- 不修改 MockOrder/MockPosition 内存对象结构（运行时逻辑不变）
- 不做历史数据回填迁移（新字段从变更部署后开始写入，历史数据通过迁移脚本补全）
- 不修改 backtesting.py 回测框架（它不涉及 orders/entries 表）

## Decisions

### D1: Entry/Closure 加 order_id 而非 Order 加 entry_id/closure_ids

**选择**: 在 Entry 和 Closure 侧加 order_id（反向关联）

**替代方案**:
- A: Order 加 entry_id / closure_ids → 平仓单 1:N closure，需要 JSON 数组或多值字段，复杂
- B: Entry/Closure 加 order_id → 1:1 或 1:N 都自然处理，写入时已知 order_id

**理由**: 归因侧追溯到订单是更自然的查询方向（"这笔盈亏是哪个订单触发的？"），且 1:N 关系在 Closure 侧自然处理——一个 close Order 产生 N 个 Closure，每个都指向同一个 order_id。

### D2: 删除 orders.position_id

**选择**: 完全删除

**替代方案**:
- A: 保留为冗余便利字段 → 增加维护负担，且唯一查询场景已替代
- B: 删除 → 通过 Entry/Closure 的 order_id + position_id 间接关联

**理由**: 代码库中 orders.position_id 的唯一查询场景是 `SUM(fee) WHERE position_id = %s`（paper_trading.py:222），已由 positions.total_fee 替代。MockOrder.position_id 内存属性保留，仅 DB 字段删除。

### D3: 删除 orders.signal_id 和 orders.order_type

**选择**: 完全删除

**理由**:
- signal_id: Entry/Closure 已有 signal_id，orders.signal_id 是事后回填的（main.py:435-443），删除后回填逻辑也一并删除
- order_type: open/add 通过 Entry.seq 推断（seq=1→open, seq>1→add），close 类通过 Closure.reason 查询。MockOrder.order_type 内存属性保留

### D4: positions.total_fee 预聚合

**选择**: 在 positions 表加 total_fee 字段，仓位变化时累加更新

**替代方案**:
- A: 每次查询时 SUM(entries.entry_fee) + SUM(closures.fee) → 跨两张表 JOIN，效率低
- B: 预聚合字段 → 与现有 pnl/closed_quantity/close_count 模式一致

**理由**: positions 表已有 4 个累计字段（pnl、closed_quantity、close_count、partial_tp_triggered），total_fee 完全符合此模式。更新时机：开仓/加仓时 += entry_fee，平仓时 += close_fee。

### D5: entry_fee 归因分摊方式

**选择**: 在 Closure 的归因 PNL 计算中，按 close_qty / orig_qty 比例分摊 entry_fee

**公式**:
```
attributed_pnl = (exit_price - entry_price) × close_qty × direction
                 - closure.fee                          # 平仓手续费分摊
                 - entry.entry_fee × (close_qty / orig_qty)  # 入场手续费分摊
```

**替代方案**:
- A: entry_fee 整体计入首次 closure → 部分平仓时首次扣全部入场费，不公平
- B: 按比例分摊 → 每次平仓按实际扣减数量分摊，公平且与平仓手续费分摊逻辑一致

**理由**: 与现有 closure.fee 的分摊逻辑（按 close_qty / total_close_qty）保持一致，都是按实际数量比例分摊。

### D6: orders.fee 字段保留

**选择**: 保留 orders.fee 作为流水记录，但不再作为计算来源

**理由**: orders.fee 是原始交易流水，保留用于对账和审计。但持仓级手续费统计改用 positions.total_fee，归因级手续费改用 entries.entry_fee + closures.fee。COMMENT 中明确说明此定位。

## Risks / Trade-offs

- **[数据迁移]** 历史数据中 entries/closures 缺少 order_id 和 entry_fee，需迁移脚本补全 → 迁移脚本从 orders 表反推：按 position_id + timestamp 匹配 order 到 entry，按 fee_rate × entry_price × orig_qty 计算 entry_fee
- **[查询兼容]** 删除 orders.position_id 后，"查某仓位所有订单"需通过 Entry/Closure 的 order_id 反查 → 当前代码无此查询场景，未来需要时可创建视图
- **[归因 PNL 变化]** 修正后归因 PNL 会降低（多扣了入场手续费），历史数据重算后数值会变化 → 这是 bug 修正，不是回归；需在迁移脚本中重算 closures.pnl
- **[MockOrder 解耦]** MockOrder 内存对象保留 position_id/order_type/signal_id，但不再写入 DB → 需确保 _to_order_record() 正确映射，不遗漏字段

## Migration Plan

1. **Phase 1 - 加字段**: ALTER TABLE 添加新字段（order_id、entry_fee、total_fee），均为可空或有默认值，不影响现有逻辑
2. **Phase 2 - 数据回填**: 迁移脚本从 orders 表反推，补全 entries.order_id、entries.entry_fee、closures.order_id，重算 closures.pnl，计算并回填 positions.total_fee
3. **Phase 3 - 切换逻辑**: 修改 Python 代码，新写入走新字段，归因 PNL 用新公式
4. **Phase 4 - 删字段**: 确认新逻辑稳定后，ALTER TABLE 删除 orders.position_id/signal_id/order_type 及索引
5. **回滚**: Phase 1-2 可安全回滚（删新字段即可）；Phase 3-4 需代码回退 + 恢复旧字段
