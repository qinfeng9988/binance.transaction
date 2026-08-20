## 修改需求

### 需求:系统必须将每笔平仓盈亏精确追溯到触发入场的信号
系统必须按 FIFO 顺序扣减 entries 生成 closures，归因 PNL 计算必须同时扣除平仓手续费分摊和入场手续费分摊。closures 的 close_time 必须使用业务时间（K 线收盘时间），禁止使用墙钟时间。

#### 场景:全平归因计算
- **当** 系统执行全平操作，按 FIFO 顺序扣减 entries
- **那么** 每条 closure 的归因 PNL = (exit_price - entry_price) × close_qty × direction - closure.fee - entry.entry_fee × (close_qty / orig_qty)，且 close_time 为业务时间

#### 场景:部分平仓归因计算
- **当** 系统执行部分平仓操作，按 FIFO 顺序扣减 entries
- **那么** 每条 closure 的归因 PNL 必须按 close_qty / orig_qty 比例分摊对应 entry 的 entry_fee

#### 场景:止损平仓归因计算
- **当** 系统执行止损平仓操作
- **那么** 归因 PNL 计算同样必须扣除入场手续费分摊，close_time 为止损触发 K 线的收盘时间

#### 场景:归因 PNL 汇总验证
- **当** 查询某持仓的归因汇总
- **那么** SUM(closures.pnl) + SUM(closures.fee) + SUM(entries.entry_fee) 必须等于持仓的原始盈亏（不含手续费的毛盈亏）

#### 场景:归因查询按业务时间排序
- **当** 按时间范围查询归因数据
- **那么** 系统必须使用 close_time（业务时间）排序和过滤，禁止使用 create_time（记录写入时间）
