## Context

当前系统存在两套交易引擎：`PaperTradingEngine`（实盘 paper 模式）和 `BacktestEngine`（回测脚本内嵌）。两者在杠杆 PNL 计算、风控、资金管理、归因支持等方面存在显著差异，导致回测结果无法真实反映实盘表现。7 月 31 日上线的交易归因功能（FIFO entry/closure）仅通过 `PaperTradingEngine` 落地，回测中无法验证和利用归因数据优化策略。

核心约束：
- 生产代码（`PaperTradingEngine`、`DataStore`、`main.py`）必须零侵入
- 回测 DB 与生产 DB 隔离，K 线表在回测 DB 内共享复用
- 多组参数实验数据通过 `run_id` 共存于同一回测 DB

## Goals / Non-Goals

**Goals:**
- 回测与实盘使用同一交易引擎（`PaperTradingEngine`），消除行为差异
- 回测数据写入独立 DB（`db_backtest`），支持归因查询和策略优化
- 多组参数实验通过 `run_id` 隔离，K 线表跨 run_id 复用
- 回测报告包含归因指标（按 reason / entry_seq / 方向聚合）
- 风控参数（冷静期、总风险上限等）从硬编码移至配置文件

**Non-Goals:**
- 不修改生产 DB 表结构或生产代码逻辑
- 不实现回测参数自动优化（网格搜索等），保留手动指定参数
- 不实现回测结果可视化 UI
- 不支持回测与生产 DB 跨库查询

## Decisions

### D1: 统一引擎 — 废弃 BacktestEngine，复用 PaperTradingEngine

**选择**: 废弃 `BacktestEngine`，回测循环直接调用 `PaperTradingEngine` 的 `place_order()` / `close_position()` / `partial_close_position()` / `update_prices()` / `update_stop_loss()`

**替代方案**:
- 保留 BacktestEngine 并补齐归因 → 两套世界持续存在，维护成本翻倍
- 让 BacktestEngine 继承 PaperTradingEngine → 继承关系脆弱，PaperTradingEngine 不是为继承设计的

**理由**: 回测和实盘的区别应只是 K 线数据来源不同，交易引擎本身必须统一。`PaperTradingEngine` 已包含完整的归因逻辑（`_close_entries_fifo`）、资金管理、手续费计算，直接复用最可靠。

### D2: 数据层隔离 — BacktestDataStore 继承 DataStore

**选择**: 新增 `BacktestDataStore(DataStore)`，覆盖写入方法注入 `run_id`，覆盖查询方法加 `run_id` 过滤。生产 `DataStore` 完全不感知 `run_id`。

**替代方案**:
- 在 DataStore 中加 `if run_id` 分支判断 → 污染生产代码，违反开闭原则
- 用独立 SQL 模块共享查询 → 增加间接层，维护困难
- 每次回测建独立 schema/database → K 线表无法复用

**理由**: 继承覆盖是最干净的隔离方式。生产代码零修改，回测扩展集中在 `BacktestDataStore` 一个类中。K 线表在同一 `db_backtest` database 内共享，避免数据冗余。

### D3: DB 隔离方式 — 同一 PG 实例，新增 db_backtest database

**选择**: 在同一 PostgreSQL 实例中创建 `db_backtest` database，回测脚本连接此 DB。

**替代方案**:
- 独立 PG 实例 → 运维成本高
- 同 database 不同 schema → 跨 schema 查询复杂，且 schema 概念不通用
- 同 database 同 schema 加 run_id → 生产表被污染

**理由**: 同实例不同 database 是最简单的隔离方式，连接配置只需改 dbname，无跨库依赖。

### D4: run_id 格式与生命周期

**选择**: `run_id = bt_{YYYYMMDD_HHMMSS}_{symbol}_{timeframe}_{param_hash8}`，例如 `bt_20260812_143000_BTC-USDT_5m_a3f2e1d8`。回测脚本启动时生成，贯穿整个回测生命周期。

**需带 run_id 的表**: positions, orders, signals, position_entries, position_entry_closures, filter_stats
**不带 run_id 的表**: klines（共享只读数据）

**filter_stats 唯一键调整**: 生产 DB 为 `(strategy_name, symbol, timeframe, direction, filter_type)`，回测 DB 为 `(run_id, strategy_name, symbol, timeframe, direction, filter_type)`

### D5: positions 表隐式唯一性处理

**问题**: `update_position()` 使用 `WHERE symbol = %s AND status = 'open'`，假设同一 symbol 只有一个 open position。回测 DB 中多 run_id 共存会打破此假设。

**选择**: `BacktestDataStore` 覆盖 `update_position()`、`get_open_positions()`、`get_position_count()`，所有 WHERE 子句加 `AND run_id = %s`。

**需覆盖的完整方法清单**:
- `update_position()` — WHERE 加 run_id
- `get_open_positions()` — WHERE 加 run_id
- `get_position_count()` — WHERE 加 run_id
- `open_position()` — INSERT 注入 run_id
- `close_position()` — 通过 position_id 定位，无需改 WHERE，但建表时已含 run_id
- `insert_order()` — INSERT 注入 run_id
- `insert_signal()` — INSERT 注入 run_id
- `insert_entry()` — INSERT 注入 run_id
- `insert_closure()` — INSERT 注入 run_id
- `upsert_filter_stats()` — 唯一键含 run_id，WHERE 加 run_id
- `get_filter_stats()` — WHERE 加 run_id
- `get_attribution_by_signal()` — WHERE 加 run_id
- `get_attribution_by_position()` — WHERE 加 run_id
- `get_attribution_by_time_range()` — WHERE 加 run_id

### D6: 信号处理逻辑复用

**选择**: 从 `main.py` 的 `_handle_signal()` 抽取核心逻辑为独立函数 `handle_signal(signal, paper_engine, data_store, risk_manager, signal_id=None)`，`main.py` 和回测脚本共用。

**替代方案**:
- 回测脚本重新实现信号路由 → 逻辑重复，容易不一致
- 让回测脚本直接调用 `Engine._handle_signal()` → 绑定 Engine 实例，过重

**理由**: 信号路由是"开仓/平仓/部分平仓/止损更新"的分支逻辑，与数据来源无关，应复用。

### D7: 风控参数化

**选择**: 将以下硬编码参数移至 `risk.yaml`:
- `max_consecutive_losses` (默认 3) → 触发冷静期
- `cooldown_duration_seconds` (默认 3600) → 冷静期时长
- `max_total_risk_pct` (默认 8.0) → 总风险上限

`RiskManager` 从配置读取这些参数，回测和实盘共享同一份 `risk.yaml`。

### D8: 归因分析脚本架构

**选择**: `scripts/analyze_attribution.py` 直连 `db_backtest`，创建 `BacktestDataStore` 实例调用归因方法，不依赖主进程。

**替代方案**:
- 请求主进程 QueryServer API → 主进程连生产 DB，查不到回测数据
- 在脚本中写独立 SQL → 与 BacktestDataStore 重复

**理由**: 直连 DB + 复用 `BacktestDataStore` 方法，SQL 集中维护，不重复，不依赖主进程。

### D9: 回测 K 线驱动

**选择**: 新增 `BacktestKlineDriver`，从 `db_backtest.klines` 读取历史数据，逐根推进模拟 closed 事件。包含 parent/grandparent 周期对齐逻辑（复用现有 `_align_parent_klines` 算法）和 funding rate JSON 缓存匹配。

**理由**: K 线驱动是回测与实盘唯一应该不同的地方。将此逻辑封装为独立模块，回测脚本只需遍历驱动器产出的事件。

## Risks / Trade-offs

- **[positions 隐式唯一性遗漏]** → 完整列出所有需覆盖的方法，逐个验证 WHERE 子句。遗漏任何一个都会导致多 run_id 数据互相污染
- **[BacktestDataStore SQL 维护]** → 基类 DataStore 的 SQL 变更时，子类覆盖方法需同步更新。缓解：添加集成测试验证覆盖方法与基类行为一致（除 run_id 外）
- **[回测速度下降]** → 写 DB 比纯内存慢。缓解：回测 DB 在本地 PG 实例，网络延迟极低；批量写入可优化
- **[run_id 管理复杂度]** → 用户需记住 run_id 才能查询归因。缓解：回测结束自动打印 run_id；归因分析脚本支持 `--latest` 查最近一次
- **[废弃旧脚本 BREAKING]** → 用户需迁移到新入口。缓解：新入口参数兼容旧脚本；README 更新迁移说明
