# 量化交易系统操作手册

## 项目结构

```
quant_trader/
├── config/                        # 配置文件目录
│   ├── exchanges.yaml             # 交易所 API Key、数据库连接、代理配置
│   ├── strategies.yaml            # 策略配置（币种、周期、参数、开关）
│   └── risk.yaml                  # 风控参数（杠杆、止损、仓位）
├── core/
│   ├── config_loader.py           # 配置加载 + 文件热重载
│   └── data_store.py              # PostgreSQL 数据层
├── exchange/
│   ├── binance_adapter.py         # Binance API 封装（ccxt）
│   └── paper_trading.py           # 模拟交易引擎
├── strategies/
│   ├── base.py                    # 策略基类
│   └── s_r_levels.py              # 支撑/压力位策略
├── technical/
│   ├── indicators.py              # 技术指标（RSI/EMA/MACD/布林带）
│   └── support_resistance.py      # 支撑/压力位识别
├── risk/
│   └── manager.py                 # 风控检查（杠杆/止损/仓位）
├── main.py                        # 主程序入口
├── status.py                      # 查看账户状态
├── reset.py                       # 数据归档与重置
├── requirements.txt               # Python 依赖
└── logs/                          # 运行日志
```

## 安装

```bash
# 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# TA-Lib C 库 (macOS, 可选)
brew install ta-lib
# 如果 TA-Lib 安装失败，系统会自动使用 numpy 备选方案
```

---

## 快速开始

### 启动程序

```bash
cd /Users/qinj057/develop/binance.transaction-ui/quant_trader
.venv/bin/python main.py
```

### 停止程序

- 正常停止：`Ctrl + C`
- 紧急暂停：在项目根目录下创建 `.pause` 文件，程序会自动暂停当前周期

```bash
touch .pause      # 暂停
rm .pause         # 恢复运行
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
  proxy: "http://127.0.0.1:7890"  # HTTP 代理（不需要代理留空）

database:
  host: "localhost"
  port: 5432
  dbname: "db_quant_trader"
  user: ""
  password: ""
```

**生效方式**：重启生效（修改后需重启程序）

### 2. strategies.yaml — 策略配置

```yaml
mode: "paper"              # 运行模式：paper | live_confirm | live_auto

strategies:
  - name: support_resistance
    enabled: true
    symbols: ["BTC/USDT", "ETH/USDT"]
    timeframes: ["5m", "1h", "4h"]
    params:
      lookback: 100           # 回看 K 线根数
      min_touches: 2          # 最少触碰次数
      stop_loss_pct: 1.5      # 止损百分比
      entry_threshold_pct: 0.5  # 入场阈值（价格距支撑/压力位的百分比）
      leverage: 5             # 杠杆倍数
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

### 查看账户状态

```bash
.venv/bin/python status.py
```

显示内容：
- 初始资金 / 可用余额 / 总权益 / 收益率
- 当前持仓（方向、入场价、现价、数量、杠杆、未实现盈亏、止损价）
- 历史交易（最近 20 笔，含盈亏明细）

### 数据归档与重置

当你更换策略或想重新开始时，将当前交易数据归档并清空。

```bash
# 归档 + 清空交易数据（交互式确认）
.venv/bin/python reset.py

# 跳过确认直接执行
.venv/bin/python reset.py --force

# 仅归档，不清空（留个备份）
.venv/bin/python reset.py --archive-only

# 指定归档表名称（方便识别）
.venv/bin/python reset.py --archive-only --suffix "before_add_rsi_strategy"

# 列出已有归档
.venv/bin/python reset.py --list

# 删除某个归档
.venv/bin/python reset.py --drop orders_20260521_174124
```

**保留的数据**：K 线历史（klines 表）不会被删除，策略重新跑时直接复用。
**被清空的数据**：订单记录（orders）、信号记录（signals）、持仓记录（positions）。

### 查看日志

```bash
# 查看运行日志（实时追踪）
tail -f logs/trader.log

# 查看信号日志
tail -f logs/signals.log

# 查看交易日志
tail -f logs/trades.log
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
       leverage, pnl, entry_time, exit_time, status
FROM positions
WHERE status = 'closed'
ORDER BY exit_time DESC;
```

### 订单流水

```sql
SELECT id, symbol, side, price, quantity, leverage,
       mode, status, order_id, timestamp
FROM orders
ORDER BY timestamp DESC
LIMIT 50;
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
SELECT * FROM orders_20260521_174124;
SELECT * FROM positions_20260521_174124;
SELECT * FROM signals_20260521_174124;
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
from strategies.my_strategy import MyStrategy

strategy_registry = {
    "support_resistance": SupportResistanceStrategy,
    "my_strategy": MyStrategy,  # 添加你的策略
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
from technical.support_resistance import identify_support_resistance

levels = identify_support_resistance(klines, min_touches=2, lookback=100)
# levels["support"]    -> 支撑位列表 [{price, strength}, ...]
# levels["resistance"] -> 压力位列表 [{price, strength}, ...]
```

---

## 网络与代理

如果你的网络需要代理访问 Binance，在 `config/exchanges.yaml` 中配置：

```yaml
binance:
  proxy: "http://127.0.0.1:7890"
```

如果不需要代理，将 `proxy` 置为空字符串：

```yaml
binance:
  proxy: ""
```

修改后需要 **重启程序** 生效（代理属于静态配置）。

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
| `core/data_store.py` | PostgreSQL 数据层（K 线/信号/订单/持仓） |
| `core/logger.py` | 日志系统（终端 + 文件 + signals.log + trades.log） |
| `exchange/binance_adapter.py` | Binance API 封装（ccxt 同步 + 线程池） |
| `exchange/paper_trading.py` | 模拟交易引擎（余额/下单/持仓/盈亏/数据库恢复） |
| `strategies/base.py` | 策略基类 |
| `strategies/s_r_levels.py` | 支撑/压力位交易策略 |
| `technical/indicators.py` | 技术指标（RSI/EMA/MACD/布林带） |
| `technical/support_resistance.py` | 支撑/压力识别 + 成交量分布 |
| `risk/manager.py` | 风控管理器（止损/杠杆/仓位/日亏损） |
| `backtest/runner.py` | 回测引擎（backtesting.py 封装） |

---

## 常见问题

### Q: 修改配置文件后不生效？
- 静态配置（exchanges.yaml）需要重启程序
- 策略/风控配置（strategies.yaml / risk.yaml）支持热重载，约 1 秒内生效

### Q: 程序重启后持仓丢失？
不会丢失。程序启动时自动从数据库恢复未平仓记录。

### Q: 如何更换策略重新跑？
使用 `reset.py` 归档旧数据后重新开始（见"数据归档与重置"部分）。

### Q: K 线数据如何获取？
程序启动后自动从 Binance 拉取，并存入数据库。每次运行只拉取增量数据（基于最新时间戳）。

### Q: 如何知道程序是否在运行？
查看进程：
```bash
ps aux | grep main.py
```
或者查看日志文件是否持续更新：
```bash
ls -la logs/trader.log
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
| `orders` | 交易订单流水 |
| `positions` | 持仓记录（含历史） |

详细的字段说明和注释可在 DataGrip 中查看，所有字段已添加中文注释。
