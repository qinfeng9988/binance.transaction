## 上下文

周期知识当前分散在 `main.py`（`TIMEFRAME_SECONDS`、`PARENT_TIMEFRAME`）、`query_server.py`（`SUPPORTED_TIMEFRAMES`）、`query_sr.py`（argparse `choices`）、`time_utils.py`（`parse_timeframe`）五处，互不同步。`PARENT_TIMEFRAME` 表里已写 `"1d": "1w"`，但 `TIMEFRAME_SECONDS` 无 `"1w"`，导致 `collect_subscriptions` 的守卫 `parent_tf in TIMEFRAME_SECONDS` 静默丢弃 `1w` 父周期订阅。手动查询的允许周期硬编码在两处，且不含 `5m`（量化在交易的周期），也不含 `1w`/`3d`（已补）。

## 目标 / 非目标

**目标：**

- 建立单一周期注册表，作为"哪些周期受支持、各自秒数与父级"的唯一来源。
- 从注册表派生秒数、父级映射、可查询清单，消除复读与不一致。
- 补上周期合法性校验（此前完全缺失）。
- 量化周期（`strategies.yaml`）与手动查询周期（`manual_config.yaml`）分别声明、相互独立。

**非目标：**

- 不改变量化/手动各自的交易与查询语义：手动查询仍以 CLI 兜底直拉 Binance 为主路径。
- 不重写策略过滤逻辑或数据订阅机制。
- 不引入新的 YAML 加载链/热重载（注册表放在代码模块，而非配置文件）。

## 决策

1. **注册表放 `time_utils.py`（代码模块而非 YAML）**：`time_utils` 已是"时间"域的家（`parse_timeframe` / `bar_close_time`），且 `bar_close_time` 已被 `backtest_runner.py` 消费。放入代码模块无需触及配置加载链、热重载、跨线程。

2. **`PERIODS` 结构**：`dict[str, tuple[int, str | None]]`，即 `周期名 → (秒数, 父级周期)`。父级链：`30m→1h`、`1d→1w`、`3d→1w`、`1w→None`（终端）。完整注册表：

   ```
   1m(60,15m)  3m(180,1h)  5m(300,1h)  15m(900,1h)  30m(1800,1h)
   1h(3600,4h)  4h(14400,1d)  1d(86400,1w)  3d(259200,1w)  1w(604800,None)
   ```

3. **派生函数**：`timeframe_seconds(name)`、`parent_timeframe(name)`、`supported_timeframes()`、`is_valid_timeframe(name)`。所有对"周期合法性"的判断统一走 `is_valid_timeframe`。

4. **后向兼容**：`main.py` 保留 `TIMEFRAME_SECONDS` / `PARENT_TIMEFRAME` 两个模块级名字，但从注册表派生（`TIMEFRAME_SECONDS = {n: sec for n,(sec,_) in PERIODS.items()}`；`PARENT_TIMEFRAME = {n: p for n,(_,p) in PERIODS.items() if p}`）。`scripts/backtest_runner.py`、`sol_*` 等 `from quant_trader.main import PARENT_TIMEFRAME` 无需改动。

5. **手动查询允许清单**：`manual_config.yaml` 新增 `query_timeframes: [5m, 15m, 30m, 1h, 4h, 1d, 3d, 1w]`（含 `5m`，保留此前 `SUPPORTED_TIMEFRAMES` 已含 `5m` 的行为）；`query_server` 与 `query_sr` 的允许清单从它生成，并对注册表校验。缺失或空时回退到 `time_utils.DEFAULT_QUERY_TIMEFRAMES`（同一份 8 周期默认清单）。

6. **周期校验**：`config_loader._validate_strategies` 增加"策略 timeframes ∈ 注册表"检查，非法即抛错（对齐现有 `name`/`mode` 校验失败即拒载的行为）；手动配置的 `query_timeframes` 同样校验。

7. **`30m` 父级 = `1h`**：这是探索阶段我提的默认（下一级周期），用户未异议，作为默认落地。

## 风险 / 权衡

- **`parse_timeframe` 与注册表边界**：`parse_timeframe` 是通用正则（接受任意 `\d+` + m/h/d/w，如 `2h`、`45m`），与固定集合的注册表并存。需明确二者职责：注册表 = "程序认可的合法周期集合"；`parse_timeframe` = "任意间隔的通用解析"。`backtest_runner` 的 `bar_close_time` 依赖 `parse_timeframe` 而非注册表，不受影响。
- **`SUPPORTED_TIMEFRAMES` 语义变化**：从"模块级硬编码常量"变为"从手动配置生成"。`query_server` 在请求处理里已有 `_load_manual_config()` 调用（当前位置在周期校验之后），实现时需把读取时序理顺或提供模块级默认回退。
- **`1w` 进入秒数表后**：`collect_subscriptions` 将不再丢弃 `1d→1w`。当前无 `1d` 策略，无实盘影响；若未来上 `1d` 策略，会开始订阅 `1w` K 线（ccxt/Binance 支持 `1w`），需留意订阅量与限频。
- **校验增强可能让现有配置加载失败**：若现有 `strategies.yaml` 存在未被察觉的非法周期，`_validate_strategies` 会从"静默通过"变为"拒绝加载"。实现前需先核对现有配置无非法周期（当前已知均为 `5m`/`15m`/`1h`）。