# ETH/USDT 5m 支撑/阻力位策略

| 属性 | 值 |
|------|-----|
| 策略名称 | `support_resistance_eth_5m` |
| 策略类型 | 趋势跟踪 + 技术分析 |
| 适用市场 | 高流动性、有明确趋势的加密货币市场 |
| 交易标的 | ETH/USDT |
| 时间周期 | 5m / 1h / 4h |
| 核心思想 | 基于 S/R 位质量评分 + 多周期趋势确认 + 多维度过滤的短周期交易策略 |
| 当前版本 | v1.0.0 |

## 1. 核心算法

基于 `SupportResistanceStrategy`（`quant_trader/strategies/s_r_levels.py`），核心逻辑与 ETH 1h 策略一致，但参数适配 5m 短周期：

- **S/R 检测**：质量加权评分（触碰次数 + 成交量爆发 + 聚类密度）
- **多周期趋势确认**：5m 依赖 parent 1h 趋势方向
- **多维度过滤**：ADX、RSI、成交量、拒绝蜡烛、Funding Rate

## 2. 交易逻辑

### 2.1 信号生成流程

```
获取 5m K 线数据 → 上级 1h 趋势确认 → ADX 检查 → S/R 位识别 → 逐层过滤 → 生成信号
```

### 2.2 关键参数差异（vs ETH 1h）

| 参数 | ETH 1h | ETH 5m |
|------|--------|--------|
| entry_threshold_pct | 0.7% | **0.5%** |
| adx_min | 13.0 | **15.0** |
| strict_bias_mode | true | **false** |
| use_atr_stop | true | **false** |
| stop_loss_pct | 1.5% | **2.0%** |
| partial_tp_enabled | true | **true** |
| volume_burst_ratio | 1.5 | **1.3** |

## 3. 策略参数

```yaml
- name: support_resistance_eth_5m
  enabled: true
  symbols:
  - ETH/USDT
  timeframes:
  - 5m
  - 1h
  - 4h
  params:
    lookback: 288
    sr_lookback: 288
    trend_lookback: 200
    parent_lookback: 150
    min_touches: 3.5
    stop_loss_pct: 2.0
    leverage: 5
    entry_threshold_pct: 0.5
    secondary_min_touches: 3
    secondary_entry_threshold_pct: 0.35
    trend_bonus_aligned: 1.3
    trend_bonus_counter: -0.5
    adaptive_swing_window: true
    use_order_blocks: true
    use_fvg: true
    require_parent_ema_aligned: true
    ema_fast_period: 9
    ema_slow_period: 21
    ema_gap_threshold_pct: 0.5
    adx_period: 14
    adx_min: 15.0
    adx_min_high_quality: 12.0
    volume_burst_ratio: 1.3
    volume_burst_lookback: 30
    use_atr_stop: false
    partial_tp_enabled: true
    partial_tp_levels:
    - r_threshold: 1.2
      close_pct: 0.4
    - r_threshold: 2.0
      close_pct: 0.3
    require_rejection_candle: true
    strict_bias_mode: false
    funding_filter_enabled: true
```

## 4. 回测记录

| 版本 | 回测区间 | 标的 | 收益率 | 最大回撤 | 交易次数 | 胜率 | Profit Factor | 备注 |
|------|---------|------|--------|---------|---------|------|--------------|------|
| v1.0.0 | 2026-01-01 ~ 2026-06-30 | ETH/USDT 5m | **+10.06%** | 4.67% | 103 | 76.7% | 1.97 | 增加回看周期(288) + 降低成交量要求(1.3) |
| v1.0.0 | 2026-01-01 ~ 2026-06-30 | ETH/USDT 5m | +6.82% | 6.23% | 98 | 76.5% | 1.56 | 初始版本 |
| v1.0.0 | 2026-01-01 ~ 2026-06-30 | ETH/USDT 5m | +1.44% | 6.36% | 17 | 23.5% | 1.20 | 初版参数（ADX 18/ATR 5.0） |

## 5. 与 ETH 1h 策略对比

| 指标 | ETH 1h (基准) | ETH 5m |
|------|--------------|--------|
| 收益率 | **+21.45%** | +10.06% |
| 最大回撤 | **5.38%** | 4.67% |
| 交易次数 | 60 | **103** |
| 胜率 | 68.3% | **76.7%** |
| Profit Factor | **2.24** | 1.97 |

**结论**：5m 策略交易频率更高，但单笔盈利较小，总体收益率低于 1h。可作为补充策略运行。

## 6. 已知问题与 TODO

- [ ] 5m 周期噪音较大，需持续优化参数
- [ ] 与 ETH 1h 策略同时运行时需关注信号冲突

## 7. 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0.0 | 2026-07-01 | 初始版本：基于 ETH 1h 策略核心逻辑，适配 5m 短周期参数 |
