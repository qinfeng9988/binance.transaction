## ADDED Requirements

### 需求:风控参数必须支持冷静期配置
系统必须在 `risk.yaml` 中支持连续亏损冷静期配置，触发后一定时间内拒绝新开仓信号。

#### 场景:连续亏损触发冷静期
- **当** `risk.yaml` 配置 `max_consecutive_losses: 3` 和 `cooldown_duration_seconds: 3600`，且回测或实盘中连续亏损达 3 次
- **那么** 系统在接下来 3600 秒内拒绝所有新开仓信号（反向平仓和加仓不受限）

#### 场景:冷静期配置默认值
- **当** `risk.yaml` 未配置 `max_consecutive_losses` 或 `cooldown_duration_seconds`
- **那么** 系统必须使用默认值（max_consecutive_losses=3, cooldown_duration_seconds=3600），不报错

### 需求:风控参数必须支持总风险上限配置
系统必须在 `risk.yaml` 中支持总风险上限配置，所有持仓的潜在最大亏损占余额比例不得超过此值。

#### 场景:总风险超限拒绝新开仓
- **当** `risk.yaml` 配置 `max_total_risk_pct: 8.0`，且当前所有 open positions 的总风险占比已达 8%
- **那么** 系统必须拒绝下一笔新开仓信号

#### 场景:加仓降级规则
- **当** 连续亏损次数达到 `max_consecutive_losses` 阈值
- **那么** 系统必须将加仓上限降低 1 级（即 max_additions - 1），最低不低于 0

### 需求:RiskManager 必须从配置读取所有参数化指标
系统必须让 `RiskManager` 从 `risk.yaml` 读取冷静期、加仓降级、总风险上限等参数，禁止在引擎代码中硬编码这些值。

#### 场景:风控参数化读取
- **当** 实盘 paper 模式或回测启动
- **那么** `RiskManager` 必须从 `risk.yaml` 加载所有参数，引擎代码（`PaperTradingEngine`、`BacktestKlineDriver`、信号路由函数）不得出现这些魔法数字

#### 场景:回测与实盘共享风控配置
- **当** 用户运行回测和实盘 paper 模式
- **那么** 两者必须读取同一份 `risk.yaml`，确保风控行为一致
