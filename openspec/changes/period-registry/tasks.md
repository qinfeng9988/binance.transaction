## 1. 建立周期注册表

- [x] 1.1 在 `time_utils.py` 定义 `PERIODS` 注册表（补齐 `30m`/`3d`/`1w`；父级链 `30m→1h`、`1d→1w`、`3d→1w`、`1w` 终端）
- [x] 1.2 实现派生函数 `timeframe_seconds` / `parent_timeframe` / `supported_timeframes` / `is_valid_timeframe`
- [x] 1.3 更新模块 docstring 与 `parse_timeframe` docstring，注明注册表是合法周期集合、`parse_timeframe` 是通用解析

## 2. main.py 去手抄并对齐

- [x] 2.1 `TIMEFRAME_SECONDS` / `PARENT_TIMEFRAME` 改为从 `PERIODS` 派生（保留模块级名字，后向兼容）
- [x] 2.2 修复 `collect_subscriptions` 中 `1d→1w` 被 `TIMEFRAME_SECONDS` 守卫静默丢弃的问题（`1w` 入秒数表后守卫自然放行）

## 3. 手动侧允许清单单源化

- [x] 3.1 `manual_config.yaml` 新增 `query_timeframes` 列表（`5m`/`15m`/`30m`/`1h`/`4h`/`1d`/`3d`/`1w`，保留此前含 `5m` 的行为）
- [x] 3.2 `query_server.py` 的允许清单从 `manual_config.query_timeframes` 生成（缺失时回退 `DEFAULT_QUERY_TIMEFRAMES`）
- [x] 3.3 `query_sr.py` 的 argparse `choices` 从同一来源生成

## 4. 周期合法性校验

- [x] 4.1 `config_loader._validate_strategies` 校验每个策略 timeframes ∈ 注册表，非法时报错
- [x] 4.2 手动配置加载时校验 `query_timeframes` ∈ 注册表

## 5. 测试与回归

- [x] 5.1 新增 `tests/test_time_utils.py` 覆盖注册表派生与 `is_valid_timeframe`
- [x] 5.2 扩展 `tests/test_collect_subscriptions.py` 覆盖 `1d→1w` 订阅
- [x] 5.3 跑全量 pytest，确认无回归（189 passed, 4 deselected）