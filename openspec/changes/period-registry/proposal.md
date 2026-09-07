## 为什么

量化自动交易与手动交易辅助两套程序，对"交易周期"的知识目前散落在 5 个互不同步的地方：

| 位置 | 内容 | 问题 |
|------|------|------|
| `main.py` `TIMEFRAME_SECONDS` | 秒数映射 | 缺 `30m`/`3d`/`1w` |
| `main.py` `PARENT_TIMEFRAME` | 父级周期映射 | 缺 `30m`/`3d` 条目；`1d→1w` 存在但被秒数表守卫静默丢弃 |
| `query_server.py` `SUPPORTED_TIMEFRAMES` | 手动查询门卫 | 硬编码 |
| `query_sr.py` argparse `choices` | 手动查询门卫 | 与上一处复读 |
| `time_utils.py` `parse_timeframe` | 通用周期解析 | docstring 未含 `3d`，与注册表脱节 |

由此导致三个实际问题：

- 手动交易需要的周期（`1h`/`4h`/`1d`/`3d`/`1w`）无法在量化配置里干净表达，量化配置被迫为手动查询"塞周期"。
- 同一周期（如 `1d`）在引擎快速路径与 CLI 兜底路径下，上级趋势结果不一致——`1w` 父周期因 `TIMEFRAME_SECONDS` 守卫拿不到。
- 配置文件里声明的周期没有任何合法性校验——敲错周期名会静默回退到 300 秒默认值，过滤逻辑静默失效。

需要一个"周期注册表"，统一"程序支持哪些周期、各自秒数与父级周期"，并让量化与手动各自声明自己用的周期、互不影响。

## 变更内容

- 在 `quant_trader/core/time_utils.py` 建立**周期注册表** `PERIODS`（周期名 → 秒数 + 父级周期），补齐此前缺失的 `30m`、`3d`、`1w`；父级链定为：`30m→1h`、`1d→1w`、`3d→1w`、`1w` 终端无父级。
- 提供派生函数 `timeframe_seconds` / `parent_timeframe` / `supported_timeframes` / `is_valid_timeframe`，作为秒数、父级映射、合法性判断的唯一来源。
- `main.py` 的 `TIMEFRAME_SECONDS` / `PARENT_TIMEFRAME` 改为从注册表派生（保留模块级名字，后向兼容）；借此修复 `collect_subscriptions` 中 `1d→1w` 因子周期守卫被静默丢弃的 bug。
- `query_server.py` 的 `SUPPORTED_TIMEFRAMES` 与 `query_sr.py` 的 argparse `choices` 改为从 `manual_config.yaml` 声明的查询周期生成，不再硬编码复读。
- 新增周期合法性校验：`strategies.yaml` / `manual_config.yaml` 声明的周期不在注册表时报错（当前 `_validate_strategies` 完全不校验 timeframes）。
- `manual_config.yaml` 新增手动查询周期声明（`query_timeframes`），实现量化周期与手动周期配置分离。

## 功能 (Capabilities)

### 新增功能
- `period-registry`: 程序支持的交易周期宇宙（周期名 → 秒数 + 父级周期）的单一来源，提供秒数/父级/可查询清单派生与合法性校验。

### 修改功能
- `configuration`: 配置校验需对 timeframes 做注册表合法性校验（此前只校验 name/mode，不校验周期）；手动配置新增查询周期声明，与量化周期配置相互独立。

## 影响

- `quant_trader/core/time_utils.py`：新增 `PERIODS` 注册表与派生函数。
- `quant_trader/main.py`：`TIMEFRAME_SECONDS` / `PARENT_TIMEFRAME` 改为派生；修复 `collect_subscriptions`。
- `quant_trader/core/query_server.py`：`SUPPORTED_TIMEFRAMES` 从手动配置生成。
- `quant_trader/query_sr.py`：argparse `choices` 从手动配置生成。
- `quant_trader/core/config_loader.py`：`_validate_strategies` 增加周期校验。
- `quant_trader/config/manual_config.yaml`：新增 `query_timeframes`。
- 后向兼容：`scripts/backtest_runner.py`、`scripts/sol_grid_search.py`、`scripts/sol_period_analysis.py` 仍 `import PARENT_TIMEFRAME` / `TIMEFRAME_SECONDS`，模块级名字保留。