## 上下文

回测系统复用 `PaperTradingEngine`（为实时交易设计），引擎内所有时间取 `datetime.now()`。回测时这些时间变为回测运行时刻（墙钟时间），导致 entries/closures/signals/orders/positions 的时间列对复盘无意义。

`Signal` 模型通过 `metadata` dict 传递策略指标（quality_score、ADX 等），但这些数据未落库，无法做指标分桶归因分析。

当前表结构：`position_entries` 有 create_time/update_time 但无业务时间 `entry_time`；`position_entry_closures` 仅有 close_time（墙钟），无 create_time/update_time。

## 目标 / 非目标

**目标：**
- 回测时所有业务时间使用 K 线收盘时间（= kline timestamp + timeframe duration）
- signals 表新增 12 个指标字段，从 metadata 提升为显式列
- entries 表加 entry_time 业务时间列
- closures 表加 create_time/update_time 列，close_time 改显式传值
- Signal.timestamp 时区统一为 aware UTC（修复 naive datetime.utcnow 偏移）
- 归因分析支持按指标分桶统计 PNL 和胜率
- 生产库 ALTER 加列保留数据，回测库删除重建

**非目标：**
- 不修复历史回测数据（回测库删了重跑）
- 不修改生产库已有数据的时间值（实盘时间本身就是真实时间，无需修正）
- 不重构 metadata dict 的整体设计（渐进式迁移，本次只提升 12 个字段）
- 不给 Bollinger 策略强加不适用的指标（字段可空即可）

## 决策

### D1: 时钟注入方案 —— 引擎层 current_time

**选择**：给 `PaperTradingEngine` 增加 `current_time` 属性，内部 `_now()` 方法根据是否设置 current_time 返回不同值。

**替代方案**：
- A) 仅在 backtest_runner 里 patch signal.timestamp → 不够，止损/强平没有信号上下文
- B) 给每个方法加 bar_time 参数 → 改动面太大，且容易遗漏

**理由**：止损触发（update_prices）和 END_OF_DATA 强平由引擎内部发起，不经过 signal_router，没有信号可 patch。必须在引擎层维护当前业务时间。

```python
class PaperTradingEngine:
    def __init__(self, ...):
        self._current_time: datetime | None = None

    def set_current_time(self, bar_close_time: datetime):
        self._current_time = bar_close_time

    def _now(self) -> datetime:
        if self._current_time is not None:
            return self._current_time
        return datetime.now(tz=timezone.utc)
```

实盘不调用 `set_current_time`，`_now()` 行为与原来完全一致。

### D2: bar close time 计算

**选择**：`bar_close = kline["timestamp"] + timeframe_delta`，其中 timeframe_delta 从周期字符串解析（如 "5m" → 5 分钟）。

**理由**：klines.timestamp 是 bar 开盘时间；信号在 bar 收盘后评估（on_bar 拿到的是已完成 K 线），成交价 = close，语义一致。

### D3: EntryRecord / ClosureRecord 时间字段

**选择**：
- `EntryRecord` 新增 `entry_time: datetime` 字段，INSERT 显式传值
- `ClosureRecord` 新增 `close_time: datetime` 字段，INSERT 显式传值

**DDL 变更**：
- `position_entries`: 加 `entry_time TIMESTAMPTZ NOT NULL DEFAULT NOW()`，INSERT 语句加入 entry_time
- `position_entry_closures`: 加 `create_time TIMESTAMPTZ NOT NULL DEFAULT NOW()` 和 `update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()`；close_time 改 INSERT 显式传值

**理由**：entry_time 是业务时间（入场 bar 收盘时刻），create_time 是记录写入时间（审计用），两者语义不同，必须分开。

### D4: Signal 模型新增 12 个显式可选字段

**选择**：Signal dataclass 新增 12 个 `= None` 的可选字段，策略构造 Signal 时显式赋值。

**替代方案**：继续用 metadata，insert_signal 时提取 → 类型不安全、隐式，且 metadata 没有结构约束

**理由**：显式字段类型清晰、IDE 补全友好、insert_signal SQL 直接对齐。

### D5: 字段名 parent_trend 而非 parent_bias

**选择**：字段名用 `parent_trend`，与策略代码中的变量名一致，语义更直接（上级周期趋势方向）。

### D6: 生产库迁移策略

**选择**：ALTER TABLE ADD COLUMN（默认 NULL），不删不修历史数据。

**理由**：生产库的 entries/closures/signals 时间是真实交易时间（误差秒级），无需修正。新写入走新逻辑，旧数据字段为 NULL 可接受。

## 风险 / 权衡

- [改动面大] paper_trading.py 多处 `datetime.now()` → `self._now()`，遗漏会导致个别时间仍为墙钟 → 逐个 grep 替换 + 代码审查
- [时区一致性] Signal 模型原来用 naive `datetime.utcnow`，统一改 aware UTC 后 psycopg2 行为变化 → 测试验证
- [Bollinger 策略字段缺失] 大部分新字段 Bollinger 不产出（无 quality_score、ADX 等），字段为 NULL → 可接受，归因分桶时自动跳过 NULL
- [回测库重建耗时] 删数据重跑需要时间 → klines 保留（缓存复用），只删交易数据

## 迁移计划

1. 回测库：`TRUNCATE signals, orders, positions, position_entries, position_entry_closures, filter_stats`（保留 klines），然后重跑回测
2. 生产库：通过 `data_store._migrate()` 自动 ALTER TABLE ADD COLUMN，现有数据字段为 NULL，无需回填
3. 代码部署后，新写入数据自动使用新逻辑（业务时间 + 新字段）
