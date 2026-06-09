# 支撑/阻力位多周期趋势确认策略

| 属性 | 值 |
|------|-----|
| 策略名称 | `support_resistance` (多策略模式) |
| 策略类型 | 趋势跟踪 + 技术分析 |
| 核心思想 | 在高质量支撑/阻力位附近，经多维度过滤（ADX/RSI/成交量）确认后，顺势交易为主、逆势交易为辅 |
| 适用市场 | 高流动性、有明确趋势的加密货币市场 |
| 交易标的 | BTC/USDT (5m), ETH/USDT (1h), SOL/USDT, PAXG/USDT |
| 当前版本 | v1.3.0 |

## 1. 核心算法

### 1.1 支撑/阻力位识别（质量加权版）

采用三层算法 + 加权评分融合：

```
[波段高低点检测] → [价格聚类] → [成交量分布确认] → [质量加权评分]
```

#### 波段高低点检测（Swing High/Low）
- 对于每根 K 线，比较其左右各 N 根 K 线
- 低点低于左右所有低点 → 波段低点（潜在支撑）
- 高点高于左右所有高点 → 波段高点（潜在压力）
- 默认参数：`left=2, right=2`

#### 价格聚类与密度
- 将波段高低点按价格聚类（容差 0.5%）
- **聚类密度加成**：0.5% 容差内的价格点越多，`density_bonus` 越高（`density × 0.3`，上限 2.0）

#### 成交量分布确认（Volume Profile）
- 将 K 线数据分成 50 个价格区间
- 高成交量节点附近的 S/R 位强度 +1

#### 质量加权评分

每个 S/R 位的最终质量分数：

```
quality_score = strength + vp_bonus + vol_surge_bonus + density_bonus
```

| 加分项 | 条件 | 分值 |
|--------|------|------|
| 基础强度 | 聚类触碰次数 | `strength` |
| 成交量分布 | 位于高成交量节点 ±1% 内 | +1 |
| 成交量爆发 | 最新 K 线量 ≥ 1.5× 前20平均 | +1 |
| 聚类密度 | 0.5% 容差内点数 × 0.3 | 最高 +2.0 |

**实现位置：** `quant_trader/technical/support_resistance.py`

### 1.2 趋势判断（双 EMA 交叉）

| 币种 | 周期 | 快线 | 慢线 | 震荡阈值 | 自身ranging判断 |
|------|------|------|------|---------|----------------|
| BTC | 5m | EMA9 | EMA21 | 0.5% | **跳过**（只看parent 1h） |
| ETH | 1h | EMA21 | EMA50 | 0.5% | **跳过**（只看parent 4h） |

**v1.2.0 重要变更**：1h 和 5m 周期均跳过自身 ranging 判断。原因是短周期EMA快慢线间距通常很小（BTC 5m最大仅0.6-0.8%，ETH 1h也类似），容易被误判为ranging。策略改为只依赖上级周期趋势方向决定交易偏向。

**判断逻辑：**
- `EMA(fast) > EMA(slow)` 且间距 ≥ threshold → **上涨趋势 (uptrend)**
- `EMA(fast) < EMA(slow)` 且间距 ≥ threshold → **下跌趋势 (downtrend)**
- 间距 < threshold → **震荡 (ranging)**（仅用于上级周期判断）

**实现位置：** `quant_trader/strategies/s_r_levels.py:detect_trend()`

### 1.3 上级周期 EMA 锚定

| 当前周期 | 上级周期（parent） | 锚定检查 |
|---------|-------------------|---------|
| 5m | 1h | 1h EMA 同向才允许 5m 信号 |
| 1h | 4h | 4h EMA 同向才允许 1h 信号 |
| 4h | 1d | 1d EMA 同向才允许 4h 信号 |

若上级 EMA 不同向（ranging），策略返回 `bias=neutral`（不交易）。

**v1.2.0 新增**：上级周期 ranging 时也跳过交易。

**实现位置：** `quant_trader/strategies/s_r_levels.py:detect_trend_with_parent()`

### 1.4 ADX 趋势强度过滤

| 币种 | 周期 | ADX 周期 | 最低 ADX |
|------|------|---------|---------|
| BTC | 5m | 14 | 20 |
| ETH | 1h | 14 | 15 |

**逻辑：** 在生成任何信号前，先检查当前周期 ADX。ADX 低于阈值时直接返回空列表，不生成任何信号。

**实现位置：** `quant_trader/strategies/s_r_levels.py:check_adx_strength()`

## 2. 交易逻辑

### 2.1 信号生成流程（v1.2.0 完整版）

```
获取 K 线数据
    ↓
1. ADX 趋势强度检查（ADX >= 阈值？）
    ↓ 否 → 返回空
2. 多周期趋势确认 → 得到 Bias（交易偏向）
    ↓
3. 上级周期 EMA 锚定检查（上级 ranging？→ 跳过）
    ↓
4. 识别高质量支撑/压力位（加权评分）
    ↓
5. 确定各方向入场条件（主方向/副方向）
    ↓
6. 逐层过滤：
    ├── 6a. RSI 动量过滤
    ├── 6b. 成交量爆发确认
    └── 6c. （已关闭：拒绝蜡烛、价格vsEMA、回踩）
    ↓
7. 计算 ATR 动态止损
    ↓
8. 生成 Signal
```

### 2.2 入场条件

#### BTC/USDT (5m) - 做多（Buy）

| 条件 | 主方向 | 副方向 |
|------|--------|--------|
| 最低质量分 | 5.3 | 6.5 |
| 入场距离阈值 | 0.35% | 0.25% |
| RSI(14) | 35 < RSI < 75 | RSI < 45 |
| 成交量爆发 | 量比 >= 1.5× | 量比 >= 1.6× |

#### BTC/USDT (5m) - 做空（Sell）

| 条件 | 主方向 | 副方向 |
|------|--------|--------|
| 最低质量分 | 5.3 | 6.5 |
| 入场距离阈值 | 0.35% | 0.25% |
| RSI(14) | 25 < RSI < 58 | RSI > 55 |
| 成交量爆发 | 量比 >= 1.5× | 量比 >= 1.6× |

#### ETH/USDT (1h) - 做多（Buy）

| 条件 | 主方向 | 副方向 |
|------|--------|--------|
| 最低质量分 | 4 | 4 |
| 入场距离阈值 | 1.0% | 0.5% |
| RSI(14) | 35 < RSI < 75 | RSI < 45 |
| 成交量爆发 | 量比 >= 1.5× | 量比 >= 1.5× |

#### ETH/USDT (1h) - 做空（Sell）

| 条件 | 主方向 | 副方向 |
|------|--------|--------|
| 最低质量分 | 4 | 4 |
| 入场距离阈值 | 1.0% | 0.5% |
| RSI(14) | 25 < RSI < 58 | RSI > 55 |
| 成交量爆发 | 量比 >= 1.5× | 量比 >= 1.5× |

### 2.3 止损设置（ATR 动态）

```
止损距离 = ATR(14) × atr_multiplier

做多止损 = 入场价 - 止损距离
做空止损 = 入场价 + 止损距离
```

| 币种 | 周期 | ATR倍数 | 典型止损距离 |
|------|------|---------|-------------|
| BTC | 5m | 5.0 | 0.7% - 1.2% |
| ETH | 1h | 2.8 | 1.0% - 1.5% |

当 ATR 数据不足时，回退到固定百分比（BTC `stop_loss_pct=2.2%`，ETH `stop_loss_pct=1.5%`）。

**v1.2.0 回测引擎新增**：同方向最多加仓 **1 次**（最多 2 层仓位），避免仓位无限累积导致重仓亏损。

**实现位置：** `quant_trader/strategies/s_r_levels.py:compute_dynamic_stop_loss()`

### 2.4 出场条件

| 类型 | 条件 | 说明 |
|------|------|------|
| 止损 | 价格触及 ATR 动态止损位 | 由策略生成信号时设定，引擎自动监控 |
| 反向信号 | 持有多仓时收到 sell / 持有空仓时收到 buy | 先平仓再反向开仓 |
| 同向加仓 | 同方向新信号（最多1次） | 以新价格加仓，更新持仓均价 |

## 3. 策略参数

### 3.1 BTC/USDT 专用策略

```yaml
# quant_trader/config/strategies.yaml
# 策略名: support_resistance_btc

params:
  # S/R 检测参数
  lookback: 100
  min_touches: 5.3
  stop_loss_pct: 2.2
  leverage: 5
  entry_threshold_pct: 0.35
  secondary_min_touches: 4
  secondary_entry_threshold_pct: 0.25
  trend_bonus_aligned: 1.3
  trend_bonus_counter: -0.5

  # 趋势过滤参数
  require_parent_ema_aligned: true
  ema_fast_period: 9
  ema_slow_period: 21
  ema_gap_threshold_pct: 0.5
  adx_period: 14
  adx_min: 20.0
  adx_min_high_quality: 17.0
  di_tolerance: 2.0
  quality_threshold_for_lower_adx: 5.5
  price_vs_ema_filter: false

  # 动量/波动参数
  rsi_period: 14
  rsi_long_min: 35.0
  rsi_long_max: 75.0
  rsi_short_min: 25.0
  rsi_short_max: 58.0
  rsi_countertrend_long_max: 45.0
  rsi_countertrend_short_min: 55.0
  volume_burst_ratio: 1.5
  volume_burst_lookback: 20

  # 逆势交易额外过滤
  secondary_min_quality: 6.5
  secondary_volume_ratio: 1.6

  # ATR 动态止损
  use_atr_stop: true
  atr_period: 14
  atr_multiplier: 5.0

  # 拒绝蜡烛 / 回踩确认
  require_rejection_candle: false
  require_pullback_retest: false
  pullback_lookback: 10
```

### 3.2 ETH/USDT 专用策略

```yaml
# 策略名: support_resistance_eth

params:
  # S/R 检测参数
  lookback: 100
  min_touches: 4
  stop_loss_pct: 1.5
  leverage: 5
  entry_threshold_pct: 1.0      # 1h 周期价格偏离范围更大
  secondary_min_touches: 4
  secondary_entry_threshold_pct: 0.5

  # 趋势过滤参数
  require_parent_ema_aligned: true
  ema_fast_period: 21           # 1h 周期使用更长EMA
  ema_slow_period: 50
  ema_gap_threshold_pct: 0.5
  adx_period: 14
  adx_min: 15.0                 # 1h 周期信号更少，降低阈值
  price_vs_ema_filter: false

  # 动量/波动参数
  rsi_period: 14
  rsi_long_min: 35.0
  rsi_long_max: 75.0
  rsi_short_min: 25.0
  rsi_short_max: 58.0
  rsi_countertrend_long_max: 45.0
  rsi_countertrend_short_min: 55.0
  volume_burst_ratio: 1.5
  volume_burst_lookback: 20

  # ATR 动态止损
  use_atr_stop: true
  atr_period: 14
  atr_multiplier: 3.5

  # 拒绝蜡烛 / 回踩确认
  require_rejection_candle: false
  require_pullback_retest: false
  pullback_lookback: 10
```

## 4. 多策略架构

### 4.1 配置文件结构

`quant_trader/config/strategies.yaml` 支持多个独立策略配置：

```yaml
strategies:
  - name: "support_resistance_btc"
    enabled: true
    symbols: ["BTC/USDT"]
    timeframes: ["5m", "1h", "4h"]
    params: { ... }

  - name: "support_resistance_eth"
    enabled: true
    symbols: ["ETH/USDT"]
    timeframes: ["5m", "1h", "4h"]
    params: { ... }
```

### 4.2 策略注册表

`quant_trader/main.py` 中策略名称映射到策略类：

```python
strategy_registry = {
    "support_resistance": SupportResistanceStrategy,
    "support_resistance_btc": SupportResistanceStrategy,
    "support_resistance_eth": SupportResistanceStrategy,
    "support_resistance_sol": SupportResistanceStrategy,
    "support_resistance_paxg": SupportResistanceStrategy,
}
```

多个策略名称可映射到同一策略类，通过不同参数实现差异化交易。

### 4.3 回测脚本

```bash
# BTC 回测
python scripts/backtest.py --symbol BTC/USDT --timeframe 5m --days 60

# ETH 回测（自动匹配 support_resistance_eth）
python scripts/backtest.py --symbol ETH/USDT --timeframe 1h --days 60

# 指定策略名称回测
python scripts/backtest.py --symbol ETH/USDT --timeframe 1h --days 60 --strategy support_resistance_eth
```

## 5. 风控集成

| 风控项 | 策略内实现 | 外部风控接管 | 说明 |
|--------|-----------|-------------|------|
| ADX 趋势强度 | **是** | 否 | 策略前置过滤 |
| 上级 EMA 锚定 | **是** | 否 | 策略前置过滤 |
| 上级 ranging 过滤 | **是** | 否 | v1.2.0 新增 |
| RSI 动量 | **是** | 否 | 策略前置过滤 |
| 成交量爆发 | **是** | 否 | 策略前置过滤 |
| ATR 动态止损 | **是** | 检查 | 策略设定，风控检查止损距离 |
| 同向加仓限制 | **是** | 否 | v1.2.0 回测引擎：最多加仓1次 |
| 杠杆设定 | 建议值 | **限制** | 策略请求，风控按币种上限调整 |
| 单笔亏损上限 | 否 | **是** | 风控检查 |
| 日累计亏损 | 否 | **是** | 风控检查 |
| 最大持仓数 | 否 | **是** | 风控检查 |
| 信号冷却期 | **是** | 否 | 引擎层 220 秒同币种冷却 |

## 6. 回测结果

### 6.1 BTC/USDT (5m)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | 盈亏比 | Profit Factor |
|---------|--------|---------|---------|------|--------|--------------|
| 30天 | **+6.70%** | 3.49% | 5 | 40.0% | 10.59 | **7.06** |
| 60天 | **+5.09%** | 3.79% | 13 | 38.5% | 4.21 | **2.63** |

**策略特征**：止损放宽后盈亏比和胜率同步提升。盈利单能跑很远（单笔最大+7,693），亏损单被截断但空间更合理。60天覆盖震荡+趋势两个阶段，回撤控制在4%以内。

### 6.2 ETH/USDT (1h)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | 盈亏比 | Profit Factor |
|---------|--------|---------|---------|------|--------|--------------|
| 30天 | +5.36% | 3.57% | 7 | 42.9% | 2.98 | 2.24 |
| 60天 | +3.28% | 6.14% | 11 | 45.5% | 1.72 | 1.43 |

**策略特征**：盈亏比低于BTC（1.7-3.0倍），胜率略高。ETH 1h 在额外30天（4月）表现弱于5月，但总体仍盈利。

### 6.3 SOL/USDT / PAXG/USDT

| 币种 | 状态 | 说明 |
|------|------|------|
| SOL | 未优化 | 参数待调整，波动特性与BTC/ETH不同 |
| PAXG | 未优化 | 低波动品种，可能需要更紧的止损和更低的ADX阈值 |

## 7. 已知问题与 TODO

- [x] **多策略模式**：v1.2.0 已实现，不同币种使用不同参数配置
- [x] **1h 周期 ranging 跳过**：v1.2.0 已修复，1h 和 5m 一样跳过自身 ranging 判断
- [x] **加仓限制**：v1.2.0 回测引擎新增同方向最多加仓1次
- [ ] **SOL/USDT 参数优化**：需单独测试和调参
- [ ] **PAXG/USDT 参数优化**：低波动品种可能需要完全不同的逻辑
- [ ] **4h 周期回测**：当前主要验证 5m/1h，4h 长周期尚未充分测试
- [ ] `identify_support_resistance` 被调用两次（做多/做空各一次），可优化为单次调用
- [ ] **ADX numpy 回退**：纯 numpy ADX 实现较复杂，建议安装 TA-Lib 以获得更精确的结果

## 8. 变更历史

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|---------|--------|
| v1.3.0 | 2025-06-03 | BTC 止损放宽+质量优化：①ATR倍数3.5→5.0 ②stop_loss_pct 1.5→2.2 ③min_touches 4→5.3 ④entry_threshold_pct 0.5→0.35 ⑤secondary_min_quality 6.5 ⑥trend_bonus_counter -0.5 ⑦冷却期300→220秒 ⑧BTC 5m验证60天+5.09% / 30天+6.70% | jiangfeng.qin |
| v1.2.0 | 2025-06-02 | 多策略模式：①BTC/ETH分策略配置 ②1h周期跳过ranging判断 ③回测引擎限制加仓1次 ④ATR倍数1.5→3.5 ⑤成交量爆发1.2→1.5 ⑥BTC 5m策略验证(+3.36%/60天) ⑦ETH 1h策略验证(+3.28%/60天) | jiangfeng.qin |
| v1.1.1 | 2025-05-28 | 紧急修复：①EMA计算顺序bug修复 ②过滤参数大幅放宽：ADX 22→18、RSI区间放宽、成交量爆发1.8→1.2、关闭price_vs_ema过滤和回踩确认 | jiangfeng.qin |
| v1.1.0 | 2025-05-27 | 全面升级：ADX趋势过滤、RSI动量过滤、ATR动态止损、成交量爆发确认、拒绝蜡烛、回踩确认、S/R质量加权评分、上级EMA锚定 | jiangfeng.qin |
| v1.0.0 | 2025-05-21 | 初始版本：基础 S/R + 双EMA趋势 + 多周期确认 | jiangfeng.qin |
