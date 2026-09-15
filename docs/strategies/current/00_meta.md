# 版本元信息

## 当前版本

| 属性 | 值 |
|------|-----|
| 版本号 | **v1.7.0** |
| 发布日期 | 2026-09-13 |
| 状态 | 已验证（BTC 5m 参数优化 +12.28%，PAXG S/R 替代 EMA 策略 +9.99%） |
| 代码分支 | develop |

## 版本历史

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|---------|--------|
| v1.7.0 | 2026-09-13 | BTC 5m 参数优化+12.28% & PAXG S/R 策略替代 EMA：①BTC 5m 回测 2026/1-8 从 +9.75%→+12.28%（position_value_ratio 0.13→0.17, min_touches 4.8→5.0, adx_min 18→17, atr_multiplier 7.0→7.5 等 9 项改动）②PAXG Bollinger EMA 策略三种周期全负（5m -5.86%, 1h -0.19%），替换为 S/R 策略，1h 回测 +9.99%（leverage 3→5x, position_value_ratio 0.32→0.20（风险平价）, adx_min 20, atr_multiplier 3.5）③Bollinger 策略启用 adx_min 20 过滤 ADX<20 全亏区间 ④修正：跟踪止损/部分止盈在 BTC 5m 验证中确认拖累收益（+9.75%→+0.69%） | jiangfeng.qin |
| v1.6.1 | 2026-08-24 | 退出机制回测评估：①修复信号调度bug（退出信号被break/cooldown跳过，改为退出信号优先执行无cooldown）②BTC 1h/ETH 1h YTD回测对比（baseline vs structured_stop+partial_tp）③回测结论：structured_stop在1h周期止损移至S/R位后频繁触发，严重拖累收益，关闭；BTC 1h partial_tp截断大赢单，关闭 | jiangfeng.qin |
| v1.6.0 | 2026-08-24 | 退出管理与开仓过滤解耦：①提取 `_manage_position_exits()` 方法 ②ATR/已有持仓查找提前到 ADX 过滤之前 ③ADX/自身震荡/上级震荡三处提前返回由 `return []` 改为返回退出信号，已有仓位止盈止损每根 bar 无条件执行 ④BTC 1h / ETH 启用 `structured_stop`+`partial_tp`，锁定已有浮盈、防止盈利单回吐 | jiangfeng.qin |
| v1.5.2 | 2026-07-08 | 持仓监控模式改进：①修复结构化止损浮亏场景bug（止损被保本约束拉到当前价另一侧）②新增止盈建议（按S/R位质量分排序取TP1/TP2，ATR备选）③持仓输出格式重设计（趋势/止盈/S/R位详情/中文标签） | jiangfeng.qin |
| v1.5.1 | 2026-07-03 | 新增手动交易辅助 S/R 查询功能：①analyze_sr_levels 纯分析函数 ②QueryServer HTTP 查询服务 ③query_sr CLI 脚本 ④manual_config.yaml 手动配置 | jiangfeng.qin |
| v1.5.0 | 2026-06-09 | Funding Rate 过滤：①基于近一年分位数分析制定阈值（BTC/ETH 做多 0.008/0.009，SOL 做空 -0.022/-0.070）②硬阈值在 S/R 识别前阻断方向 ③软阈值在 distance 检查后扣分降杠杆 ④历史数据 JSON 缓存 + 回测支持 | jiangfeng.qin |
| v1.4.2 | 2026-06-09 | 策略配置定型：①ETH 切换至 1h 主周期并关闭 5m/加仓/拒绝蜡烛 ②BTC 启用拒绝蜡烛（2.0x） ③SOL 关闭拒绝蜡烛 ④清理 ETH/SOL 无用配置参数 ⑤移除废弃的 `reference_size` 参数并优化拒绝蜡烛计算跳过逻辑 | jiangfeng.qin |
| v1.4.1 | 2026-06-08 | 加仓机制优化（保守版本）：①分级加仓（震荡1次/趋势2次）②第2次加仓质量门槛≥6.0 ③总风险上限8% ④连续亏损≥3次后降级 ⑤修复paper模式existing_pos感知bug | jiangfeng.qin |
| v1.4.0 | 2026-06-08 | S/R检测增强+止损简化：①Volume Profile POC/HVN分层评分 ②Order Block/FVG检测 ③自适应摆动窗口 ④测试并禁用移动止损/结构化止损 ⑤部分止盈重新评估后ETH/SOL启用(2R/40%) ⑥移除多周期共振（冗余） ⑦SOL杠杆5x→3x ⑧BTC长周期回测+12.40% | jiangfeng.qin |
| v1.3.0 | 2025-06-03 | BTC止损放宽+质量优化：①ATR倍数3.5→5.0 ②stop_loss_pct 1.5→2.2 ③min_touches 4→5.3 ④entry_threshold_pct 0.5→0.35 ⑤secondary_min_quality 6.5 ⑥trend_bonus_counter -0.5 ⑦冷却期300→220秒 | jiangfeng.qin |
| v1.2.0 | 2025-06-02 | 多策略模式：①BTC/ETH分策略配置 ②1h周期跳过ranging判断 ③回测引擎限制加仓1次 ④ATR倍数1.5→3.5 ⑤成交量爆发1.2→1.5 | jiangfeng.qin |
| v1.1.1 | 2025-05-28 | 紧急修复：EMA计算顺序bug修复，过滤参数放宽（ADX 18/RSI区间放宽/成交量1.2x/关闭EMA位置过滤和回踩确认） | jiangfeng.qin |
| v1.1.0 | 2025-05-27 | 策略质量过滤全面升级：ADX趋势强度、RSI动量过滤、ATR动态止损、成交量爆发确认、拒绝蜡烛、回踩确认、S/R位质量加权评分、上级EMA锚定 | jiangfeng.qin |
| v1.0.0 | 2025-05-21 | 初始版本：基础S/R + 双EMA趋势 + 多周期确认 | jiangfeng.qin |

## 当前策略清单

| 编号 | 策略名称 | 文件 | 状态 |
|------|---------|------|------|
| 02 | 支撑/阻力位多周期趋势确认策略 v1.7.0 | [02_support_resistance.md](02_support_resistance.md) | 生效中 |
| 03 | ETH/USDT 1h 策略 | [03_eth_5m.md](03_eth_5m.md) | 生效中 |
| 04 | PAXG/USDT 双均线趋势跟踪策略（已废弃） | [04_paxg_ema.md](04_paxg_ema.md) | 已废弃 |
| 05 | 手动交易辅助 S/R 查询功能 | [05_manual_query.md](05_manual_query.md) | 已生效 |
| 06 | PAXG/USDT S/R 策略 v1.0.0 | [06_paxg_sr.md](06_paxg_sr.md) | 生效中 |
| 07 | UNI/USDT 5m S/R 策略 v1.0.0 | [07_uni_sr.md](07_uni_sr.md) | 生效中 |

## 风控版本

| 文档 | 版本 | 状态 |
|------|------|------|
| [01_risk_policy.md](01_risk_policy.md) | v1.1.0 | 生效中（2026-09-15 对齐 risk.yaml：major_symbols -SOL +PAXG、max_concurrent 6、max_total_risk 10%） |

## v1.7.0 变更摘要（当前）

### A. BTC 5m 参数优化（+9.75% → +12.28%）

Backtest period: 2026-01-01 ~ 2026-08-20

| Param | Old | New | Rationale |
|-------|-----|-----|-----------|
| position_value_ratio | 0.13 | 0.17 | Core lever: +31% position = linear return scaling |
| min_touches | 4.8 | 5.0 | Slightly tighter entry quality |
| atr_multiplier | 7.0 | 7.5 | Slightly wider stop (DD 2.14%→2.82%, acceptable) |
| adx_min | 18.0 | 17.0 | Attribution shows ADX<20 has best WR (43.5%) |
| adx_min_high_quality | 15.0 | 14.0 | Consistent with above |
| rsi_long_min | 35.0 | 32.0 | Slightly wider RSI range |
| rsi_short_max | 58.0 | 60.0 | Slightly wider RSI range |
| secondary_min_quality | 6.5 | 7.0 | Tighter secondary quality |
| trend_addition_allow_chase | (none) | true | Allow chase in trends |

Verified failures: trailing_stop (+9.75%→+0.69%), partial_tp, price_vs_ema_filter, atr_multiplier=8.5 (DD 5.91%).

### B. PAXG: Bollinger EMA → S/R 策略替代

- Bollinger EMA crossover on PAXG all timeframes negative: 5m -5.86%, 1h -0.19%
- Replaced with S/R strategy, 1h optimized to +9.99%
- Key insight: ADX<20 is a death zone for PAXG (5 trades, all losing), must use adx_min >= 20
- High leverage (5x) with moderate position (0.20) via risk parity

### C. UNI 5m S/R 策略新增

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 |
|---------|--------|---------|---------|------|
| YTD（1/1-8/20） | **+12.41%** | 9.89% | 272 | 46.0% |

基于 PAXG 经验，UNI 直接从 5m S/R 起步。关键参数：min_touches 5.0, adx_min 22, atr_multiplier 5.5, position_value_ratio 0.20。

### D. XAU 评估与禁用

XAU 测试了 Bollinger EMA 和 S/R 两种策略。Bollinger 优化后 +3.81%，S/R 优化后 +4.65%，均远低于 PAXG S/R 的 +9.99%。结论：保留 PAXG，禁用 XAU 全部策略。

## v1.6.1 变更摘要

### A. 信号调度修复

**问题**：`backtest_runner.py` 和 `main.py` 的信号分发循环均有 `break`（每根 K 线只处理一个信号）+ 220 秒 cooldown。v1.6.0 策略返回 `[open_signal, update_stop_signal, partial_close_signal]` 时，退出信号排在开仓信号之后，被 `break` 跳过；cooldown 也可能延迟退出信号 1~2 根 bar。

**修复**：退出管理信号（`update_stop` / `partial_close`）优先执行且无 cooldown，开仓/全平信号再执行保持 cooldown + one-per-bar。修改文件：`scripts/backtest_runner.py`、`quant_trader/main.py`。

### B. 退出机制回测评估

**回测区间**：2026-01-01 ~ 2026-08-24（YTD）

#### BTC/USDT 1h

| 配置 | 收益率 | MDD | 交易数 | 胜率 | 关键归因 |
|------|--------|-----|--------|------|---------|
| baseline（structured_stop=false, partial_tp=false） | **+4.62%** | 2.24% | 11 | 45.5% | 10 reverse_close, 6 SL |
| v1.6.0（structured_stop=true, partial_tp=true） | +0.92% | 1.24% | 32 | 78.1% | 12 partial 2.0R, 6 partial 3.5R, 4 reverse, 18 SL |

**结论**：structured_stop 将止损移至 S/R 位附近后频繁触发（SL 从 6 次增至 18 次），截断原本能继续盈利的单子；partial_tp 在 2.0R/3.5R 平仓进一步削减大赢单。BTC 1h 的核心逻辑依赖大赢单覆盖小亏损，两种机制均严重拖累收益。**关闭 structured_stop 和 partial_tp**。

#### ETH/USDT 1h

| 配置 | 收益率 | MDD | 交易数 | 胜率 | 关键归因 |
|------|--------|-----|--------|------|---------|
| baseline（structured_stop=false, partial_tp=true） | **+6.45%** | 0.96% | 32 | 78.1% | 10 partial 2.0R, 20 reverse, 5 SL |
| v1.6.0（structured_stop=true, partial_tp=true） | +1.69% | 0.97% | 48 | 64.6% | 12 partial 2.0R, 14 reverse, 29 SL |

**结论**：与 BTC 一致，structured_stop 导致 SL 从 5 次暴增至 29 次，收益从 +6.45% 降至 +1.69%。MDD 无改善（0.96%→0.97%）。**关闭 structured_stop**；partial_tp（2.0R/40%）在 baseline 中表现稳定，保留。

### C. 配置调整

| 策略 | structured_stop | partial_tp | 说明 |
|------|-----------------|------------|------|
| support_resistance_btc_1h | true→**false** | true→**false** | 回测验证：均拖累收益 |
| support_resistance_eth | true→**false** | 保持 true | structured_stop 拖累收益；partial_tp 2.0R 保留 |

**综合结论**：v1.6.0 解耦改动（`_manage_position_exits()` 提取 + 退出信号不被开仓过滤阻断）是正确的架构改进，但退出机制本身在 1h 周期上仍不适用——S/R 位附近止损频繁被触发，与 v1.4.0 在 5m 周期的结论一致。退出管理代码保留但默认关闭，后续可探索更宽松的 buffer 或更长周期（4h）适配。

## v1.6.0 变更摘要

### A. 退出管理与开仓过滤解耦

**问题背景**：实盘 paper 模式观察到，已有仓位在 +20%~+30% 浮盈时，因 `structured_stop`/`partial_tp` 全关、止损固定在入场价下方，浮盈完全未锁定；更隐蔽的是退出管理写在 `on_bar` 的 ADX/震荡过滤**之后**，开仓被过滤拦截（`return []`）时连带跳过退出管理，导致已有仓位在震荡行情中"裸奔"。

**代码改动**（`strategies/s_r_levels.py`）：
- 提取退出管理为独立方法 `_manage_position_exits()`（结构化止损 / 跟踪止损 / 部分止盈）
- ATR 与已有持仓查找**提前**到 ADX 过滤之前（退出管理与开仓过滤共用）
- ADX / 自身震荡 / 上级震荡 三处提前返回由 `return []` 改为 `return self._manage_position_exits(...)`
- 正常路径在开仓信号生成后追加调用，保持 `[开仓, 退出]` 信号顺序

**效果**：已有仓位的止盈止损每根 bar 无条件执行，不再受开仓侧过滤（ADX / 震荡 / strict_bias / Funding Rate）阻断。验证：BTC 1h 在 4h 震荡（开仓被 parent-ranging 拦截）时，仍正确产出结构化止损上移（62630→76137）与 2.0R/3.5R 部分止盈信号。

### B. BTC 1h / ETH 启用退出机制

| 策略 | structured_stop | partial_tp | 说明 |
|------|------------------|------------|------|
| support_resistance_btc_1h | false→**true** | false→**true** | 锁定 BTC 多单浮盈（当前 +21% 裸奔） |
| support_resistance_eth | 新增**true** | 已 true | 残余仓位止损上移至最近支撑下方 |

**定位转变**：v1.4.0 回测在"开仓即被严格过滤"前提下得出这些机制拖累收益；v1.6.0 解耦后退出管理独立运行，定位从"提升每笔表现"转为"锁定已有浮盈、防止盈利单回吐成亏损"，二者场景不同，需重新回测评估。

## v1.5.0 变更摘要

### A. Funding Rate 过滤（基于分位数分析）

在信号生成流程中增加 Funding Rate 双层过滤，减少挤仓反转导致的止损。阈值基于近一年（1095 条）资金费率分位数分析制定：

- **硬阈值阻断**：funding 超过硬阈值时直接禁止该方向整根 K 线（在 S/R 识别之前）
- **软阈值惩罚**：funding 超过软阈值时 quality_score -1.0，可选降杠杆（在 distance 检查之后）
- **最终阈值**：
  - BTC：做多 0.008/0.009（交易所上限 0.01%），做空 -0.006/-0.010
  - ETH：做多 0.008/0.009，做空 -0.008/-0.015
  - SOL：做多 0.012/0.018（正向极端极少），做空 -0.022/-0.070（核心过滤价值）
  - PAXG：默认关闭

### B. 数据获取与缓存

- `exchange/binance_adapter.py` 新增 `fetch_funding_rate()` 用于实盘
- `scripts/fetch_funding_history.py` 预拉取历史 JSON 缓存，供回测使用
- 引擎层 `_get_funding_rate()` 带 60 秒 TTL 缓存
- 回测引擎通过 `bisect` 匹配最近 funding rate，零延迟

## v1.4.2 变更摘要

### A. ETH 1h 策略定型

- **关闭 ETH 5m 交易周期**：timeframes 由 `[5m, 1h, 4h]` 缩减为 `[1h, 4h]`，显著减少噪音信号
- **关闭 ETH 加仓机制**：`max_additions_ranging/trending` 移除（实际行为等同于 0），1h 周期上无触发记录
- **关闭 ETH 拒绝蜡烛策略**：`require_rejection_candle: false`，并清理相关 `rejection_min_wick_ratio`、`rejection_max_body_ratio` 参数
- **ADX 阈值**：ETH `adx_min` 维持 13.0 / `adx_min_high_quality` 11.0

### B. BTC / SOL 拒绝蜡烛配置定型

| 币种 | 拒绝蜡烛 | 参数 | 原因 |
|------|---------|------|------|
| BTC | **启用** | `rejection_min_wick_ratio=2.0`, `max_body_ratio=1.0` | YTD 收益从 ~14% 提升至 +25.57%，盈亏比优异 |
| SOL | **关闭** | 已移除相关参数 | SOL 波动大，固定阈值会过滤掉有效信号，关闭后 YTD 收益 +7.64% 且 MDD 仅 2.81% |

### C. 代码清理

- 移除 `check_rejection_candle()` 中废弃的 `reference_size` 参数
- `identify_support_resistance()` 在 `require_rejection=False` 时跳过拒绝蜡烛计算，减少无用开销
- 清理 `strategies.yaml` 中 ETH/SOL 的无效 rejection/addition 参数

## v1.4.1 变更摘要

### A. 加仓机制优化（保守版本）

- **分级加仓次数**：震荡期最多加仓 1 次，趋势期最多加仓 2 次（由 `max_additions_ranging` / `max_additions_trending` 控制）
- **第 2 次加仓质量门槛**：`add_count >= 1` 时要求 `quality_score >= 6.0`（`add_quality_tier2` 参数）
- **总风险上限**：回测引擎新增 `_total_risk_pct()`，新增仓位前检查总潜在亏损不超过余额 8%
- **连续亏损降级**：连续亏损 >= 3 次后，加仓上限自动降低 1 级
- **修复 paper 模式 existing_pos 感知 bug**：策略层 `isinstance(pos, dict)` 导致 paper 模式下 `existing_pos` 永远为 `None`，现已统一支持 dict 和 dataclass

### B. v1.4.0 变更摘要（已保留）

#### A. S/R 检测增强（已保留）

- **Volume Profile POC/HVN 分层评分**：区分 POC（最大成交量节点）和 HVN（高量节点），近距离（±0.5%）+2.0/+1.5 分，远距离（±1.0%）+1.0/+0.5 分，取代原有统一 +1 分
- **Order Block 检测**：识别趋势反转前的最后一根反向 K 线，作为额外 S/R 来源
- **FVG (Fair Value Gap) 检测**：识别价格跳空缺口， bullish FVG 作为支撑、bearish FVG 作为压力
- **自适应摆动窗口**：根据 ADX 动态调整 swing high/low 检测窗口（ADX > 25 用 3 根，否则用 2 根）
- **混合聚类**：OB + FVG + 波段高低点的价格统一聚类，避免独立聚类导致过度分散

### B. 测试后拒绝的功能（代码保留，默认禁用）

| 功能 | 测试结果 | 禁用原因 | 代码位置 |
|------|---------|---------|---------|
| 4层移动止损 | 回测收益下降 | 频繁触发，截断盈利 | `s_r_levels.py:compute_trailing_stop()` |
| 结构化止损 | 回测收益下降 | S/R位附近过早移动止损 | `s_r_levels.py:compute_structured_stop()` |
| 部分止盈（BTC） | 回测收益腰斩 | BTC核心逻辑依赖大赢单覆盖小亏损，partial TP严重削弱 | BTC禁用，ETH/SOL启用 |
| 多周期共振（BTC） | 回测无影响 | 100%交易已满足条件，冗余过滤 | 已移除 |
| 多周期共振（SOL 5m+15m） | 回测收益腰斩 | 交易次数从73→7，即使threshold放宽至5%也无法改善 | 已禁用（代码保留） |

### C. 参数调整

| 参数 | v1.3.0 | v1.4.0 | 说明 |
|------|--------|--------|------|
| SOL 杠杆 | 5x | **3x** | 降低波动品种风险 |
| SOL ATR倍数 | 3.5 | **4.0** | 匹配杠杆降低 |
| BTC min_touches | 4 | **5.3** | 提升S/R位质量门槛 |
| BTC entry_threshold_pct | 0.5% | **0.35%** | 更精确入场 |

### D. ADX 阈值测试结论

- 测试降低 BTC adx_min 20→18、ETH adx_min 15→13
- **结论：阈值已足够宽松**。FILTER SUMMARY 仍显示 93-94% 的 S/R 位被距离过滤拦截，ADX/RSI/Volume 仅拦截 0-2%
- 当前配置：BTC 18.0/15.0、ETH 13.0/11.0、SOL 19.0/17.0，不再继续下调 ADX

## 回测摘要

### BTC/USDT (5m)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 |
|---------|--------|---------|---------|------|
| YTD（1/1-8/20, v1.7.0） | **+12.28%** | 2.82% | 49 | 34.7% |

**策略特征（v1.7.0）**：低胜率（~35%）但较高盈亏比。v1.7.0 通过 9 项参数优化将 YTD 收益从 +9.75% 提升至 +12.28%，同时 MDD 从 2.14% 小幅升至 2.82%（可接受）。跟踪止损/部分止盈验证为拖累因素，已关闭。

### BTC/USDT (1h)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | 说明 |
|---------|--------|---------|---------|------|------|
| YTD（1/1-8/24, baseline） | **+4.62%** | 2.24% | 11 | 45.5% | structured_stop=false, partial_tp=false |
| YTD（1/1-8/24, v1.6.0） | +0.92% | 1.24% | 32 | 78.1% | structured_stop=true, partial_tp=true |

**策略特征（v1.6.1）**：1h 周期低频交易（YTD 11 笔 baseline），依赖大赢单覆盖小亏损。structured_stop 将止损移至 S/R 位后频繁触发（SL 从 6 增至 18），partial_tp 截断大赢单，两种机制均严重拖累收益。当前配置：**关闭 structured_stop 和 partial_tp**。

### ETH/USDT (1h)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | Profit Factor |
|---------|--------|---------|---------|------|--------------|
| 30天（近期） | -7.20% | 8.52% | 11 | 36.4% | 0.14 |
| 60天（近期） | -4.57% | 8.69% | 29 | 62.1% | 0.60 |
| YTD（1/1-7/8, v1.5.0） | **+17.21%** | 8.69% | 62 | 66.1% | **1.80** |
| YTD（1/1-8/24, baseline） | **+6.45%** | 0.96% | 32 | 78.1% | — |
| YTD（1/1-8/24, v1.6.0） | +1.69% | 0.97% | 48 | 64.6% | — |

**策略特征（v1.6.1）**：YTD 收益率 +6.45%（baseline，structured_stop=false, partial_tp=true）。structured_stop 导致 SL 从 5 暴增至 29，收益从 +6.45% 降至 +1.69%，MDD 无改善。当前配置：**关闭 structured_stop，保留 partial_tp（2.0R/40%）**。

### SOL/USDT (5m)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | Profit Factor |
|---------|--------|---------|---------|------|--------------|
| 30天（近期） | +2.15% | 3.30% | 135 | 57.0% | 1.35 |
| 60天（近期） | +4.91% | 3.30% | 240 | 57.1% | 1.46 |
| YTD（1/1-7/8, v1.5.0） | **+7.34%** | 3.60% | 630 | 58.3% | 1.25 |

**策略特征（v1.5.0）**：交易频率高（630 笔/YTD），胜率中等（~58%），MDD 控制在 3.6% 以内。FR 过滤是 SOL 的核心增强点：做空软惩罚大幅减少逆势交易。逐月盈利分布均匀，表现稳健。

### PAXG/USDT

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | 策略 |
|---------|--------|---------|---------|------|------|
| YTD（1/1-8/20, Bollinger） | -0.19% | 1.89% | 76 | 21.1% | EMA 交叉 |
| YTD（1/1-8/20, S/R v1.7.0） | **+9.99%** | 6.35% | 31 | 25.8% | S/R 1h |

**策略特征（v1.7.0）**：Bollinger EMA 交叉策略在 PAXG 低波动品种上全周期亏损，v1.7.0 替换为 S/R 策略，1h 周期回测 +9.99%。杠杆 5x + 仓位 0.20（风险平价：5x×0.20≈3x×0.32）。ADX<20 是 PAXG 的死区，必须 adx_min >= 20。

### UNI/USDT (5m)

| 回测区间 | 收益率 | 最大回撤 | 交易次数 | 胜率 | 策略 |
|---------|--------|---------|---------|------|------|
| YTD（1/1-8/20, v1.7.0） | **+12.41%** | 9.89% | 272 | 46.0% | S/R 5m |

**策略特征（v1.7.0）**：基于 PAXG 经验，UNI 直接从 5m S/R 起步。关键参数：min_touches 5.0, adx_min 22, atr_multiplier 5.5, position_value_ratio 0.20。交易频率高（272 笔/YTD），胜率中等（46.0%），MDD 偏高（9.89%）需持续监控。

### v1.6.1 YTD 退出机制回测验证（2026-01-01 ~ 2026-08-24）

| 币种 | 周期 | 配置 | 收益率 | MDD | 交易次数 | 胜率 | 说明 |
|------|------|------|--------|-----|---------|------|------|
| BTC/USDT | 1h | baseline | **+4.62%** | 2.24% | 11 | 45.5% | structured_stop=false, partial_tp=false |
| BTC/USDT | 1h | v1.6.0 | +0.92% | 1.24% | 32 | 78.1% | structured_stop=true, partial_tp=true |
| ETH/USDT | 1h | baseline | **+6.45%** | 0.96% | 32 | 78.1% | structured_stop=false, partial_tp=true |
| ETH/USDT | 1h | v1.6.0 | +1.69% | 0.97% | 48 | 64.6% | structured_stop=true, partial_tp=true |

**结论**：structured_stop 在 1h 周期将止损移至 S/R 位后频繁触发，两种策略收益均大幅下降，MDD 无改善。当前配置：BTC 1h 关闭 structured_stop + partial_tp；ETH 关闭 structured_stop，保留 partial_tp。

### v1.5.0 YTD 回测验证（2026-01-01 ~ 2026-07-08）

| 币种 | 周期 | 收益率 | 最大回撤 | 交易次数 | 胜率 | PF |
|------|------|--------|---------|---------|------|-----|
| BTC/USDT | 5m | **+38.22%** | 7.69% | 72 | 33.3% | **2.72** |
| ETH/USDT | 1h | **+17.21%** | 8.69% | 62 | 66.1% | 1.80 |
| SOL/USDT | 5m | **+7.34%** | 3.60% | 630 | 58.3% | 1.25 |
| PAXG/USDT | 1h | +0.66% | 3.60% | 51 | 25.5% | 1.13 |

**关键观察**：
- **BTC**：YTD 收益 +38.22%，30 天近期受连续止损影响为 -3.74%，60 天区间 +33.64% 体现大赢单覆盖小亏损
- **ETH**：YTD +17.21%，近期 30/60 天受震荡行情影响为负，上半年趋势期盈利是核心支撑
- **SOL**：YTD +7.34%，逐月盈利分布均匀，MDD 仅 3.6%，FR 过滤是核心价值所在
- **PAXG**：YTD +0.66%，低波动品种收益微薄，可能需要策略参数优化

## 迭代计划

| 优先级 | 计划内容 | 目标版本 | 预计日期 | 状态 |
|--------|---------|---------|---------|------|
| P0 | ETH 1h 策略定型（关闭 5m/加仓/拒绝蜡烛） | v1.4.2 | 2026-06-09 | **已完成** |
| P0 | BTC/SOL 拒绝蜡烛配置定型 | v1.4.2 | 2026-06-09 | **已完成** |
| P0 | Funding Rate 过滤（硬阈值阻断 + 软阈值扣分/降杠杆） | v1.5.0 | 2026-06-09 | **已完成** |
| P0 | 退出管理与开仓过滤解耦（已有仓位止盈止损每根 bar 无条件执行） | v1.6.0 | 2026-08-24 | **已完成** |
| P1 | 解耦后退出机制回测评估（structured_stop/partial_tp 重测，定位为锁定浮盈） | v1.6.1 | 2026-08-24 | **已完成** |
| P1 | PAXG 策略激活（Bollinger EMA → S/R 策略替代，1h 回测 +9.99%） | v1.7.0 | 2026-09-13 | **已完成** |
| P1 | BTC 5m 参数优化（9 项改动，+9.75%→+12.28%） | v1.7.0 | 2026-09-13 | **已完成** |
| P1 | UNI 策略新增（5m S/R，YTD +12.41%） | v1.7.0 | 2026-09-13 | **已完成** |
| P1 | XAU 策略评估与禁用（Bollinger +3.81%, S/R +4.65%，均低于 PAXG） | v1.7.0 | 2026-09-13 | **已完成** |
| P0 | 风控配置调整（major_symbols -SOL +PAXG、max_concurrent 5→6、max_total_risk 8→10%）+ 风控文档对齐 | v1.7.0 | 2026-09-15 | **已完成** |
| P1 | SOL 5m 长周期回测验证（覆盖趋势期，验证第2次加仓效果） | v1.4.2 | 2026-06-15 | 已完成 |
| P1 | Funding Rate 过滤回测验证（对比开启/关闭的关键指标） | v1.5.0 | 2026-06-12 | **已完成** |
| P1 | PAXG 低波动品种适配 | v1.5.0 | 2026-06-30 | **已完成** |
| P1 | 4h 周期充分回测 | v1.5.0 | 2026-06-30 | 计划中 |
| P1 | ETH 5m 策略回测与参数优化 | v1.5.0 | 2026-07-01 | **已完成** |
| P2 | 信号冷却期支持按策略/币种/周期单独配置 | v1.5.0 | 2026-06-30 | 计划中 |
| P2 | 新增策略类型（如趋势突破策略） | v2.0.0 | 2026-07-01 | 计划中 |
| P2 | 手动交易辅助 S/R 查询功能（analyze_sr_levels + QueryServer + query_sr CLI） | v1.5.1 | 2026-07-03 | **已完成** |
| P2 | 持仓监控模式改进（止损bug修复 + 止盈建议 + 输出格式重设计） | v1.5.2 | 2026-07-08 | **已完成** |

## 相关配置

| 配置文件 | 路径 | 说明 |
|---------|------|------|
| 策略配置 | `quant_trader/config/strategies.yaml` | 策略参数、币种、周期、运行模式、所有过滤开关 |
| 风控配置 | `quant_trader/config/risk.yaml` | 杠杆限制、止损范围、亏损上限等 |
| 交易所配置 | `quant_trader/config/exchanges.yaml` | API密钥、代理、数据库连接 |
