# UNI/USDT 支撑/阻力位策略

| 属性 | 值 |
|------|-----|
| 策略名称 | `support_resistance_uni` |
| 策略类型 | 趋势跟踪 + 技术分析 |
| 适用市场 | 中高波动 DeFi 代币 |
| 交易标的 | UNI/USDT |
| 时间周期 | 5m |
| 核心思想 | 在高质量支撑/阻力位附近，经多维度过滤后顺势交易，宽止损适应高波动 |
| 当前版本 | v1.0.0 |

## 1. 策略概述

UNI 是 DeFi 龙头代币，波动显著高于 BTC/ETH。5m S/R 策略直接从初始配置就达到 +15.99% YTD，通过收紧参数降至 +12.41% 同时降低回撤。

## 2. 核心发现

- **ADX<20 小幅亏损**：91笔/-736，必须过滤
- **逆向信号是核心优势**：70%+ 胜率，平均每笔 +293
- **高波动需宽止损**：ATR 5.5 倍才能避免频繁扫出（211→144 笔）
- **中质量区最赚钱**：quality 5-7 胜率高于 7+

## 3. 策略参数

```yaml
- name: support_resistance_uni
  enabled: true
  symbols: [UNI/USDT]
  timeframes: [5m]
  params:
    lookback: 100
    min_touches: 5.0
    stop_loss_pct: 1.8
    leverage: 3
    position_value_ratio: 0.20
    entry_threshold_pct: 0.8
    secondary_min_touches: 4
    secondary_entry_threshold_pct: 0.5
    adaptive_swing_window: true
    use_order_blocks: true
    use_fvg: true
    trend_bonus_aligned: 1.3
    trend_bonus_counter: -0.5
    require_parent_ema_aligned: true
    ema_fast_period: 9
    ema_slow_period: 21
    ema_gap_threshold_pct: 0.5
    adx_period: 14
    adx_min: 22.0
    adx_min_high_quality: 18.0
    di_tolerance: 2.0
    use_parent_trend_override: true
    ranging_adx_relax_factor: 0.75
    quality_threshold_for_lower_adx: 5.5
    price_vs_ema_filter: false
    rsi_period: 14
    rsi_long_min: 30.0
    rsi_long_max: 75.0
    rsi_short_min: 25.0
    rsi_short_max: 62.0
    rsi_countertrend_long_max: 45.0
    rsi_countertrend_short_min: 55.0
    volume_burst_ratio: 1.5
    volume_burst_lookback: 20
    secondary_min_quality: 7.0
    secondary_volume_ratio: 1.6
    use_atr_stop: true
    atr_period: 14
    atr_multiplier: 5.5
    trailing_stop_enabled: false
    structured_stop_enabled: false
    partial_tp_enabled: false
    require_rejection_candle: false
    require_pullback_retest: false
    max_additions_ranging: 1
    max_additions_trending: 1
    add_quality_tier2: 6.0
    strict_bias_mode: true
    trend_addition_allow_chase: false
    funding_filter_enabled: false
    use_vah_val: false
```

## 4. 回测记录

| 版本 | 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | 备注 |
|------|---------|--------|---------|---------|------|------|
| v1 初始 | 2026-01-01~08-20 | +15.99% | 10.72% | 368 | 45.6% | min_touches 4.0, atr 4.0, adx 20 |
| **v2 最优** | 2026-01-01~08-20 | **+12.41%** | 9.89% | 272 | 46.0% | min_touches 5.0, atr 5.5, adx 22 |

### 归因分析 (v2)

| 退出原因 | 笔数 | 总PNL | 胜率 |
|---------|------|-------|------|
| reverse_signal | 213 | +62,499 | 70.0% |
| stop_loss | 144 | -50,080 | 0% |

## 5. 优化过程

| 版本 | 关键改动 | 收益 | DD |
|------|---------|------|-----|
| v1 | 默认参数, 5m | +15.99% | 10.72% |
| v2 | min_touches 5.0, atr 5.5, adx 22, entry 0.8 | +12.41% | 9.89% |
| v3 | min_touches 5.5, atr 6.5 | +7.91% | 9.11% — 过度过滤 |
| v4 | atr 5.0, pos 0.22 | +10.36% | 12.78% — DD 反弹 |

## 6. 变更历史

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|---------|--------|
| v1.0.0 | 2026-09-13 | 初始版本：5m S/R 策略，YTD +12.41% | jiangfeng.qin |