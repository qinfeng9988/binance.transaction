## 需求

### 需求:系统必须提供回测归因分析脚本
系统必须提供 `scripts/analyze_attribution.py` 脚本，直连回测 database，按 `run_id` 查询并展示归因指标，无需启动主进程。脚本必须支持按信号指标字段分桶统计归因 PNL 和胜率。

#### 场景:按 run_id 查询归因
- **当** 用户执行 `python scripts/analyze_attribution.py --run-id bt_xxx`
- **那么** 脚本必须连接回测 database，调用 `BacktestDataStore` 的归因方法，输出该 run_id 的归因报告

#### 场景:查询最近一次回测
- **当** 用户执行 `python scripts/analyze_attribution.py --latest`
- **那么** 脚本必须自动定位最近一次回测的 run_id 并展示其归因报告

#### 场景:多 run_id 横向对比
- **当** 用户执行 `python scripts/analyze_attribution.py --compare bt_xxx bt_yyy bt_zzz`
- **那么** 脚本必须输出对比表，包含每个 run_id 的关键归因指标（按 reason 聚合的 PNL 占比、胜率、平均 PNL 等）

#### 场景:默认输出指标分桶统计
- **当** 用户执行归因分析且未指定 --bucket 参数
- **那么** 脚本必须额外输出按 quality_score、direction_type、is_counter_trend、adx 四个维度的分桶统计

#### 场景:指定分桶字段
- **当** 用户执行 `python scripts/analyze_attribution.py --latest --bucket quality_score`
- **那么** 脚本仅输出 quality_score 分桶统计

### 需求:归因分析必须支持按出场原因聚合
系统必须在归因报告中提供按 `reason` 字段聚合的归因指标，展示不同出场原因对总 PNL 的贡献。

#### 场景:按 reason 聚合归因
- **当** 用户查看归因报告
- **那么** 报告必须包含每个 reason（如 `partial_tp_2.0R`、`stop_loss_close`、`close` 等）的笔数、总 PNL、占比、胜率

### 需求:归因分析必须支持按入场序号聚合
系统必须在归因报告中提供按 entry `seq` 聚合的归因指标，展示首次开仓与各次加仓的 PNL 贡献差异。

#### 场景:按 entry_seq 聚合归因
- **当** 用户查看归因报告
- **那么** 报告必须包含每个 seq（1=首次开仓，2+=加仓）的笔数、总 PNL、平均 PNL、胜率

### 需求:归因分析必须复用 BacktestDataStore 方法
系统必须通过 `BacktestDataStore` 的归因查询方法获取数据，禁止在脚本中重复编写归因 SQL。

#### 场景:SQL 单一来源
- **当** 归因分析脚本需要查询归因数据
- **那么** 必须通过 `BacktestDataStore.get_attribution_by_*()` 系列方法获取，不得在脚本内联 SQL
