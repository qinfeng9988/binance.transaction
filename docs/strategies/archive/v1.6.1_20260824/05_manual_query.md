# 手动交易辅助 S/R 查询功能

## 功能概述

在量化程序自动交易的同时，提供手动交易辅助查询功能。通过 HTTP 接口调用引擎的查询服务，获取策略视角下的 S/R 分析结果，帮助用户进行手动交易决策。

**核心特点**：
- 与量化自动交易逻辑完全独立，互不影响
- 复用策略的 S/R 分析逻辑（`analyze_sr_levels` 纯函数）
- 支持两种查询模式：开盘前查询、持仓监控
- 持仓监控包含结构化止损 + ATR 止损 + 止盈建议（TP1/TP2）
- 当引擎未配置请求的币种/周期时，自动从 Binance 拉取数据并本地计算
- 自然语言纯文本输出，S/R 位标签中文翻译，便于终端查看

---

## 架构设计

```
┌─────────────────────────────────────────┐
│           量化交易引擎 (Engine)           │
│  ┌─────────────┐  ┌─────────────────────┐│
│  │ KlineManager │  │ QueryServer         ││
│  │ (定时拉取)   │  │ /api/v1/status     ││
│  └──────┬──────┘  │ /api/v1/query      ││
│         │         └─────────────────────┘│
│         │                                │
│         └──────┐                         │
│                ↓                         │
│    analyze_sr_levels()  ←── 纯分析函数    │
│         │                                │
│         └──────┐                         │
│                ↓                         │
│    SupportResistanceStrategy.on_bar()    │
│    (生成交易信号，自动交易)               │
└─────────────────────────────────────────┘
         ↑
         │ HTTP /api/v1/query
         ↓
┌─────────────────────────────────────────┐
│    python -m quant_trader.query_sr      │
│         │                                │
│         │ 1. 解析参数                     │
│         │ 2. 调用 HTTP API               │
│         │ 3. 格式化为纯文本               │
│         ↓                                │
│    终端显示自然语言描述                   │
└─────────────────────────────────────────┘
```

---

## 核心组件

### 1. analyze_sr_levels 纯函数

位于 `quant_trader/technical/support_resistance.py`，封装了 S/R 分析的核心逻辑：

```python
def analyze_sr_levels(
    klines: list[dict],
    parent_klines: list[dict] | None = None,
    params: dict | None = None,
) -> dict:
    """S/R 位纯分析函数（不生成交易信号）。"""
```

**功能**：
- EMA 趋势判断（双 EMA 交叉）
- 上级周期趋势确认
- ADX 趋势强度计算
- RSI 动量计算
- ATR 波动率计算
- 成交量爆发检测
- S/R 位识别与质量评分

**返回**：
```python
{
    "current_price": float,      # 当前价格
    "timestamp": str,            # 时间戳
    "trend": dict,              # 当前周期趋势
    "parent_trend": dict,       # 上级周期趋势
    "bias": str,                # 交易偏向
    "adx": float,               # ADX 值
    "plus_di": float,           # +DI 值
    "minus_di": float,          # -DI 值
    "rsi": float,               # RSI 值
    "atr": float,               # ATR 值
    "volume_ratio": float,      # 成交量比
    "levels": {                 # S/R 位
        "support": [...],
        "resistance": [...]
    },
    "is_ranging": bool,         # 是否震荡
}
```

### 2. QueryServer HTTP 服务

位于 `quant_trader/core/query_server.py`，基于 aiohttp 构建：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/status` | GET | 账户状态查询（原有功能） |
| `/api/v1/query` | GET | S/R 查询（新增） |

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symbol` | string | 是 | 交易对符号，如 `SOL` |
| `timeframe` | string | 否 | K 线周期，默认 `15m` |
| `mode` | string | 否 | `pre_trade`（默认）或 `position` |
| `entry_price` | float | 持仓模式必填 | 入场价格 |
| `side` | string | 持仓模式必填 | `long` 或 `short` |
| `qty` | float | 持仓模式必填 | 持仓数量 |

### 3. query_sr CLI 脚本

位于 `quant_trader/query_sr.py`，提供命令行接口：

```bash
# 开盘前查询
python -m quant_trader.query_sr --symbol SOL --timeframe 15m

# 持仓监控
python -m quant_trader.query_sr --symbol SOL --timeframe 15m \
    --position-mode --entry-price 67.0 --side long --qty 10
```

---

## 配置参数

手动交易配置独立于量化策略配置，位于 `config/manual_config.yaml`：

```yaml
# ATR 止损倍数（手动交易使用更宽松的止损）
atr_multiplier: 5.5

# 显示 S/R 位数量
show_levels: 5

# ADX 阈值
adx_threshold: 22.0

# 趋势确认周期（用于显示上级周期趋势）
trend_timeframe: "1h"

# EMA 参数
ema_fast_period: 21
ema_slow_period: 50
ema_gap_threshold_pct: 1.0

# RSI 参数
rsi_period: 14

# 成交量爆发参数
volume_burst_lookback: 20
volume_burst_ratio: 1.8

# S/R 识别参数
sr_lookback: 100
min_touches: 3.0
entry_threshold_pct: 0.5

# 结构化止损参数
structured_stop:
  buffer_pct: 0.3
  quality_buffer_pct: 0.2
  min_r: 1.5

# 部分止盈参数
partial_tp_levels:
  - r_threshold: 2.0
    close_pct: 0.4
  - r_threshold: 3.5
    close_pct: 0.3

# 拒绝蜡烛参数
require_rejection_candle: false
rejection_min_wick_ratio: 2.0
rejection_max_body_ratio: 0.5

# 自适应摆动窗口
adaptive_swing_window: false
swing_window_adx_threshold: 25.0
swing_window_strong: 3
swing_window_weak: 2

# 高级功能开关
use_order_blocks: false
use_fvg: false
use_vah_val: false
```

---

## 输出格式

### 开盘前查询

```
=== SOLUSDT 15m 最新 S/R 查询 ===
时间: 2026-07-03 14:45 | 当前价格: 67.45 USDT
趋势偏向: Downtrend (1h上级确认) | ADX: 23.4 (中等趋势) | ATR(14): 185 USDT
成交量比: 1.20x | 偏向: short_primary

支撑位 (由强到弱):
  64.82 (质量 7.8 ★★★★★)  距离 -3.90%
  66.35 (质量 6.2 ★★★★☆)  距离 -1.63%
  65.10 (质量 5.4 ★★★☆☆)  距离 -3.48%

压力位 (由强到弱):
  68.95 (质量 7.1 ★★★★★)  距离 +2.23%
  71.20 (质量 5.9 ★★★★☆)  距离 +5.56%
  69.80 (质量 5.1 ★★★☆☆)  距离 +3.48%

建议止损位 (手动缓冲版 ATR×5.5):
  做多止损建议: 64.12 (下方 3.33 USDT / 4.94%)
  做空止损建议: 69.65 (上方 2.20 USDT / 3.26%)

操作建议: 结合K线形态、成交量和新闻综合判断。
```

### 持仓监控

```
=== SOL/USDT 15m 持仓监控模式 ===
当前价格: 68.45
入场价格: 67.00 (long)
浮动盈亏: +2.16% (+144 USDT)

趋势偏向: uptrend | ADX: 23.4 | RSI: 62

=== 结构化建议 ===
推荐止损 (Structured SL): 66.35 (-3.1% from entry, -3.0% from current)
ATR 止损: 64.12 (-4.3% from entry)
推荐止盈 (Structured TP1): 71.20 (+6.3% from entry)
推荐止盈 (Structured TP2): 73.80 (+10.1% from entry)

最近高质量支撑位:
- 66.80 (quality 7.2, POC近区)
- 65.90 (quality 5.8)

最近高质量阻力位:
- 71.20 (quality 6.9, HVN近区)
- 73.80 (quality 5.5)
```

**止盈逻辑**：
- 按 S/R 位质量分（quality_score）降序排序，取前 2 个阻力位（做多）或支撑位（做空）
- 位不足时用 ATR 倍数备选：TP1 = entry ± 2×ATR, TP2 = entry ± 3×ATR
- 来源标记：`[ATR]` 表示 ATR 备选计算，无标记表示来自 S/R 位

**止损逻辑**：
- 结构化止损：基于最近支撑/压力位 + buffer 百分比
- 浮盈时止损不低于入场价（保本止损）
- 浮亏时止损必须在当前价安全侧（做多低于当前价，做空高于当前价）
- ATR 止损：current_price ± ATR×5.5，提供纯波动幅度的止损参考

**S/R 位标签中文翻译**：

| 英文 | 中文 |
|------|------|
| POC_near | POC近区 |
| POC_far | POC远区 |
| HVN_near | HVN近区 |
| HVN_far | HVN远区 |
| VAH_near | VAH近区 |
| VAL_near | VAL近区 |
| VA_inner | Value Area内 |
| LVN | LVN |
| none | (不显示) |

---

## 与量化自动交易的关系

| 维度 | 量化自动交易 | 手动交易辅助查询 |
|------|-------------|-----------------|
| 触发方式 | 定时 K 线更新 | HTTP 请求 / CLI 命令 |
| 止损逻辑 | ATR×1.5（紧凑） | ATR×5.5（宽松） |
| 交易执行 | 自动下单 | 仅提供建议，不执行 |
| 配置来源 | `strategies.yaml` | `manual_config.yaml` |
| 信号机制 | 生成 Signal 对象 | 不生成信号 |
| 影响范围 | 程序自动交易账户 | 手动交易账户（独立） |

---

## 版本历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.5.2 | 2026-07-08 | 持仓监控改进：①修复结构化止损浮亏场景bug（止损被保本约束拉到当前价另一侧）②新增止盈建议TP1/TP2（按S/R位质量分排序，ATR备选）③持仓输出格式重设计（趋势/止损+止盈/S/R位详情）④vp_detail中文翻译⑤支持15m/30m/1h/4h/1d时间粒度⑥symbol自动补全/USDT⑦引擎无数据时自动从Binance拉取⑧查询结果60秒缓存 |
| v1.0.0 | 2026-07-03 | 初始版本：支持开盘前查询和持仓监控两种模式 |
