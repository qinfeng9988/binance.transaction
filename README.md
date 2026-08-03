# 量化交易系统操作手册

## 项目结构

```
quant_trader/
├── config/                        # 配置文件目录
│   ├── exchanges.yaml             # 交易所 API Key、数据库连接、代理配置
│   ├── strategies.yaml            # 策略配置（币种、周期、参数、开关）
│   ├── risk.yaml                  # 风控参数（杠杆、止损、仓位）
│   └── manual_config.yaml         # 手动交易辅助配置（S/R 查询参数）
├── core/
│   ├── config_loader.py           # 配置加载 + 文件热重载
│   ├── data_store.py              # PostgreSQL 数据层（K 线/信号/订单/持仓/归因明细）
│   ├── kline_manager.py           # K 线数据管理（增量拉取 + 事件订阅）
│   ├── logger.py                  # 日志系统（终端 + 文件 + signals.log + trades.log）
│   ├── query_server.py            # HTTP 查询服务（状态 + S/R 查询 + 归因）
│   ├── serialization.py           # 状态序列化（持仓/账户/信号摘要）
│   └── status_server.py           # 状态 HTTP 服务
├── exchange/
│   ├── binance_adapter.py         # Binance API 封装（ccxt）
│   └── paper_trading.py           # 模拟交易引擎（余额/下单/持仓/归因/数据库恢复）
├── models/
│   └── __init__.py                # 数据模型定义（Signal 等）
├── strategies/
│   ├── base.py                    # 策略基类
│   ├── s_r_levels.py              # 支撑/压力位策略（BTC/ETH/SOL）
│   └── bollinger_bands.py         # 双均线趋势策略（PAXG）
├── technical/
│   ├── indicators.py              # 技术指标（RSI/EMA/MACD/布林带）
│   └── support_resistance.py      # 支撑/压力识别 + S/R 纯分析函数
├── risk/
│   └── manager.py                 # 风控检查（杠杆/止损/仓位）
├── backtest/
│   └── runner.py                  # 回测引擎（backtesting.py 封装）
├── main.py                        # 主程序入口
├── status.py                      # 查看账户状态
├── query_sr.py                    # 手动交易 S/R 查询工具
├── fix_positions.py               # 修复 positions 表数据（从 orders 重算）
├── reset.py                       # 数据归档与重置
├── requirements.txt               # Python 依赖
└── logs/                          # 运行日志
```

---

## 安装

### Windows (CMD)

```batch
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# TA-Lib C 库（可选）
brew install ta-lib   # macOS
# apt install libta-lib-dev    # Ubuntu/Debian
```

---

## 快速开始

### 启动程序

所有平台命令相同：

```bash
python -m quant_trader.main
```

> 在项目根目录（即包含 `quant_trader/` 子目录的目录）下执行。

- 正常停止：`Ctrl + C`
- 紧急暂停：在项目根目录下创建 `.pause` 文件，程序会自动暂停当前周期

```bash
# Windows CMD
copy nul .pause
del .pause

# Windows PowerShell
ni .pause -ItemType File
Remove-Item .pause

# macOS / Linux
touch .pause
rm .pause
```

---

## 配置文件说明

### 1. exchanges.yaml — 交易所与数据库连接

```yaml
binance:
  api_key: "你的API Key"
  api_secret: "你的API Secret"
  sandbox: false          # 是否使用测试网（Binance合约测试网已废弃）
  default_type: "swap"    # "spot"=现货, "swap"=合约
  proxy: "http://127.0.0.1:7897"  # HTTP 代理（不需要代理留空）

database:
  host: "localhost"
  port: 5432
  dbname: "db_quant_trader"
  user: "postgres"
  password: "你的密码"
```

**生效方式**：重启生效（修改后需重启程序）

### 2. strategies.yaml — 策略配置

```yaml
mode: "paper"              # 运行模式：paper | live_confirm | live_auto

strategies:
  - name: support_resistance_btc
    enabled: true
    symbols: ["BTC/USDT"]
    timeframes: ["5m", "1h", "4h"]
    params:
      lookback: 100
      min_touches: 5.3
      stop_loss_pct: 2.2
      leverage: 5
      entry_threshold_pct: 0.35
      ...

  - name: support_resistance_eth
    enabled: true
    symbols: ["ETH/USDT"]
    timeframes: ["1h", "4h"]
    params:
      lookback: 100
      min_touches: 4.0
      stop_loss_pct: 1.5
      leverage: 5
      entry_threshold_pct: 0.7
      ...

  - name: support_resistance_eth_5m
    enabled: true
    symbols: ["ETH/USDT"]
    timeframes: ["5m", "1h", "4h"]
    params:
      lookback: 288
      sr_lookback: 288
      entry_threshold_pct: 0.5
      volume_burst_ratio: 1.3
      ...

  - name: bollinger_bands_paxg
    enabled: true
    symbols: ["PAXG/USDT"]
    timeframes: ["1h"]
    params:
      ema_fast_period: 20
      ema_slow_period: 50
      atr_multiplier: 1.5
      leverage: 3
      ...
```

**生效方式**：修改后 **热重载**（约 1 秒内自动生效，无需重启）

### 3. risk.yaml — 风控参数

```yaml
leverage_limits:
  major_coins: 5      # 主流币(BTC/ETH/SOL) 最大杠杆
  alt_coins: 3        # 山寨币最大杠杆

stop_loss:
  default_pct: 1.5    # 默认止损百分比
  min_pct: 1.0        # 最小止损百分比
  max_pct: 2.5        # 最大止损百分比

position_limits:
  max_concurrent: 5       # 最大同时持仓数
  max_leveraged_loss_pct: 15  # 单笔最大亏损占保证金百分比
  max_daily_loss_pct: 30      # 日累计亏损占保证金百分比
  max_single_position_pct: 20 # 单笔仓位占总资金百分比
```

**生效方式**：修改后 **热重载**（约 1 秒内自动生效，无需重启）

---

## 配置生效方式总览

| 配置类型 | 文件 | 生效方式 | 示例 |
|---------|------|---------|------|
| 静态配置 | exchanges.yaml | 重启生效 | API Key、数据库连接、代理 |
| 动态配置 | strategies.yaml | 热重载 | 策略开关、币种、周期、参数 |
| 动态配置 | risk.yaml | 热重载 | 止损%、杠杆、仓位比例 |

## 运行模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `paper` | 模拟交易，不下真实订单 | 策略测试、模拟演练 |
| `live_confirm` | 实盘+人工确认 | 实盘初期，人工审核每笔交易 |
| `live_auto` | 实盘全自动 | 成熟策略，全自动执行 |

修改方式：在 `config/strategies.yaml` 中修改 `mode` 字段，热重载自动生效。

---

## 常用操作

### 手动交易 S/R 查询

在量化程序自动交易的同时，提供手动交易辅助查询功能。查询工具通过 HTTP 调用引擎的查询服务，获取策略视角下的 S/R 分析结果。

**前提条件**：引擎必须处于运行状态。

#### 开盘前查询（默认模式）

```bash
# 默认查询 SOL 15m 周期的 S/R 位
python -m quant_trader.query_sr --symbol SOL

# 指定周期
python -m quant_trader.query_sr --symbol BTC --timeframe 1h
python -m quant_trader.query_sr --symbol ETH --timeframe 4h
```

输出内容：
- 当前价格、趋势偏向、ADX、RSI、ATR
- 支撑位列表（按质量排序，含距离百分比）
- 压力位列表（按质量排序，含距离百分比）
- 建议止损位（手动缓冲版 ATR×5.5）

#### 持仓监控模式

```bash
# 持仓中查询结构化止损建议
python -m quant_trader.query_sr --symbol SOL --timeframe 15m \
    --position-mode --entry-price 67.0 --side long --qty 10
```

输出内容：
- 当前价格、入场价格、浮动盈亏
- 趋势偏向、ADX、RSI
- 结构化止损建议（基于 S/R 位，含距离百分比）
- ATR 止损参考值（含距离百分比）
- 推荐止盈 TP1/TP2（基于 S/R 位质量分排序，不足时 ATR 备选）
- 最近高质量支撑位和阻力位（含质量分和中文标签）

#### 参数说明

| 参数 | 必填 | 说明 | 示例 |
|------|------|------|------|
| `--symbol` | 是 | 交易对符号 | `SOL`, `BTC`, `ETH` |
| `--timeframe` | 否 | K 线周期，默认 `15m` | `15m`, `30m`, `1h`, `4h`, `1d` |
| `--position-mode` | 否 | 启用持仓监控模式 | 无 |
| `--entry-price` | 持仓模式必填 | 入场价格 | `67.0` |
| `--side` | 持仓模式必填 | 持仓方向 | `long` / `short` |
| `--qty` | 持仓模式必填 | 持仓数量 | `10` |
| `--host` | 否 | 查询服务主机，默认从配置读取 | `127.0.0.1` |
| `--port` | 否 | 查询服务端口，默认从配置读取 | `8080` |

#### 手动交易配置

手动交易参数独立于量化策略配置，位于 `quant_trader/config/manual_config.yaml`：

```yaml
# ATR 止损倍数（手动交易使用更宽松的止损）
atr_multiplier: 5.5

# 显示 S/R 位数量
show_levels: 5

# ADX 阈值
adx_threshold: 22.0

# 其他参数...
```

修改后无需重启引擎，下次查询自动生效。

---

### 查看账户状态

所有平台命令相同：

```bash
python -m quant_trader.status
```

显示内容：
- 初始资金 / 可用余额 / 总权益 / 收益率
- 当前持仓（方向、入场价、现价、数量、杠杆、未实现盈亏、止损价）
- 历史交易（最近 20 笔，含盈亏明细）

### 数据归档与重置

当你更换策略或想重新开始时，将当前交易数据归档并清空。

所有平台命令相同：

```bash
# 归档 + 清空交易数据（交互式确认）
python -m quant_trader.reset

# 跳过确认直接执行
python -m quant_trader.reset --force

# 仅归档，不清空（留个备份）
python -m quant_trader.reset --archive-only

# 指定归档表名称（方便识别）
python -m quant_trader.reset --archive-only --suffix "before_add_rsi_strategy"

# 列出已有归档
python -m quant_trader.reset --list

# 删除某个归档
python -m quant_trader.reset --drop orders_20260522_201919
```

**保留的数据**：K 线历史（klines 表）不会被删除，策略重新跑时直接复用。
**被清空的数据**：订单记录（orders）、信号记录（signals）、持仓记录（positions）、入场明细（position_entries）、出场明细（position_entry_closures）。

### 查看日志

#### Windows (PowerShell)

```powershell
# 主程序日志（实时追踪）
Get-Content quant_trader\logs\trader.log -Tail 50 -Wait

# 信号日志
Get-Content quant_trader\logs\signals.log -Tail 50 -Wait

# 成交日志
Get-Content quant_trader\logs\trades.log -Tail 50 -Wait
```

#### Windows (CMD)

```batch
REM 主程序日志（不跟踪）
type quant_trader\logs\trader.log

REM 信号日志
type quant_trader\logs\signals.log

REM 成交日志
type quant_trader\logs\trades.log
```

#### macOS / Linux

```bash
# 主程序日志（实时追踪）
tail -f quant_trader/logs/trader.log

# 信号日志
tail -f quant_trader/logs/signals.log

# 成交日志
tail -f quant_trader/logs/trades.log
```

### 程序重启后恢复

程序启动时会自动从数据库恢复未平仓的持仓，无需手动操作：

```
已连接数据库: db_quant_trader
模拟交易引擎已初始化: 初始资金=100000 USDT
从数据库恢复 2 个持仓: BTC/USDT, ETH/USDT
```

---

## 数据库直查（DataGrip）

### 当前持仓

```sql
SELECT symbol, side, entry_price, quantity, leverage,
       entry_time, stop_loss, status
FROM positions
WHERE status = 'open';
```

### 历史交易

```sql
SELECT symbol, side, entry_price, exit_price, quantity,
       leverage, pnl, total_fee, entry_time, exit_time, status
FROM positions
WHERE status = 'closed'
ORDER BY exit_time DESC;
```

### 订单流水

```sql
SELECT id, symbol, price, quantity, leverage,
       mode, order_id, fee, timestamp
FROM orders
ORDER BY timestamp DESC
LIMIT 50;
```

### 归因明细

```sql
-- 按持仓查看归因
SELECT pe.seq, pe.entry_price, pe.entry_fee, pe.order_id AS entry_order_id,
       SUM(ec.close_qty) AS total_closed_qty,
       SUM(ec.pnl) AS total_attributed_pnl,
       SUM(ec.fee) AS total_close_fee
FROM position_entries pe
LEFT JOIN position_entry_closures ec ON ec.entry_id = pe.id
WHERE pe.position_id = %s
GROUP BY pe.id, pe.seq, pe.entry_price, pe.entry_fee, pe.order_id
ORDER BY pe.seq;
```

### 信号记录

```sql
SELECT strategy_name, symbol, signal_type, price,
       stop_loss, timestamp, leverage
FROM signals
ORDER BY timestamp DESC
LIMIT 50;
```

### 查看归档表数据

```sql
-- 查看某个归档表的全部记录
SELECT * FROM orders_20260522_201919;
SELECT * FROM positions_20260522_201919;
SELECT * FROM signals_20260522_201919;
```

---

## 策略开发

### 新建策略

1. 在 `strategies/` 目录下创建新的策略文件，例如 `strategies/my_strategy.py`：

```python
from models import Signal
from strategies.base import BaseStrategy


class MyStrategy(BaseStrategy):
    """我的自定义策略。"""

    def on_bar(self, symbol: str, timeframe: str, klines: list[dict],
               positions: list) -> list[Signal]:
        if len(klines) < 20:
            return []

        # 你的策略逻辑
        current_price = klines[-1]["close"]

        # 返回信号列表（空列表表示不产生信号）
        signals = []
        # ... 判断是否满足入场条件 ...
        # if condition:
        #     signals.append(Signal(
        #         strategy_name=self.name,
        #         symbol=symbol,
        #         timeframe=timeframe,
        #         signal_type="buy",  # "buy" / "sell"
        #         price=current_price,
        #         stop_loss=stop_loss,
        #         leverage=leverage,
        #     ))

        return signals
```

2. 在 `main.py` 的 `_load_strategies()` 方法中注册策略：

```python
from strategies.s_r_levels import SupportResistanceStrategy
from strategies.bollinger_bands import BollingerBandsStrategy

strategy_registry = {
    "support_resistance": SupportResistanceStrategy,
    "support_resistance_btc": SupportResistanceStrategy,
    "support_resistance_eth": SupportResistanceStrategy,
    "support_resistance_eth_5m": SupportResistanceStrategy,
    "support_resistance_sol": SupportResistanceStrategy,
    "support_resistance_paxg": SupportResistanceStrategy,
    "bollinger_bands_paxg": BollingerBandsStrategy,  # PAXG 双均线策略
}
```

3. 在 `config/strategies.yaml` 中添加策略配置：

```yaml
strategies:
  - name: support_resistance
    enabled: true
    symbols: ["BTC/USDT"]
    timeframes: ["5m"]
    params:
      ...

  - name: my_strategy           # 与注册名一致
    enabled: true
    symbols: ["ETH/USDT"]
    timeframes: ["1h", "4h"]
    params:
      param1: 10
      param2: 20
```

4. 重启程序即可生效。

### 策略参数获取

在策略中通过 `self.params` 获取配置文件中定义的参数：

```python
def on_bar(self, symbol, timeframe, klines, positions):
    lookback = self.params.get("lookback", 100)         # 默认 100
    min_touches = self.params.get("min_touches", 2)     # 默认 2
    # ...
```

---

## 技术指标使用

```python
from technical.indicators import rsi, ema, macd, bollinger_bands

# RSI (相对强弱指标)
rsi_values = rsi(klines, period=14)
current_rsi = rsi_values[-1]

# EMA (指数移动平均)
ema_21 = ema(klines, 21)
ema_50 = ema(klines, 50)

# MACD
macd_line, signal_line, histogram = macd(klines, fast=12, slow=26, signal=9)

# 布林带
upper, middle, lower = bollinger_bands(klines, period=20, std=2.0)

# 支撑/压力位识别
from technical.support_resistance import identify_support_resistance, analyze_sr_levels

levels = identify_support_resistance(klines, min_touches=2, lookback=100)
# levels["support"]    -> 支撑位列表 [{price, strength}, ...]
# levels["resistance"] -> 压力位列表 [{price, strength}, ...]

# S/R 纯分析函数（不生成交易信号，供查询工具使用）
analysis = analyze_sr_levels(klines, parent_klines=parent_klines, params={})
# analysis["trend"]    -> {"trend": "uptrend"/"downtrend"/"ranging", ...}
# analysis["levels"]   -> {"support": [...], "resistance": [...]}
# analysis["adx"]      -> ADX 当前值
# analysis["atr"]      -> ATR 当前值
# analysis["bias"]     -> 交易偏向
```

---

## 网络与代理

如果你的网络需要代理访问 Binance，在 `config/exchanges.yaml` 中配置：

```yaml
binance:
  proxy: "http://127.0.0.1:7897"  # Clash Verge 默认端口
```

如果不需要代理，将 `proxy` 置为空字符串：

```yaml
binance:
  proxy: ""
```

修改后需要 **重启程序** 生效（代理属于静态配置）。

> **注意**：Python ccxt 不使用系统代理，必须显式配置。确保 Clash Verge 设置为「全局」或「混合」模式。

---

## 风控规则

| 规则 | 默认值 | 说明 |
|------|--------|------|
| 主流币杠杆上限 | 5x | BTC/ETH/SOL |
| 山寨币杠杆上限 | 3x | 其他币种 |
| 单笔止损距离 | 1.5% | 仓位价值的百分比 |
| 单笔最大亏损 | 保证金的 15% | 防止单笔亏太多 |
| 日累计亏损 | 保证金的 30% | 触发后暂停新交易 |
| 自动降杠杆 | 止损距离 × 杠杆 > 15% | 防止杠杆放大亏损 |

---

## 模块说明

| 模块 | 说明 |
|------|------|
| `core/config_loader.py` | YAML 配置加载 + watchdog 热重载 |
| `core/data_store.py` | PostgreSQL 数据层（K 线/信号/订单/持仓/归因明细） |
| `core/kline_manager.py` | K 线数据管理（增量拉取 + 事件订阅/发布） |
| `core/logger.py` | 日志系统（终端 + 文件 + signals.log + trades.log） |
| `core/query_server.py` | HTTP 查询服务（状态 + S/R 查询 + 归因查询） |
| `core/serialization.py` | 状态序列化（持仓/账户/信号摘要） |
| `core/status_server.py` | 状态 HTTP 服务 |
| `exchange/binance_adapter.py` | Binance API 封装（ccxt 同步 + 线程池） |
| `exchange/paper_trading.py` | 模拟交易引擎（余额/下单/持仓/归因/数据库恢复） |
| `models/__init__.py` | 数据模型定义（Signal 等） |
| `strategies/base.py` | 策略基类 |
| `strategies/s_r_levels.py` | 支撑/压力位策略（BTC/ETH/SOL） |
| `strategies/bollinger_bands.py` | 双均线趋势策略（PAXG） |
| `technical/indicators.py` | 技术指标（RSI/EMA/MACD/布林带） |
| `technical/support_resistance.py` | 支撑/压力识别 + 成交量分布 |
| `risk/manager.py` | 风控管理器（止损/杠杆/仓位/日亏损） |
| `backtest/runner.py` | 回测引擎（backtesting.py 封装） |
| `fix_positions.py` | 修复 positions 表数据（从 orders 重算持仓） |

---

## 策略文档

详细策略文档位于 `docs/strategies/` 目录：

| 文档 | 说明 |
|------|------|
| [docs/strategies/current/02_support_resistance.md](docs/strategies/current/02_support_resistance.md) | BTC/ETH/SOL 支撑/阻力位策略（v1.5.0） |
| [docs/strategies/current/03_eth_5m.md](docs/strategies/current/03_eth_5m.md) | ETH/USDT 5m 短周期策略 |
| [docs/strategies/current/04_paxg_ema.md](docs/strategies/current/04_paxg_ema.md) | PAXG/USDT 双均线趋势策略 |
| [docs/strategies/current/05_manual_query.md](docs/strategies/current/05_manual_query.md) | 手动交易辅助 S/R 查询功能 |

## 常见问题

### Q: 修改配置文件后不生效？
- 静态配置（exchanges.yaml）需要重启程序
- 策略/风控配置（strategies.yaml / risk.yaml）支持热重载，约 1 秒内生效

### Q: 程序重启后持仓丢失？
不会丢失。程序启动时自动从数据库恢复未平仓记录。

### Q: 如何更换策略重新跑？
使用 `python -m quant_trader.reset` 归档旧数据后重新开始。

### Q: K 线数据如何获取？
程序启动后自动从 Binance 拉取，并存入数据库。每次运行只拉取增量数据（基于最新时间戳）。

### Q: 如何拉取历史数据用于回测？

```bash
# 拉取所有币种的历史数据
python scripts/fetch_history.py --all --days 180

# 或单独拉取某个币种
python scripts/fetch_history.py --symbol PAXG/USDT --timeframe 1h --days 180
```

### Q: 如何进行回测？

```bash
# 指定日期范围回测
python scripts/backtest_range.py --symbol ETH/USDT --timeframe 1h --start 2026-01-01 --end 2026-06-30

# 指定策略回测（用于多策略匹配同一币种时）
python scripts/backtest_range.py --symbol PAXG/USDT --timeframe 1h --start 2026-01-01 --end 2026-06-30 --strategy bollinger_bands_paxg
```

### Q: 如何知道程序是否在运行？

```bash
# Windows CMD
tasklist | findstr python

# Windows PowerShell
Get-Process python

# macOS / Linux
ps aux | grep main.py
```

### Q: 日志文件在哪里？
日志文件位于 `quant_trader/logs/` 目录下：
- `trader.log` — 主程序日志
- `signals.log` — 交易信号日志
- `trades.log` — 成交记录日志

---

## 数据库表结构

| 表名 | 说明 |
|------|------|
| `klines` | K 线行情数据（OHLCV） |
| `signals` | 策略交易信号 |
| `orders` | 交易订单流水（成交流水，归因走 entries/closures 的 order_id 关联） |
| `positions` | 持仓记录（含历史），通过 entries/closures 关联订单 |
| `position_entries` | 入场明细（每笔开仓/加仓一条，含 entry_fee 和 order_id） |
| `position_entry_closures` | 出场明细（按 FIFO 生成，归因 PNL 已扣平仓手续费和入场手续费分摊） |

### 表关系

```
orders ←── order_id ──→ position_entries  ←── position_id ──→ positions
                       position_entry_closures ←── position_id ──→ positions
```

- **orders**：成交流水，不记录 position_id/order_type/signal_id（已删除冗余字段）
- **position_entries**：通过 order_id 关联订单，通过 signal_id 关联信号，entry_fee 记录入场手续费
- **position_entry_closures**：通过 order_id 关联平仓订单，归因 PNL 公式：
  `(exit_price - entry_price) × close_qty × direction - fee - entry_fee × (close_qty / orig_qty)`
- **positions**：total_fee 预聚合 = SUM(entries.entry_fee) + SUM(closures.fee)

详细的字段说明和注释可在 DataGrip 中查看，所有字段已添加中文注释。
