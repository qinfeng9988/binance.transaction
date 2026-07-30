## 设计概览

在现有合并持仓模型之上，增加归因明细层。运行时持仓逻辑不变，但每次开仓/加仓记录一条 entry，每次平仓按 FIFO 扣减 entries 并生成 closure 记录，实现事后精确归因。

```
┌──────────────┐         ┌──────────────┐         ┌───────────────────┐
│   signals    │         │  positions   │         │ position_entries  │
│              │         │  (不变)      │         │  (新增)           │
│  买入 #1     │────┐    │  BTC/USDT    │────────▶│  entry #1         │
│  买入 #2     │────┤    │  均价=61000  │         │  0.1@60000 sig#1  │
│              │    │    │  总量=0.2    │         │  entry #2         │
└──────────────┘    │    └──────────────┘         │  0.1@62000 sig#2  │
                    └────────────────────────────▶│                   │
                                                   └───────────────────┘
                                                           │
                                                           │ 平仓时 FIFO 扣减
                                                           ▼
                                                   ┌────────────────────────┐
                                                   │ position_entry_closures│
                                                   │  (新增)                │
                                                   │  closure #1: entry#1   │
                                                   │    0.1@60000→59000     │
                                                   │    pnl=-100 stop_loss  │
                                                   │  closure #2: entry#2   │
                                                   │    0.1@62000→59000     │
                                                   │    pnl=-300 stop_loss  │
                                                   └────────────────────────┘
```

## 数据模型

### position_entries 表

每笔入场（开仓或加仓）产生一条记录。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| position_id | INTEGER NOT NULL REFERENCES positions(id) | 关联持仓 ID |
| seq | INTEGER NOT NULL | 入场顺序（1=首次开仓, 2=第一次加仓, ...） |
| orig_qty | NUMERIC(20,8) NOT NULL | 原始入场数量（不变） |
| remaining_qty | NUMERIC(20,8) NOT NULL | 剩余未平数量（每次平仓递减） |
| entry_price | NUMERIC(20,8) NOT NULL | 本笔入场价格（不变） |
| signal_id | INTEGER REFERENCES signals(id) ON DELETE SET NULL | 触发本笔入场的信号 ID |
| status | VARCHAR(10) DEFAULT 'open' | "open" / "closed" |
| create_time | TIMESTAMPTZ NOT NULL DEFAULT NOW() | 入场时间 |
| update_time | TIMESTAMPTZ NOT NULL DEFAULT NOW() | 最后更新时间 |

UNIQUE 约束: (position_id, seq)

### position_entry_closures 表

每次平仓（全平或部分平）按 FIFO 扣减 entries，每个被扣减的 entry 产生一条 closure 记录。同一个 entry 可以有多条 closure 记录（部分平仓场景）。

| 字段 | 类型 | 说明 |
|------|------|------|
| id | SERIAL PRIMARY KEY | 主键 |
| entry_id | INTEGER NOT NULL REFERENCES position_entries(id) | 关联入场记录 ID |
| close_qty | NUMERIC(20,8) NOT NULL | 本次平仓数量 |
| entry_price | NUMERIC(20,8) NOT NULL | 入场价（冗余，方便查询） |
| exit_price | NUMERIC(20,8) NOT NULL | 出场价 |
| pnl | NUMERIC(20,8) NOT NULL | 本笔归因盈亏（已扣分摊手续费） |
| fee | NUMERIC(20,8) NOT NULL | 本笔分摊的手续费 |
| reason | VARCHAR(20) NOT NULL | 出场原因: "partial_close" / "stop_loss_close" / "take_profit_close" / "close" |
| signal_id | INTEGER REFERENCES signals(id) ON DELETE SET NULL | 触发本次平仓的信号 ID（止损时为 NULL） |
| close_time | TIMESTAMPTZ NOT NULL DEFAULT NOW() | 平仓时间 |

索引:
- idx_entry_closures_entry ON position_entry_closures(entry_id, close_time ASC)
- idx_entry_closures_signal ON position_entry_closures(signal_id)

## 核心流程

### 开仓

```
1. positions 表: 插入新持仓记录（不变）
2. position_entries 表: 插入 entry (seq=1, orig_qty=开仓数量, remaining_qty=开仓数量)
3. orders 表: 插入开仓订单（不变）
```

### 加仓

```
1. positions 表: 更新加权均价和总数量（不变）
2. position_entries 表: 插入 entry (seq=当前最大seq+1, orig_qty=加仓数量, remaining_qty=加仓数量)
3. orders 表: 插入加仓订单（不变）
```

### 全平

```
1. 按 FIFO 顺序遍历该 position_id 下 status='open' 的 entries（按 seq ASC）
2. 对每个 entry:
   a. close_qty = entry.remaining_qty（全部扣完）
   b. 计算 pnl = (exit_price - entry_price) * close_qty * direction_multiplier
   c. 分摊手续费: fee = 总手续费 * (close_qty / 总平仓数量)
   d. 插入 position_entry_closures 记录
   e. 更新 entry: remaining_qty=0, status='closed'
3. positions 表: 更新为 status='closed'（不变）
4. orders 表: 插入平仓订单（不变）
```

### 部分平仓

```
1. 计算需要平仓的总数量 close_total
2. 按 FIFO 顺序遍历 entries:
   remaining_to_close = close_total
   for entry in entries (seq ASC, status='open'):
     if remaining_to_close <= 0: break
     close_qty = min(entry.remaining_qty, remaining_to_close)
     计算 pnl 和分摊 fee
     插入 position_entry_closures 记录
     更新 entry: remaining_qty -= close_qty
     if entry.remaining_qty == 0: entry.status = 'closed'
     remaining_to_close -= close_qty
3. positions 表: 更新剩余数量（不变）
4. orders 表: 插入部分平仓订单（不变）
```

### 手续费分摊

一次平仓操作的总手续费，按各 entry 被扣减数量的比例分摊:

```
entry_fee = total_fee * (entry_close_qty / total_close_qty)
entry_pnl = raw_pnl - entry_fee
```

## 归因查询

### 按信号归因

```sql
SELECT
    s.id AS signal_id,
    s.signal_type,
    s.price AS signal_price,
    s.timestamp AS signal_time,
    pe.entry_price,
    SUM(ec.close_qty) AS total_closed_qty,
    SUM(ec.pnl) AS total_attributed_pnl,
    SUM(ec.fee) AS total_fee,
    json_agg(json_build_object(
        'close_qty', ec.close_qty,
        'exit_price', ec.exit_price,
        'pnl', ec.pnl,
        'reason', ec.reason,
        'close_time', ec.close_time
    ) ORDER BY ec.close_time) AS close_details
FROM position_entries pe
JOIN signals s ON pe.signal_id = s.id
JOIN position_entry_closures ec ON ec.entry_id = pe.id
GROUP BY s.id, s.signal_type, s.price, s.timestamp, pe.entry_price
ORDER BY s.timestamp;
```

### 按持仓归因

```sql
SELECT
    p.id AS position_id,
    p.symbol,
    p.side,
    pe.seq,
    pe.orig_qty,
    pe.entry_price,
    pe.signal_id,
    SUM(ec.close_qty) AS total_closed_qty,
    SUM(ec.pnl) AS total_attributed_pnl
FROM positions p
JOIN position_entries pe ON pe.position_id = p.id
LEFT JOIN position_entry_closures ec ON ec.entry_id = pe.id
GROUP BY p.id, p.symbol, p.side, pe.seq, pe.orig_qty, pe.entry_price, pe.signal_id
ORDER BY p.id, pe.seq;
```

## 代码改动范围

| 文件 | 改动 |
|------|------|
| `core/data_store.py` | 新增两张表的建表 SQL、EntryRecord/ClosureRecord 数据类、CRUD 方法、归因查询方法 |
| `exchange/paper_trading.py` | _update_position: 开仓/加仓时写入 entry; close_position: 全平时 FIFO 扣减 entries 写入 closures; partial_close_position: 部分平时 FIFO 扣减 entries 写入 closures; update_prices: 止损触发时走 close_position 流程 |
| `main.py` | _handle_signal: 传递 signal_id 到 paper_engine 的各方法（已有部分传递，需补全） |

**不改动**: 策略代码、风控代码、配置系统、K 线管理器。

## 边界情况

1. **加仓后再加仓（3+ 条 entry）**: FIFO 规则自然处理，按 seq 顺序扣减
2. **部分平仓跨越多条 entry**: 依次扣减，每条 entry 产生独立的 closure 记录
3. **同一 entry 多次部分平仓**: 产生多条 closure 记录，互不覆盖
4. **止损触发（非信号触发）**: closure 的 signal_id 为 NULL，reason 为 "stop_loss_close"
5. **程序重启恢复**: entries 和 closures 从数据库恢复，remaining_qty 已持久化
6. **双向持仓**: entries 挂在 position_id 上，不同方向的 position 有各自的 entries，互不干扰
