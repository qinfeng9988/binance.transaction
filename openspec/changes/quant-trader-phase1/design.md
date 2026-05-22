## 上下文

从零开始构建一个基于 Python 的量化交易系统。用户是个人交易者，目前在 Binance 平台手动进行现货和合约交易，通过 K 线分析寻找支撑位和压力位。本地已有 PostgreSQL 服务和 DataGrip 管理工具。

项目分阶段推进，Phase 1 聚焦骨架搭建：交易所接入、策略框架、回测引擎、风控模块、配置系统。后续 Phase 将加入消息面分析和 LLM 情感分析。

**约束条件：**
- 个人项目，不考虑多租户
- 本地运行，后续可部署 VPS
- 使用 PostgreSQL 存储
- Python 技术栈
- Phase 1 以 paper trading（模拟交易）为主

## 目标 / 非目标

**目标：**
- 搭建可扩展的量化交易系统骨架
- 实现 Binance API 接入（行情获取 + 下单能力）
- 建立策略框架，支持多周期（5m/1h/4h）多币种运行
- 提供 paper trading 模式用于策略验证
- 集成回测引擎，使用历史数据验证策略
- 实现风控模块（止损、杠杆、仓位管理）
- 建立分层配置系统（静态/热重载/紧急覆盖）

**非目标：**
- 消息面分析和 LLM 情感分析（后续 Phase）
- 多交易所支持（Phase 1 仅 Binance，但预留适配器接口）
- Web UI 或可视化界面
- 高频交易（毫秒级）
- 多租户或商业化功能

## 决策

### 1. 交易所抽象: ccxt vs 官方 SDK

**决策：使用 ccxt 作为交易所抽象层。**

- ccxt 统一了 Binance 的 REST 和 WebSocket API，现货和合约接口一致
- 后续扩展到 OKX 等交易所只需更换 exchange 实例
- 内置 OHLCV 数据获取、下单、持仓管理等标准方法
- 官方 `python-binance` SDK 功能更全但绑定单一交易所

**替代方案：** 使用 `python-binance` SDK。优点是 Binance 特有功能（如组合期权）支持更好，但丧失了扩展性。

### 2. 数据库: PostgreSQL (用户已有)

**决策：使用 psycopg2 + 原生 SQL。**

- 用户本地已有 PostgreSQL，DataGrip 可视化管理
- 不引入 ORM 开销，SQL 查询更透明
- K 线数据量大时，原生 SQL 性能更好
- 表结构简单：klines、orders、signals、positions 四张核心表

**替代方案：** SQLAlchemy ORM。开发更快，但增加一层抽象且个人项目没必要。

### 3. 配置系统: YAML + watchdog 热重载

**决策：分层配置文件，三类配置不同生效策略。**

```
config/
├── exchanges.yaml     # 静态配置 (启动时读取)
├── strategies.yaml    # 动态配置 (watchdog 监听变更，热重载)
├── risk.yaml          # 动态配置 (watchdog 监听变更，热重载)
└── .pause             # 紧急覆盖 (轮询检测，立即生效)
```

- `exchanges.yaml`: API key 等敏感信息，变更需重启重建连接
- `strategies.yaml`: 策略参数、timeframe、币种列表，支持热重载
- `risk.yaml`: 止损、杠杆、仓位限制，支持热重载
- `.pause` 文件: 存在即暂停交易，删除即恢复

**替代方案：** 环境变量 + 数据库配置。更复杂，个人项目不需要。

### 4. 策略框架: 基类 + 注册模式

**决策：策略继承 BaseStrategy 基类，实现 on_bar() 方法。**

```python
class BaseStrategy:
    def on_bar(self, symbol, timeframe, klines, positions) -> List[Signal]
    
class SupportResistanceStrategy(BaseStrategy):
    def on_bar(self, symbol, timeframe, klines, positions) -> List[Signal]
```

- 引擎定时调用策略的 on_bar()，传入最新 K 线数据
- 策略返回 Signal 对象（BUY/SELL/HOLD + 价格 + 止损位）
- 信号经过风控层检查后决定是否执行
- 新策略只需添加新文件并注册即可

**替代方案：** DSL 声明式策略。用户友好但解析器开发成本高，Phase 1 不需要。

### 5. 回测引擎: backtesting.py

**决策：使用 backtesting.py 作为回测框架。**

- API 简洁，与我们的策略基类风格接近
- 内置绩效报告（收益率、最大回撤、夏普比率等）
- 支持参数优化（网格搜索）
- pandas DataFrame 输入，与我们的数据格式一致

**替代方案：** Backtrader（更成熟但学习曲线陡）、vectorbt（向量化速度快但 API 复杂）。

### 6. 调度: asyncio + 自定义定时器

**决策：使用 asyncio 内置定时器，不引入 apscheduler。**

- 多个 timeframe 本质上是不同间隔的定时任务
- asyncio.create_task + sleep 实现简单且无额外依赖
- 每个 timeframe 一个协程，互不阻塞
- apscheduler 功能更丰富但增加复杂度，个人项目用不上

**替代方案：** apscheduler。功能更全（cron 表达式、持久化等），但 asyncio 够用。

### 7. 交易模式切换: 配置驱动

**决策：通过 strategies.yaml 中的 `mode` 字段控制。**

```yaml
mode: paper        # 模拟交易，记录信号不下单
mode: live_confirm  # 实盘，推送通知等人确认
mode: live_auto     # 实盘全自动，风控检查后自动下单
```

- Phase 1 默认 paper 模式
- 切换到 live_confirm 需要实现通知通道（钉钉/邮件/终端提示）
- 切换到 live_auto 需要额外的安全确认流程

### 8. 技术分析: pandas + TA-Lib

**决策：pandas 做数据处理，TA-Lib 做指标计算。**

- pandas 是量化交易的标配，K 线数据天然适合 DataFrame
- TA-Lib 提供 RSI、MACD、EMA、布林带等标准指标，C 底层速度快
- 支撑/压力位识别用自定义算法（历史高低点聚类 + 成交量分布）

## 风险 / 权衡

- **[风险] ccxt 的 WebSocket 稳定性** → ccxt 的 WebSocket 实现偶有断连，需加自动重连逻辑；REST 轮询作为备用方案
- **[风险] Binance API 频率限制** → 每分钟 1200 次请求限制，多周期多币种时需控制拉取频率；本地缓存 K 线数据
- **[风险] TA-Lib 安装依赖** → TA-Lib 需要先安装 C 库（brew install ta-lib），跨平台可能有问题；备选方案是用 pandas-ta 纯 Python 实现
- **[风险] 风控逻辑遗漏** → 合约交易有爆仓风险，风控必须覆盖止损、杠杆、仓位、日亏损四道防线；宁可保守不可激进
- **[风险] 策略参数过拟合** → 回测优秀不代表实盘有效；需要走样本外验证和模拟交易过渡期
- **[权衡] 不过度抽象** → Phase 1 只接入 Binance，但预留适配器接口；不提前做多交易所兼容层
