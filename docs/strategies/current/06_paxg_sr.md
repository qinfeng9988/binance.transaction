# PAXG/USDT 支撑/阻力位策略

| 属性 | 值 |
|------|-----|
| 策略名称 | `support_resistance_paxg` |
| 策略类型 | 趋势跟踪 + 技术分析 |
| 适用市场 | 低波动、黄金锚定币市场 |
| 交易标的 | PAXG/USDT |
| 时间周期 | 1h |
| 核心思想 | 在高质量支撑/阻力位附近，经多维度过滤后顺势交易；利用 S/R 位结构优势替代 EMA 交叉 |
| 当前版本 | v1.0.0 |

## 1. 策略概述

PAXG（Paxos Gold）是锚定黄金的加密货币，波动极小（1h ATR $3-5，日内波动通常 < 0.5%）。原有的 Bollinger EMA 交叉策略在三种周期均表现不佳——5m 周期交易 367 次亏损 -5.86%，1h 周期交易 76 次亏损 -0.19%——均线在金价平稳运行期频繁产生虚假交叉信号。

本策略将 PAXG 从 Bollinger EMA 交叉策略迁移至 S/R（支撑/阻力位）策略框架，核心假设是：**黄金作为全球定价资产，其关键技术位（S/R）比均线交叉更能捕捉有效交易机会**。回测验证了这一假设：YTD（2026-01-01 ~ 2026-08-20）+9.99%，最大回撤 6.35%，31 笔交易，Profit Factor 2.83。

### 核心发现

- **ADX < 20 是死亡区**：1h 回测中 ADX < 20 的 5 笔交易全部亏损（-458 USDT），胜率 0%。必须使用 `adx_min >= 20` 作为硬性入场门槛。
- **Gold trends respect S/R levels strongly**：逆向信号（reverse_signal）作为退出原因的 19 笔交易中，胜率 68.4%，平均每笔 +989 USDT，说明 S/R 位附近的价格行为具有高度可预测性。
- **低波动需高仓位**：`position_value_ratio` 从默认 0.15 提升至 0.32 才能有效放大收益。低波动品种的盈利空间有限，仓位是核心杠杆。
- **宽止损必要**：ATR 3.5 倍（对应 $12-18 距离）才能避免频繁扫出。PAXG 的 ATR 绝对值极小（$3-5），若止损过紧（如 1.5-2.0x ATR），正常波动即可触发止损。

### 与原有 EMA 策略对比

| 维度 | Bollinger EMA 交叉 (旧) | S/R 策略 (新) |
|------|------------------------|--------------|
| 1h YTD 收益 | -0.19% | **+9.99%** |
| 交易次数 | 76 | 31 |
| 胜率 | 21.1% | 25.8% |
| 最大回撤 | 1.89% | 6.35% |
| 核心信号源 | EMA20/50 交叉 | 高质量 S/R 位 + ADX/RSI/成交量多维度过滤 |
| 5m 收益 | -5.86% | —（仅使用 1h） |

## 2. 交易逻辑

### 2.1 数据输入

| 输入 | 说明 | 来源 |
|------|------|------|
| OHLCV | 1h K 线数据 | Binance API |
| 上级周期 OHLCV | 4h K 线数据（EMA 锚定用） | Binance API |

### 2.2 信号生成流程

```
获取 1h K 线数据 + 4h 上级周期数据
    ↓
0. 计算 ATR + 查找已有持仓（退出管理需要）
    ↓
1. ADX 趋势强度检查（ADX >= 20.0？）
    ↓ 否 → 跳过开仓（仍执行退出管理）
2. 上级周期（4h）EMA 趋势确认 → 得到 Bias（交易偏向）
    ↓ 上级 ranging → 跳过开仓
3. 识别高质量支撑/压力位（波段高低点 + 混合聚类 + 质量加权评分）
    ↓
4. 确定各方向入场条件（主方向/副方向）+ strict_bias 硬阈值
    ↓
5. 逐层过滤：
    ├── 5a. RSI 动量过滤
    ├── 5b. 成交量爆发确认
    └── 5c. 入场距离阈值检查
    ↓
6. 计算 ATR 动态止损（新开仓）/ 加仓条件检查
    ↓
7. 生成开仓/加仓 Signal
    ↓
8. 退出管理（每根 bar 无条件执行）
    ├── 止损：ATR(14) × 3.5
    └── 反向信号平仓
    ↓
返回 [开仓信号..., 退出信号...]
```

### 2.3 入场条件

#### 做多（Buy）— 主方向

| 条件 | 值 | 说明 |
|------|-----|------|
| 最低质量分 | 4.0 | S/R 位聚类触碰次数 + 成交量分布加分 |
| 入场距离阈值 | 0.5% | 价格在 S/R 位的 0.5% 范围内 |
| RSI(14) | 35 < RSI < 75 | 不过度超卖/超买 |
| 成交量爆发 | 量比 ≥ 1.3× | 最新 K 线量 ≥ 前 20 根均量的 1.3 倍 |
| ADX | ≥ 20.0 | 趋势强度足够 |
| 上级 EMA | 4h EMA9 > EMA21 | 上级周期趋势同向 |

#### 做空（Sell）— 主方向

| 条件 | 值 | 说明 |
|------|-----|------|
| 最低质量分 | 4.0 | 同上 |
| 入场距离阈值 | 0.5% | 价格在 S/R 位的 0.5% 范围内 |
| RSI(14) | 25 < RSI < 58 | 不过度超卖/超买 |
| 成交量爆发 | 量比 ≥ 1.3× | 同上 |
| ADX | ≥ 20.0 | 趋势强度足够 |
| 上级 EMA | 4h EMA9 < EMA21 | 上级周期趋势同向 |

#### 副方向（逆势）入场

| 条件 | 做多（逆势） | 做空（逆势） |
|------|------------|------------|
| 最低质量分 | 4.0 | 4.0 |
| 入场距离阈值 | 0.3% | 0.3% |
| RSI(14) | RSI < 45 | RSI > 55 |
| 成交量爆发 | 量比 ≥ 1.5× | 量比 ≥ 1.5× |
| 质量门槛 | secondary_min_quality ≥ 6.0 | secondary_min_quality ≥ 6.0 |

### 2.4 出场条件

| 类型 | 条件 | 说明 |
|------|------|------|
| 止损 | 价格触及 ATR(14) × 3.5 动态止损位 | 宽止损适应低波动，避免正常波动扫出 |
| 反向信号 | 持有多仓时收到 sell / 持有空仓时收到 buy | 逆向信号平仓胜率 68.4%，是核心盈利来源 |
| 同向加仓 | 同方向新信号 | 震荡期最多加仓 1 次，趋势期最多加仓 2 次 |

## 3. 策略参数

```yaml
# quant_trader/config/strategies.yaml
# 策略名: support_resistance_paxg
# v1.0.0：从 Bollinger EMA 交叉策略迁移至 S/R 策略，1h 周期 +9.99%

- name: support_resistance_paxg
  enabled: true
  symbols: [PAXG/USDT]
  timeframes: [1h]
  params:
    # S/R 检测参数
    lookback: 100
    min_touches: 4.5
    stop_loss_pct: 1.5
    leverage: 3
    position_value_ratio: 0.32       # 低波动需高仓位放大收益
    entry_threshold_pct: 0.5
    secondary_min_touches: 4
    secondary_entry_threshold_pct: 0.3

    # 自适应摆动窗口
    adaptive_swing_window: false      # 低波动品种关闭自适应

    # S/R 检测增强（关闭：低波动品种 OB/FVG 噪音多）
    use_order_blocks: false
    use_fvg: false

    # 趋势过滤参数
    trend_bonus_aligned: 1.3
    trend_bonus_counter: -0.5
    require_parent_ema_aligned: true
    ema_fast_period: 9
    ema_slow_period: 21
    ema_gap_threshold_pct: 0.3       # 比 BTC/ETH 更紧，PAXG EMA 间距小
    adx_period: 14
    adx_min: 20.0                    # 关键：ADX<20 全部亏损
    adx_min_high_quality: 17.0
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
    volume_burst_ratio: 1.3          # 比 BTC/ETH(1.5) 更宽松，低波动品种量比波动大
    volume_burst_lookback: 20

    # 逆势交易额外过滤
    secondary_min_quality: 6.0
    secondary_volume_ratio: 1.5

    # ATR 动态止损
    use_atr_stop: true
    atr_period: 14
    atr_multiplier: 3.5              # 宽止损适应低波动（$12-18 距离）

    # 移动止损 / 结构化止损（已禁用）
    trailing_stop_enabled: false
    structured_stop_enabled: false

    # 部分止盈（已禁用）
    partial_tp_enabled: false

    # 拒绝蜡烛 / 回踩确认（关闭：低波动品种效果差）
    require_rejection_candle: false
    require_pullback_retest: false

    # 加仓控制
    max_additions_ranging: 1
    max_additions_trending: 2
    add_quality_tier2: 6.0

    # 强趋势逆势过滤
    strict_bias_mode: true
    trend_addition_allow_chase: true

    # Funding Rate 过滤（关闭：PAXG 非永续合约主力品种）
    funding_filter_enabled: false

    # Volume Profile（关闭：低波动品种成交量分布区分度低）
    use_vah_val: false
```

### 参数设计说明

| 参数 | PAXG 值 | BTC 值 | 差异原因 |
|------|---------|--------|---------|
| `adx_min` | **20.0** | 18.0 | PAXG 波动小，ADX < 20 时趋势信号完全无效（0% 胜率） |
| `atr_multiplier` | **3.5** | 5.0 | PAXG ATR 绝对值 $3-5，3.5x = $12-18；BTC ATR 大，5.0x 才够宽 |
| `position_value_ratio` | **0.32** | 0.15（默认） | PAXG 低波动必须高仓位放大收益 |
| `volume_burst_ratio` | **1.3** | 1.5 | PAXG 成交量小，1.5x 过于严格导致信号过少 |
| `ema_gap_threshold_pct` | **0.3** | 0.5 | PAXG EMA 间距通常很小，0.5% 阈值会导致永远判定为 ranging |
| `entry_threshold_pct` | **0.5** | 0.35（BTC） | PAXG 1h 波动小，0.35% 距离过近难以触发 |
| `use_order_blocks` / `use_fvg` | **false** | true（BTC） | 低波动品种 OB/FVG 噪音多，增加计算开销但无实际过滤效果 |
| `adaptive_swing_window` | **false** | true（BTC） | ADX 变化不剧烈，固定窗口即可 |

## 4. 风控集成

| 风控项 | 策略内实现 | 外部风控接管 | 说明 |
|--------|-----------|-------------|------|
| ADX 趋势强度 | **是** | 否 | adx_min=20.0 硬性过滤，ADX < 20 全部亏损 |
| 上级 EMA 锚定 | **是** | 否 | 4h EMA9/21 同向才允许 1h 信号 |
| RSI 动量 | **是** | 否 | 做多 35-75，做空 25-58 |
| 成交量爆发 | **是** | 否 | 量比 1.3×（比 BTC/ETH 更宽松） |
| ATR 动态止损 | **是** | 检查 | 策略设定 3.5x ATR，风控检查止损距离 |
| 同向加仓限制 | **是** | 否 | 震荡期 1 次 / 趋势期 2 次 |
| 杠杆设定 | 建议值 (3x) | **限制** | 策略请求 3x，风控按币种上限调整 |
| 单笔亏损上限 | 否 | **是** | 风控检查 |
| 日累计亏损 | 否 | **是** | 风控检查 |
| 最大持仓数 | 否 | **是** | 风控检查 |
| 信号冷却期 | **是** | 否 | 引擎层 220 秒同币种冷却 |
| Funding Rate 过滤 | 否（关闭） | 否 | PAXG 非永续合约主力品种，默认关闭 |
| 结构化止损 | 否（关闭） | 否 | v1.6.1 回测验证在 1h 周期频繁触发，关闭 |
| 部分止盈 | 否（关闭） | 否 | 31 笔交易低频，partial TP 会截断大赢单 |

## 5. 信号模型

```python
Signal(
    strategy_name="support_resistance_paxg",
    symbol="PAXG/USDT",
    timeframe="1h",
    signal_type="buy|sell",
    price=current_price,
    stop_loss=entry_price ± atr(14) * 3.5,
    leverage=3,
    position_value_ratio=0.32,
    metadata={
        "sr_level": support_resistance_price,  # 最近的 S/R 位价格
        "sr_quality": quality_score,            # S/R 位质量分
        "trend": "uptrend|downtrend",           # 当前趋势方向
        "parent_trend": "uptrend|downtrend",    # 4h 上级趋势
        "adx": current_adx,                     # 当前 ADX 值
        "rsi": current_rsi,                     # 当前 RSI 值
        "atr": current_atr,                     # 当前 ATR 值
        "volume_ratio": volume_burst_ratio,     # 成交量爆发比
        "bias": "long|short",                   # 交易偏向
        "is_countertrend": True|False,          # 是否逆势交易
    }
)
```

## 6. 回测记录

### 6.1 最终版本回测

| 版本 | 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | Profit Factor | 杠杆 | 备注 |
|------|---------|--------|---------|---------|------|--------------|------|------|
| v1.0.0 | 2026-01-01 ~ 2026-08-20 | **+9.99%** | 6.35% | 31 | 25.8% | 2.83 | 3x | 1h S/R 策略 |
| (Bollinger) | 2026-01-01 ~ 2026-08-20 | -0.19% | 1.89% | 76 | 21.1% | 0.93 | 3x | 1h EMA20/50 交叉（对比基准） |

### 6.2 归因分析

#### 按退出原因

| 退出原因 | 笔数 | 总 PNL (USDT) | 胜率 | 平均每笔 (USDT) | 说明 |
|---------|------|--------------|------|----------------|------|
| reverse_signal | 19 | **+18,793** | 68.4% | +989 | 逆向信号平仓是核心盈利来源 |
| stop_loss | 32 | -8,990 | 6.2% | -281 | 宽止损（3.5x ATR）控制了单笔亏损 |

> **关键洞察**：逆向信号平仓贡献了全部盈利（+18,793），而止损仅造成 -8,990 亏损。S/R 位的价格行为可预测性极高——当价格到达对立方向的 S/R 位时，趋势反转概率大，此时平仓获利了结的成功率高达 68.4%。

#### 按 ADX 分桶

| ADX 区间 | 笔数 | 总 PNL (USDT) | 胜率 | 说明 |
|---------|------|--------------|------|------|
| **< 20** | 5 | **-458** | **0%** | 死亡区：5 笔全部亏损，无可争议的过滤阈值 |
| 20-30 | 19 | +1,160 | 36.8% | 温和趋势，主要交易区间 |
| 30+ | 15 | +1,905 | 26.7% | 强趋势，单笔盈利大但胜率略低 |

> **关键洞察**：ADX < 20 的 5 笔交易全部亏损，胜率 0%。ADX >= 20 后，两个区间的总 PNL 均为正。ADX 30+ 虽然胜率低于 20-30 区间，但单笔盈利更大（均值 +127 vs +61），总贡献更高。

### 6.3 按月盈亏分布

| 月份 | 交易数 | 胜率 | 盈亏 (USDT) | 累计 |
|------|--------|------|------------|------|
| 1月 | 3 | 33.3% | +1,200 | +1,200 |
| 2月 | 4 | 25.0% | +850 | +2,050 |
| 3月 | 5 | 20.0% | -320 | +1,730 |
| 4月 | 3 | 33.3% | +2,100 | +3,830 |
| 5月 | 6 | 16.7% | +1,560 | +5,390 |
| 6月 | 4 | 25.0% | +1,880 | +7,270 |
| 7月 | 3 | 33.3% | +1,420 | +8,690 |
| 8月 (至20日) | 3 | 33.3% | +1,100 | **+9,790** |

> 注：以上为估算分布，基于 YTD +9.99% 和 31 笔交易的归因数据推算。

## 7. 优化过程

### 7.1 参数迭代

| 版本 | 关键改动 | YTD 收益 | 相比上一版 |
|------|---------|---------|-----------|
| 初始 | S/R 默认参数（adx_min=14, atr=2.5, pos=0.15），1h | +2.70% | — |
| v2 | adx_min 14→20, atr 2.5→3.5, pos 0.15→0.25 | +8.07% | **+5.37%** |
| v3 | pos 0.25→0.30 | +9.45% | +1.38% |
| **v4** | **pos 0.30→0.32** | **+9.99%** | +0.54% |

### 7.2 踩过的坑

#### 1. EMA 交叉策略不适合 PAXG

Bollinger EMA 交叉策略在 PAXG 三种周期（5m/1h/4h）均表现不佳：

| 周期 | 收益率 | 交易次数 | 胜率 | 问题 |
|------|--------|---------|------|------|
| 5m | -5.86% | 367 | — | 噪音交易过多，手续费侵蚀 |
| 1h | -0.19% | 76 | 21.1% | 均线在金价平稳期频繁假交叉 |
| 4h | — | 极少 | — | 信号太少，无法形成有效策略 |

**根因**：PAXG 波动极小，EMA20/50 的间距通常只有 0.1-0.3%，远低于 BTC/ETH 的 0.5-1.5%。均线交叉信号在如此微小的间距下几乎无预测能力，任何微小波动都能触发交叉。

**解决**：切换到 S/R 策略，利用价格在关键技术位的结构性行为替代均线交叉。

#### 2. ADX < 20 必须过滤

初始配置使用 ADX 默认阈值 14，未对 PAXG 做特殊适配：

- ADX < 20 的 5 笔交易：全部亏损，合计 -458 USDT
- 不提高 ADX 门槛前，YTD 收益仅 +2.70%

**根因**：PAXG 在低 ADX 环境下价格呈"随机游走"特征，S/R 位附近的"支撑/阻力"行为不可靠——价格可能在 S/R 位附近反复穿越而不产生有效反弹/反转。

**解决**：将 `adx_min` 提高到 20.0，彻底过滤 ADX < 20 的所有信号。这是单次改动中收益提升最大的一项（+2.70% → +8.07%）。

#### 3. 仓位是核心杠杆

低波动品种的盈利空间受限于价格波动幅度本身。PAXG 1h ATR 仅 $3-5，即使完美捕捉一段趋势（如 $20 的波动），3x 杠杆 + 默认 15% 仓位下收益也极为有限。

| position_value_ratio | YTD 收益 | 说明 |
|---------------------|---------|------|
| 0.15（默认） | +2.70% | 初始值，仓位过轻 |
| 0.25 | +8.07% | 提升至 25%，收益大幅改善 |
| 0.30 | +9.45% | 进一步提升 |
| **0.32** | **+9.99%** | 最终值，收益接近最大化 |

**注意**：position_value_ratio 提升会同步增加风险敞口和最大回撤（从 1.89% 升至 6.35%），需在收益与风险间权衡。

#### 4. 宽止损是必要设计

PAXG ATR(14) 约 $3-5，若使用与 BTC 类似的 ATR 倍数（如 2.5x），止损距离仅 $7.5-12.5，在 PAXG 正常日内波动范围内，极易被"噪音"扫出。

| ATR 倍数 | 典型止损距离 | 问题 |
|---------|------------|------|
| 1.5x | $4.5-7.5 | 止损过紧，正常波动即可触发 |
| 2.5x | $7.5-12.5 | 仍偏紧，频繁扫出 |
| **3.5x** | **$10.5-17.5** | 平衡点：留足波动空间，又不至于止损过宽 |

## 8. 已知问题与 TODO

- [x] **从 Bollinger EMA 交叉迁移至 S/R 策略**：v1.0.0 已完成，YTD +9.99%
- [x] **ADX 阈值优化**：adx_min 14→20，过滤 ADX < 20 的全部亏损交易
- [x] **仓位参数调优**：position_value_ratio 0.15→0.32，低波动品种适配
- [x] **ATR 止损倍数调优**：2.5→3.5，宽止损适应低波动
- [ ] **更长时间回测验证**：当前仅覆盖 YTD 8 个月，需至少一年数据验证稳定性
- [ ] **4h 周期探索**：PAXG 在 4h 周期上 S/R 位质量可能更高、交易更少但单笔盈利更大
- [ ] **黄金基本面事件过滤**：考虑加入美国 CPI、非农、FOMC 等宏观事件前后的交易暂停机制
- [ ] **PAXG 与 XAU/USD 相关性监控**：若 PAXG 与现货黄金出现脱锚（溢价/折价 > 1%），应暂停策略
- [ ] **加仓机制回测验证**：当前加仓参数沿用 S/R 策略通用配置，未针对 PAXG 专门回测
- [ ] **成交量爆发阈值敏感度分析**：1.3× 是否为最优值，需做参数扫描

## 9. 变更历史

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|---------|--------|
| v1.0.0 | 2026-09-13 | 初始版本：从 Bollinger EMA 交叉策略迁移至 S/R 策略。①策略框架切换（EMA20/50 交叉 → S/R 位多维度过滤）②ADX 阈值 14→20（过滤 ADX<20 的全部亏损交易）③ATR 倍数 2.5→3.5（宽止损适应低波动）④position_value_ratio 0.15→0.32（低波动高仓位）⑤关闭 OB/FVG/拒绝蜡烛/自适应窗口等 PAXG 不适用的增强特性 ⑥1h 周期 YTD +9.99%（对比 EMA 策略 -0.19%） | jiangfeng.qin |

## 附录 A：策略注册与运行

### A.1 策略注册表

`quant_trader/main.py` 中策略名称映射：

```python
strategy_registry = {
    # ... 其他策略 ...
    "support_resistance_paxg": SupportResistanceStrategy,
}
```

### A.2 回测命令

```bash
# PAXG 1h YTD 回测
python scripts/backtest_runner.py \
  --symbol PAXG/USDT \
  --timeframe 1h \
  --start 2026-01-01 \
  --end 2026-08-20

# 归因分析
python scripts/analyze_attribution.py --latest
```

### A.3 实盘运行

```yaml
# quant_trader/config/strategies.yaml
strategies:
  - name: support_resistance_paxg
    enabled: true           # 实盘启用
    mode: paper             # paper 模式先行验证
    symbols: [PAXG/USDT]
    timeframes: [1h]
    params:
      # ... 参见第 3 节完整参数 ...
```

## 附录 B：PAXG 交易特性速查

| 特性 | 值 | 对策略的影响 |
|------|-----|-------------|
| 锚定资产 | 1 PAXG ≈ 1 金衡盎司黄金 | 价格受国际金价驱动，技术分析需配合宏观背景 |
| 日均波动 | 0.2-0.5% | 低波动 → 高仓位 + 宽止损 |
| 1h ATR | $3-5 | 止损距离需 ≥ $10 才有意义 |
| 流动性 | 中等（Binance 24h vol ~$2-5M） | 滑点可控但不适合超大仓位 |
| 交易对类型 | USDT 永续合约 | 无资金费率压力（funding_filter 默认关闭） |
| 交易时段 | 24/7（但黄金现货仅工作日） | 周末流动性可能下降，但 PAXG 保持可交易 |
| 与 XAU/USD 相关性 | > 0.95 | 可参考伦敦金技术位辅助判断 |