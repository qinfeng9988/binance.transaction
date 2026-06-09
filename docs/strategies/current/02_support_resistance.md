# 支撑/阻力位多周期趋势确认策略

| 属性 | 值 |
|------|-----|
| 策略名称 | `support_resistance` (多策略模式) |
| 策略类型 | 趋势跟踪 + 技术分析 |
| 核心思想 | 在高质量支撑/阻力位附近，经多维度过滤（ADX/RSI/成交量）确认后，顺势交易为主、逆势交易为辅 |
| 适用市场 | 高流动性、有明确趋势的加密货币市场 |
| 交易标的 | BTC/USDT (5m), ETH/USDT (1h), SOL/USDT (5m), PAXG/USDT |
| 当前版本 | v1.4.2 |

## 1. 核心算法

### 1.1 支撑/阻力位识别（质量加权版 v1.4.0）

采用多层算法 + 加权评分融合：

```
[波段高低点检测] → [Order Block检测] → [FVG检测] → [混合聚类] → [成交量分布确认] → [质量加权评分]
```

#### 波段高低点检测（Swing High/Low）

- 对于每根 K 线，比较其左右各 N 根 K 线
- 低点低于左右所有低点 → 波段低点（潜在支撑）
- 高点高于左右所有高点 → 波段高点（潜在压力）
- 默认参数：`left=2, right=2`
- **自适应窗口**（v1.4.0）：ADX > 25 时用 `left=right=3`，否则用 `left=right=2`

#### Order Block 检测（v1.4.0）

识别趋势反转前的最后一根反向 K 线：
- **Bullish OB**：连续下跌后的反转点，取反转前最后一根 bearish K 线（close < open）的低点作为支撑
- **Bearish OB**：连续上涨后的反转点，取反转前最后一根 bullish K 线（close > open）的高点作为压力

**实现位置：** `quant_trader/technical/support_resistance.py:find_order_blocks()`

#### FVG (Fair Value Gap) 检测（v1.4.0）

识别价格跳空缺口：
- **Bullish FVG**：`klines[i].high < klines[i+2].low`，下跳缺口构成支撑
- **Bearish FVG**：`klines[i].low > klines[i+2].high`，上跳缺口构成压力

**实现位置：** `quant_trader/technical/support_resistance.py:find_fvg()`

#### 混合聚类（v1.4.0）

将三种来源的价格统一聚类（容差 0.5%）：
- 波段高低点价格
- Order Block 价格
- FVG 价格

**混合聚类**（所有来源一起聚类）优于独立聚类，避免不同来源的 S/R 位过度分散。

#### 成交量分布确认（Volume Profile v1.4.0）

将 K 线数据分成 50 个价格区间，返回：
- **POC** (Point of Control)：成交量最大的价格点
- **HVN** (High Volume Nodes)：成交量显著高于平均的节点（volume > mean + 0.5×std）

**分层评分（取代原有统一 +1）：**

| 层级 | 距离条件 | 加分 | 说明 |
|------|---------|------|------|
| POC 近区 | ±0.5% | +2.0 | 最强结构位 |
| POC 远区 | ±1.0% | +1.0 | 强结构位 |
| HVN 近区 | ±0.5% | +1.5 | 高量节点 |
| HVN 远区 | ±1.0% | +0.5 | 高量节点边缘 |
| 普通高量 | ±1.0% | +0.5 | 兜底 |

#### 质量加权评分

每个 S/R 位的最终质量分数：

```
quality_score = strength + vp_bonus + vol_surge_bonus + density_bonus
```

| 加分项 | 条件 | 分值 |
|--------|------|------|
| 基础强度 | 聚类触碰次数 | `strength` |
| 成交量分布 | POC/HVN 分层评分 | +0.5 ~ +2.0 |
| 成交量爆发 | 最新 K 线量 ≥ 1.5× 前20平均 | +1 |
| 聚类密度 | 0.5% 容差内点数 × 0.3 | 最高 +2.0 |

**实现位置：** `quant_trader/technical/support_resistance.py`

### 1.2 趋势判断（双 EMA 交叉）

| 币种 | 周期 | 快线 | 慢线 | 震荡阈值 | 自身ranging判断 |
|------|------|------|------|---------|----------------|
| BTC | 5m | EMA9 | EMA21 | 0.5% | **跳过**（只看parent 1h） |
| ETH | 1h | EMA21 | EMA50 | 0.5% | **跳过**（只看parent 4h） |
| SOL | 5m | EMA9 | EMA21 | 0.5% | **跳过**（只看parent 1h） |

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

**v1.4.0 移除**：多周期共振（grandparent 检查）已移除，因为 100% 的已有交易已满足条件，冗余过滤无实际效果。

**实现位置：** `quant_trader/strategies/s_r_levels.py:detect_trend_with_parent()`

### 1.4 ADX 趋势强度过滤

| 币种 | 周期 | ADX 周期 | 最低 ADX | 高质量放宽 ADX |
|------|------|---------|---------|--------------|
| BTC | 5m | 14 | 18.0 | 15.0 |
| ETH | 1h | 14 | 13.0 | 11.0 |
| SOL | 5m | 14 | 19.0 | 17.0 |
| PAXG | — | 14 | 20.0 | 17.0 |

**逻辑：** 在生成任何信号前，先检查当前周期 ADX。ADX 低于阈值时直接返回空列表，不生成任何信号。

**v1.4.2 结论**：BTC/ETH ADX 阈值分别降至 18/13 后，FILTER SUMMARY 仍显示 93-94% 的 S/R 位被距离过滤拦截，ADX 仅拦截 0-2%。当前阈值已足够宽松，不再继续下调。

**实现位置：** `quant_trader/strategies/s_r_levels.py:check_adx_strength()`

## 2. 交易逻辑

### 2.1 信号生成流程（v1.4.0 完整版）

```
获取 K 线数据
    ↓
1. ADX 趋势强度检查（ADX >= 阈值？）
    ↓ 否 → 返回空
2. 上级周期趋势确认 → 得到 Bias（交易偏向）
    ↓
3. 上级周期 EMA 锚定检查（上级 ranging？→ 跳过）
    ↓
4. 识别高质量支撑/压力位（加权评分，含 OB/FVG）
    ↓
5. 确定各方向入场条件（主方向/副方向）
    ↓
6. 逐层过滤：
    ├── 6a. RSI 动量过滤
    ├── 6b. 成交量爆发确认
    └── 6c. （BTC 启用拒绝蜡烛；ETH/SOL 关闭拒绝蜡烛；价格vsEMA/回踩/多周期共振已关闭）
    ↓
7. 计算 ATR 动态止损
    ↓
8. 加仓条件检查（v1.4.1）
    ├── 8a. 同向持仓存在？
    │   ├── 否 → 新开仓（执行总风险上限检查）
    │   └── 是 → 进入加仓评估
    │       ├── 价格优于持仓价？（做多需更低，做空需更高）
    │       ├── quality_score >= 6.5？（第1次加仓门槛）
    │       ├── add_count < max_additions？（震荡1次/趋势2次）
    │       └── add_count >= 1 时 quality_score >= 6.0？（第2次加仓门槛）
    ↓
9. Funding Rate 过滤（v1.5.0）
    ├── 硬阈值（>+0.10% 或 <-0.10%）→ 直接禁止对应方向
    └── 软阈值（>+0.05% 或 <-0.05%）→ quality_score -1.0，可选降杠杆
    ↓
10. 生成 Signal（metadata 携带 funding_rate）
```

### 2.2 加仓机制（v1.4.1）

#### 分级加仓次数

| 市场状态 | `is_ranging` 判定 | 最大加仓次数 | 总仓位层数 |
|---------|------------------|-------------|-----------|
| 震荡期 | ADX < adx_min **或** trend == "ranging" | 1 | 2 |
| 趋势期 | ADX >= adx_min **且** trend != "ranging" | 2 | 3 |

#### 分级质量门槛

| 加仓次数 | 质量分要求 | 额外条件 |
|---------|-----------|---------|
| 第1次（add_count=0 → 1）| quality_score >= 6.5 | 价格优于持仓价 |
| 第2次（add_count=1 → 2）| quality_score >= 6.0 | 价格优于持仓价 + 趋势期 |

#### 安全机制

1. **总风险上限**：回测引擎计算所有持仓的潜在最大亏损（`|entry - stop_loss| × qty × leverage`），新增仓位前检查总风险 < 余额 8%
2. **连续亏损降级**：连续亏损 >= 3 次后，加仓上限自动降低 1 级（如趋势期 2→1，震荡期 1→0）
3. **价格优于持仓价**：做多时加仓价格必须低于持仓均价，做空时必须高于持仓均价

**v1.4.2 币种差异**：BTC 开启加仓（震荡 1 次 / 趋势 2 次）；ETH 与 SOL 关闭加仓（`max_additions_ranging/trending = 0`），避免 1h 低频率仓位过重及 SOL 高波动风险。

### 2.3 Funding Rate 过滤（v1.5.0）

Funding Rate（资金费率）反映永续合约多空双方的情绪和拥挤度。极端 funding 往往预示挤仓/反转风险，在信号生成前增加 funding rate 过滤可显著减少此类止损。

**关键发现**：Binance 对 BTC/ETH 的 funding rate 有硬性上限（±0.01%），因此原配置的 0.05%/0.10% 阈值永远不可能触发。v1.5.0 最终阈值基于近一年（1095 条）资金费率分位数分析制定。

#### 过滤规则

| 层级 | 触发条件 | 处理方式 | 代码位置 |
|------|---------|---------|---------|
| **硬阈值** | funding > hard_block | **直接禁止该方向整根 K 线** | 步骤 4a，S/R 识别**之前** |
| **软阈值** | funding > penalty | quality_score **-1.0** + 可选降杠杆 | 步骤 5 内部，distance 检查**之后** |

**设计逻辑**：
- 硬阈值在前：极端拥挤期直接关闭方向，不再浪费计算资源检查 S/R 位
- 软阈值在后：price 已靠近 S/R 位后才降级处理，避免过滤掉正常信号

#### 最终阈值配置（基于分位数分析）

| 币种 | 做多软阈值 | 做多硬阈值 | 做空软阈值 | 做空硬阈值 | 杠杆缩减 | 分析依据 |
|------|-----------|-----------|-----------|-----------|---------|---------|
| **BTC** | +0.008% | +0.009% | -0.006% | -0.010% | 0.6（5x→3x） | 正向 80% 分位≈0.008%，交易所上限 0.01% |
| **ETH** | +0.008% | +0.009% | -0.008% | -0.015% | 0.6（5x→3x） | 正向 80% 分位≈0.008%，负向 5% 分位≈-0.015% |
| **SOL** | +0.012% | +0.018% | -0.022% | -0.070% | 0.7（3x→2x） | 正向 90% 分位≈0.01%，负向 10% 分位≈-0.012% |
| **PAXG** | — | — | — | — | — | 默认关闭 |

#### 杠杆降低机制

当 funding 触发软阈值且 `funding_long_leverage_reduction_enabled: true` 时：

```
新杠杆 = max(1, floor(原杠杆 × reduction_factor))
```

| 币种 | reduction_factor | 示例 |
|------|-----------------|------|
| BTC | 0.6 | 5x → 3x |
| ETH | 0.6 | 5x → 3x |
| SOL | 0.7 | 3x → 2x |

#### 币种差异配置（YAML）

```yaml
# BTC/USDT
funding_filter_enabled: true
funding_long_penalty_threshold: 0.008
funding_long_hard_block_threshold: 0.009
funding_short_block_threshold: -0.006
funding_short_hard_block_threshold: -0.010
funding_long_quality_penalty: -1.0
funding_long_leverage_reduction_enabled: true
funding_long_leverage_reduction: 0.6

# ETH/USDT
funding_long_penalty_threshold: 0.008
funding_long_hard_block_threshold: 0.009
funding_short_block_threshold: -0.008
funding_short_hard_block_threshold: -0.015
funding_long_leverage_reduction: 0.6

# SOL/USDT
funding_long_penalty_threshold: 0.012
funding_long_hard_block_threshold: 0.018
funding_short_block_threshold: -0.022
funding_short_hard_block_threshold: -0.070
funding_long_leverage_reduction: 0.7

# PAXG/USDT
funding_filter_enabled: false
```

#### 实现位置

| 功能 | 文件 | 说明 |
|------|------|------|
| 实时数据获取 | `exchange/binance_adapter.py:fetch_funding_rate()` | ccxt 获取当前 funding rate |
| 历史数据缓存 | `scripts/fetch_funding_history.py` | 预拉取 JSON 缓存，供回测使用 |
| 引擎缓存 | `main.py:_get_funding_rate()` | 60 秒 TTL，每周期复用 |
| 硬阈值过滤 | `strategies/s_r_levels.py` 步骤 4a | 设置 `can_buy=False` / `can_sell=False` |
| 软阈值过滤 | `strategies/s_r_levels.py` 步骤 5 | `quality_score -= 1.0`，可选降杠杆 |
| 回测加载 | `scripts/backtest_range.py` | `_load_funding_rates()` + `_match_funding_rate()` |

#### 日志输出示例

```
[FR过滤] BTC/USDT 5m: funding=+0.0100% > 硬阈值0.01%, 禁止做多
[FILTER] BTC/USDT 5m buy: checked=0 ... fr_hard=1 fr_penalty=0 ...
FR软阈值 SOL/USDT 5m 做空: funding=-0.0250% < -0.02%, quality 6.0 → 5.0
做多信号 BTC/USDT 5m: 现价=... FR=+0.0085%→penalty=-1.0, lev=5x→3x
```

#### 回测验证（YTD 2026-01-01 ~ 2026-06-09）

| 组合 | 收益 | 交易 | 胜率 | 回撤 | PF | FR 硬阻断 | FR 软惩罚 |
|------|------|------|------|------|----|-----------|-----------|
| BTC 5m | **+31.42%** | 30 | 23.3% | 6.16% | 4.01 | Buy 602 / Sell 315 | Buy 26 / Sell 49 |
| ETH 1h | **+16.74%** | 30 | 70.0% | 6.32% | 2.82 | Buy 144 / Sell 92 | Buy 15 / Sell 22 |
| SOL 5m | **+7.29%** | 348 | 57.5% | 2.89% | 1.46 | Buy 0 / Sell 0 | Buy 0 / Sell 879 |

**验证结论**：
- BTC：硬阈值有效拦截顶格拥挤时段（约 1.3% K 线），单笔大赚（+29,170）支撑整体收益
- ETH：FR 过滤后胜率保持 70%，5-6 月连续盈利
- SOL：做空方向是 FR 核心价值，硬阻断完全消失、软惩罚降至 879 次时收益最优（+7.29%）

### 2.4 入场条件

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
| 最低质量分 | 4.0 | 4.0 |
| 入场距离阈值 | 0.7% | 0.5% |
| RSI(14) | 35 < RSI < 75 | RSI < 45 |
| 成交量爆发 | 量比 >= 1.5× | 量比 >= 1.5× |

#### ETH/USDT (1h) - 做空（Sell）

| 条件 | 主方向 | 副方向 |
|------|--------|--------|
| 最低质量分 | 4.0 | 4.0 |
| 入场距离阈值 | 0.7% | 0.5% |
| RSI(14) | 25 < RSI < 58 | RSI > 55 |
| 成交量爆发 | 量比 >= 1.5× | 量比 >= 1.5× |

#### SOL/USDT (5m) - 做多/做空

| 条件 | 主方向 | 副方向 |
|------|--------|--------|
| 最低质量分 | 4.0 | 4.0 |
| 入场距离阈值 | 1.5% | 0.5% |
| RSI(14) | 35 < RSI < 75 / 25 < RSI < 58 | RSI < 45 / RSI > 55 |
| 成交量爆发 | 量比 >= 1.5× | 量比 >= 1.6× |

### 2.5 止损设置（ATR 动态）

```
止损距离 = ATR(14) × atr_multiplier

做多止损 = 入场价 - 止损距离
做空止损 = 入场价 + 止损距离
```

| 币种 | 周期 | ATR倍数 | 杠杆 | 典型止损距离 |
|------|------|---------|------|-------------|
| BTC | 5m | 5.0 | 5x | 0.7% - 1.2% |
| ETH | 1h | 2.8 | 5x | 1.0% - 1.5% |
| SOL | 5m | 4.0 | 3x | 1.5% - 2.5%（估计） |
| PAXG | — | 3.5 | 3x | 0.3% - 0.6%（估计） |

当 ATR 数据不足时，回退到固定百分比止损。

**实现位置：** `quant_trader/strategies/s_r_levels.py:compute_dynamic_stop_loss()`

### 2.6 出场条件

| 类型 | 条件 | 说明 |
|------|------|------|
| 止损 | 价格触及 ATR 动态止损位 | 由策略生成信号时设定，引擎自动监控 |
| 反向信号 | 持有多仓时收到 sell / 持有空仓时收到 buy | 先平仓再反向开仓 |
| 同向加仓 | 同方向新信号 | 以新价格加仓，更新持仓均价；BTC 震荡期最多1次/趋势期最多2次，ETH/SOL 关闭加仓 |

#### 已禁用功能（v1.4.0 测试后关闭）

| 功能 | 说明 | 禁用原因 |
|------|------|---------|
| 4层移动止损 | 盈利 > 0.8R 后移动到成本价，> 1.8R 后1.8R跟踪，> 3.5R 后2.5R跟踪 | 频繁触发，截断盈利 |
| 结构化止损 | 价格接近 S/R 位时移动止损到 S/R 位附近 | 过早移动，减少大赢单 |
| 部分止盈 | 2R 平 40%，3.5R 平 30% | 降低大行情收益 |

代码保留在 `s_r_levels.py` 中，通过配置开关控制，默认全部禁用。

## 3. 策略参数

### 3.1 BTC/USDT 专用策略

```yaml
# quant_trader/config/strategies.yaml
# 策略名: support_resistance_btc
# v1.4.2：启用拒绝蜡烛（2.0x）；ADX 18/15；分级加仓 1/2

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

  # 自适应摆动窗口
  adaptive_swing_window: true
  swing_window_adx_threshold: 25.0
  swing_window_strong: 3
  swing_window_weak: 2

  # S/R 检测增强
  use_order_blocks: true
  use_fvg: true

  # 趋势过滤参数
  require_parent_ema_aligned: true
  ema_fast_period: 9
  ema_slow_period: 21
  ema_gap_threshold_pct: 0.5
  adx_period: 14
  adx_min: 18.0
  adx_min_high_quality: 15.0
  di_tolerance: 2.0
  use_parent_trend_override: true
  ranging_adx_relax_factor: 0.75
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

  # 移动止损（已禁用）
  trailing_stop_enabled: false
  trailing_breakeven_threshold_r: 0.8
  trailing_breakeven_target_r: 1.8
  trailing_start_r: 1.8
  trailing_distance_r: 1.8
  trailing_wide_start_r: 3.5
  trailing_wide_distance_r: 2.5

  # 结构化止损（已禁用）
  structured_stop_enabled: false
  structured_stop_min_r: 1.5
  structured_stop_buffer_pct: 0.3
  structured_stop_quality_buffer_pct: 0.2

  # 部分止盈（已禁用）
  partial_tp_enabled: false
  partial_tp_levels:
    - r_threshold: 2.0
      close_pct: 0.4
    - r_threshold: 3.5
      close_pct: 0.3

  # 拒绝蜡烛 / 回踩确认
  require_rejection_candle: true
  rejection_min_wick_ratio: 2.0
  rejection_max_body_ratio: 1.0
  require_pullback_retest: false

  # 加仓控制
  max_additions_ranging: 1
  max_additions_trending: 2
  add_quality_tier2: 6.0

  # 强趋势逆势过滤
  strict_bias_mode: true
```

### 3.2 ETH/USDT 专用策略

```yaml
# 策略名: support_resistance_eth
# v1.4.2：切换至 1h 主交易周期；关闭加仓与拒绝蜡烛；ADX 13/11

params:
  # S/R 检测参数
  lookback: 100
  min_touches: 4.0
  stop_loss_pct: 1.5
  leverage: 5
  entry_threshold_pct: 0.7
  secondary_min_touches: 4
  secondary_entry_threshold_pct: 0.5
  trend_bonus_aligned: 1.3
  trend_bonus_counter: -1.0

  # 自适应摆动窗口
  adaptive_swing_window: false
  swing_window_adx_threshold: 25.0
  swing_window_strong: 3
  swing_window_weak: 2

  # S/R 检测增强
  use_order_blocks: false
  use_fvg: false

  # 趋势过滤参数
  require_parent_ema_aligned: true
  ema_fast_period: 21
  ema_slow_period: 50
  ema_gap_threshold_pct: 0.5
  adx_period: 14
  adx_min: 13.0
  adx_min_high_quality: 11.0
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
  secondary_min_quality: 6.0
  secondary_volume_ratio: 1.6

  # ATR 动态止损
  use_atr_stop: true
  atr_period: 14
  atr_multiplier: 2.8

  # 部分止盈（v1.4.0: ETH 启用 2R/40%）
  partial_tp_enabled: true
  partial_tp_levels:
    - r_threshold: 2.0
      close_pct: 0.4

  # 拒绝蜡烛 / 加仓 / 回踩确认（v1.4.2: 全部关闭）
  require_rejection_candle: false
  require_pullback_retest: false

  # 强趋势逆势过滤
  strict_bias_mode: true
```

### 3.3 SOL/USDT 专用策略

```yaml
# 策略名: support_resistance_sol
# v1.4.2：拒绝蜡烛关闭；加仓关闭；启用 strict_bias 与趋势期追仓参数

params:
  # S/R 检测参数
  lookback: 100
  min_touches: 4
  stop_loss_pct: 1.5
  leverage: 3          # v1.4.0: 5x → 3x
  ranging_leverage_multiplier: 0.5
  entry_threshold_pct: 1.5
  secondary_min_touches: 4
  secondary_entry_threshold_pct: 0.5

  # 自适应摆动窗口
  adaptive_swing_window: true
  swing_window_adx_threshold: 25.0
  swing_window_strong: 3
  swing_window_weak: 2

  # S/R 检测增强
  use_order_blocks: true
  use_fvg: true
  use_resonance: false

  # 趋势过滤参数
  require_parent_ema_aligned: true
  ema_fast_period: 9
  ema_slow_period: 21
  ema_gap_threshold_pct: 0.5
  adx_period: 14
  adx_min: 19.0
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
  secondary_min_quality: 6.0
  secondary_volume_ratio: 1.6

  # ATR 动态止损
  use_atr_stop: true
  atr_period: 14
  atr_multiplier: 4.0

  # 移动止损 / 结构化止损（已禁用）
  trailing_stop_enabled: false
  structured_stop_enabled: false

  # 部分止盈（v1.4.0: SOL 启用 2R/40%）
  partial_tp_enabled: true
  partial_tp_levels:
    - r_threshold: 2.0
      close_pct: 0.4

  # 拒绝蜡烛 / 回踩确认（v1.4.2: 全部关闭）
  require_rejection_candle: false
  require_pullback_retest: false

  # 加仓控制（v1.4.2: 关闭）
  max_additions_ranging: 0
  max_additions_trending: 0
  add_quality_tier2: 6.0

  # 强趋势逆势过滤
  strict_bias_mode: true

  # 趋势期追仓加仓（预留参数）
  trend_addition_allow_chase: true
```

### 3.4 PAXG/USDT 专用策略

```yaml
# 策略名: support_resistance_paxg

params:
  # S/R 检测参数
  lookback: 100
  min_touches: 4
  stop_loss_pct: 1.5
  leverage: 3
  entry_threshold_pct: 0.5
  secondary_min_touches: 4
  secondary_entry_threshold_pct: 0.3

  # 自适应摆动窗口
  adaptive_swing_window: false

  # S/R 检测增强
  use_order_blocks: false
  use_fvg: false

  # 趋势过滤参数
  require_parent_ema_aligned: true
  ema_fast_period: 9
  ema_slow_period: 21
  ema_gap_threshold_pct: 0.5
  adx_period: 14
  adx_min: 20.0
  adx_min_high_quality: 17.0

  # 动量/波动参数
  rsi_period: 14
  rsi_long_min: 35.0
  rsi_long_max: 75.0
  rsi_short_min: 25.0
  rsi_short_max: 58.0
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
    timeframes: ["1h", "4h"]
    params: { ... }

  - name: "support_resistance_sol"
    enabled: true
    symbols: ["SOL/USDT"]
    timeframes: ["5m", "1h", "4h"]
    params: { ... }

  - name: "support_resistance_paxg"
    enabled: false
    symbols: ["PAXG/USDT"]
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

### 4.3 回测脚本

**回测前需先拉取 Funding Rate 缓存（首次或数据过期时）：**

```bash
# 拉取所有币种的 funding rate 历史
python scripts/fetch_funding_history.py --all --days 60

# 或单独拉取某个币种
python scripts/fetch_funding_history.py --symbol BTC/USDT --days 60
```

**执行回测：**

```bash
# BTC 回测
python scripts/backtest.py --symbol BTC/USDT --timeframe 5m --days 60

# 指定日期范围回测
python scripts/backtest_range.py --symbol BTC/USDT --timeframe 5m --start 2026-02-01 --end 2026-04-30

# ETH 回测
python scripts/backtest.py --symbol ETH/USDT --timeframe 1h --days 60
```

> **注意**：回测时若 funding rate 缓存缺失，将自动跳过 FR 过滤（日志会提示），结果与关闭 `funding_filter_enabled` 一致。

## 5. 风控集成

| 风控项 | 策略内实现 | 外部风控接管 | 说明 |
|--------|-----------|-------------|------|
| ADX 趋势强度 | **是** | 否 | 策略前置过滤 |
| 上级 EMA 锚定 | **是** | 否 | 策略前置过滤 |
| 上级 ranging 过滤 | **是** | 否 | v1.2.0 新增 |
| RSI 动量 | **是** | 否 | 策略前置过滤 |
| 成交量爆发 | **是** | 否 | 策略前置过滤 |
| ATR 动态止损 | **是** | 检查 | 策略设定，风控检查止损距离 |
| 同向加仓限制 | **是** | 否 | BTC 震荡1次/趋势2次；ETH/SOL 关闭加仓 |
| 杠杆设定 | 建议值 | **限制** | 策略请求，风控按币种上限调整 |
| 单笔亏损上限 | 否 | **是** | 风控检查 |
| 日累计亏损 | 否 | **是** | 风控检查 |
| 最大持仓数 | 否 | **是** | 风控检查 |
| 信号冷却期 | **是** | 否 | 引擎层 220 秒同币种冷却 |
| Funding Rate 过滤 | **是** | 否 | v1.5.0：硬阈值阻断 + 软阈值扣分/降杠杆 |
| 移动止损 | 策略内（已禁用） | 否 | v1.4.0 测试后关闭 |
| 结构化止损 | 策略内（已禁用） | 否 | v1.4.0 测试后关闭 |
| 部分止盈 | 策略内（ETH/SOL启用） | 是 | v1.4.0: BTC禁用, ETH/SOL启用2R/40% |

## 6. 回测结果

### 6.1 BTC/USDT (5m)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | Profit Factor |
|---------|--------|---------|---------|------|--------------|
| 30天（近期） | **+18.40%** | 3.88% | 7 | 14.3% | **13.97** |
| 60天（近期） | **+17.08%** | 3.88% | 6 | 16.7% | **7.78** |
| YTD（1/1-6/8） | **+25.57%** | 5.47% | 9 | 22.2% | **7.22** |

**策略特征（v1.4.2）**：低胜率（~20%）但极高盈亏比（~8x）。启用拒绝蜡烛（`rejection_min_wick_ratio=2.0`）后，YTD 收益从约 +14% 提升至 +25%，同时 MDD 仅小幅扩大至 5.47%。盈利单能跑很远（单笔最大可达 +19,600 USDT），亏损单被严格截断。

### 6.2 ETH/USDT (1h)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | Profit Factor |
|---------|--------|---------|---------|------|--------------|
| 30天（近期） | **+6.01%** | 2.61% | 9 | 88.9% | 70.56 |
| 60天（近期） | **+7.84%** | 3.35% | 11 | 90.9% | 90.12 |
| YTD（1/1-6/8） | **+16.05%** | 6.32% | 30 | 70.0% | **2.67** |

**策略特征（v1.4.2）**：ETH 完全切换至 1h 主交易周期，关闭 5m、关闭加仓、关闭拒绝蜡烛后，YTD 收益率大幅提升至 **+16.05%**（30 笔，胜率 70%，PF 2.67）。60 天近期样本仅 11 笔，胜率受单笔异常盈利影响显得极高，参考价值有限。距离过滤仍是主要信号瓶颈（拦截率 93-94%）。

**关键变更效果**：
- 从 5m+1h+4h 多周期降级到 1h+4h，过滤掉大量 5m 噪音信号，盈亏比和胜率显著改善
- 关闭拒绝蜡烛后 ETH 收益进一步提升（1h 周期本身已包含足够的价格确认）
- 加仓机制在 1h 周期上实际触发次数为 0，关闭后无负面影响

### 6.3 SOL/USDT (5m)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | Profit Factor |
|---------|--------|---------|---------|------|--------------|
| 30天（近期） | +1.49% | 1.33% | 70 | 58.6% | 1.64 |
| 60天（近期） | +3.89% | 1.38% | 106 | 57.5% | 2.05 |
| YTD（1/1-6/8） | **+7.64%** | 2.81% | 295 | 58.0% | 1.57 |

**策略特征（v1.4.2）**：交易频率高（295 笔/YTD），胜率中等（~58%），MDD 控制在 3% 以内。距离过滤拦截率 77-82%（低于 BTC 的 94%），说明 SOL 的 entry_threshold_pct（1.5%）相对较宽，允许更多信号通过。

**关键变更效果**：
- 关闭拒绝蜡烛后，SOL 收益由负转正并稳定（SOL 波动大，固定影长阈值会过度过滤有效信号）
- 加仓机制保持关闭；_partial TP 2R/40%_ 继续启用以削峰填谷
- YTD +7.64% / MDD 2.81% 的收益风险比优于 60 天近期样本

### 6.4 PAXG/USDT

| 币种 | 状态 | 说明 |
|------|------|------|
| PAXG | 未启用 | 低波动品种，可能需要更紧的止损和更低的 ADX 阈值 |

## 7. 已知问题与 TODO

- [x] **Volume Profile POC/HVN**：v1.4.0 已实现分层评分
- [x] **Order Block / FVG 检测**：v1.4.0 已实现混合聚类
- [x] **自适应摆动窗口**：v1.4.0 已实现
- [x] **移动止损测试**：v1.4.0 测试完成，已禁用
- [x] **结构化止损测试**：v1.4.0 测试完成，已禁用
- [x] **部分止盈测试**：v1.4.0 测试完成，BTC禁用、ETH/SOL启用（2R/40%）
- [x] **多周期共振（BTC）**：v1.4.0 测试完成，已移除（冗余）
- [x] **多周期共振（SOL 5m+15m）**：测试完成，收益腰斩，已禁用
- [x] **ETH 1h 长周期回测**：2026-01~06 完成，+16.05% / MDD 6.32% / 30笔 / PF 2.67
- [x] **SOL 5m 回测验证**：YTD +7.64% / 295笔 / MDD 2.81%
- [x] **加仓机制优化（保守版本）**：v1.4.1 已完成，分级加仓+风险上限+连续亏损降级
- [x] **ETH 1h 策略落地与配置清理**：v1.4.2 已关闭 ETH 5m、加仓、拒绝蜡烛；清理无用参数
- [x] **SOL/BTC 拒绝蜡烛配置定型**：BTC 启用（2.0x），SOL 关闭
- [ ] **PAXG 策略激活**：低波动品种适配
- [ ] **4h 周期回测**：长周期尚未充分测试
- [ ] `identify_support_resistance` 被调用两次（做多/做空各一次），可优化为单次调用

## 8. 变更历史

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|---------|--------|
| v1.4.2 | 2026-06-09 | 策略配置定型与文档更新：①ETH 关闭 5m 交易周期、关闭加仓与拒绝蜡烛 ②BTC 启用拒绝蜡烛（2.0x） ③SOL 关闭拒绝蜡烛 ④清理 ETH/SOL 无用配置参数 ⑤移除 `check_rejection_candle` 废弃的 `reference_size` 参数 ⑥优化 `identify_support_resistance` 拒绝蜡烛计算（require_rejection=false 时跳过） | jiangfeng.qin |
| v1.4.1 | 2026-06-08 | 加仓机制优化（保守版本）：①分级加仓（震荡期1次/趋势期2次）②第2次加仓质量门槛≥6.0 ③总风险上限8% ④连续亏损≥3次后加仓降级 ⑤修复paper模式existing_pos感知bug（加仓质量过滤对模拟交易失效） | jiangfeng.qin |
| v1.4.0 | 2026-06-08 | S/R检测增强+止盈优化：①Volume Profile POC/HVN分层评分 ②Order Block/FVG检测+混合聚类 ③自适应摆动窗口（ADX驱动） ④测试并禁用4层移动止损/结构化止损 ⑤部分止盈重新评估：BTC禁用、ETH/SOL启用(2R/40%) ⑥移除多周期共振（冗余） ⑦SOL杠杆5x→3x ⑧ETH长周期回测+10.12%/MDD6.32% ⑨SOL长周期回测+8.44%/MDD6.71% | jiangfeng.qin |
| v1.3.0 | 2025-06-03 | BTC止损放宽+质量优化：①ATR倍数3.5→5.0 ②stop_loss_pct 1.5→2.2 ③min_touches 4→5.3 ④entry_threshold_pct 0.5→0.35 ⑤secondary_min_quality 6.5 ⑥trend_bonus_counter -0.5 ⑦冷却期300→220秒 ⑧BTC 5m验证60天+5.09% / 30天+6.70% | jiangfeng.qin |
| v1.2.0 | 2025-06-02 | 多策略模式：①BTC/ETH分策略配置 ②1h周期跳过ranging判断 ③回测引擎限制加仓1次 ④ATR倍数1.5→3.5 ⑤成交量爆发1.2→1.5 ⑥BTC 5m策略验证(+3.36%/60天) ⑦ETH 1h策略验证(+3.28%/60天) | jiangfeng.qin |
| v1.1.1 | 2025-05-28 | 紧急修复：①EMA计算顺序bug修复 ②过滤参数大幅放宽：ADX 22→18、RSI区间放宽、成交量爆发1.8→1.2、关闭price_vs_ema过滤和回踩确认 | jiangfeng.qin |
| v1.1.0 | 2025-05-27 | 全面升级：ADX趋势过滤、RSI动量过滤、ATR动态止损、成交量爆发确认、拒绝蜡烛、回踩确认、S/R质量加权评分、上级EMA锚定 | jiangfeng.qin |
| v1.0.0 | 2025-05-21 | 初始版本：基础 S/R + 双EMA趋势 + 多周期确认 | jiangfeng.qin |
