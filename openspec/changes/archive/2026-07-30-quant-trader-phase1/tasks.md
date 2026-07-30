## 1. 项目骨架搭建

- [x] 1.1 创建 quant-trader 项目目录结构
- [x] 1.2 创建 requirements.txt，包含 ccxt, pandas, TA-Lib/pandas-ta, backtesting.py, watchdog, pyyaml, psycopg2-binary
- [x] 1.3 创建 config/ 目录下的 exchanges.yaml、strategies.yaml、risk.yaml 模板文件
- [x] 1.4 创建各模块包的 __init__.py 文件

## 2. 配置系统

- [x] 2.1 实现 config_loader.py，支持 YAML 文件加载和类型校验
- [x] 2.2 实现 exchanges.yaml 解析（API Key、Secret、数据库连接）
- [x] 2.3 实现 strategies.yaml 解析（策略列表、timeframe、币种、mode）
- [x] 2.4 实现 risk.yaml 解析（杠杆上限、止损、仓位限制）
- [x] 2.5 实现 watchdog 热重载机制，监听 strategies.yaml 和 risk.yaml 变更
- [x] 2.6 实现 .pause 文件检测（紧急暂停）
- [x] 2.7 实现配置文件校验和错误提示

## 3. 日志系统

- [x] 3.1 配置 Python logging，支持终端输出和文件写入
- [x] 3.2 实现交易信号日志输出（signals.log）
- [x] 3.3 实现交易执行日志输出（trades.log）
- [x] 3.4 实现各模块的分级日志输出

## 4. 数据库层

- [x] 4.1 创建 PostgreSQL 连接管理模块
- [x] 4.2 创建数据库初始化脚本，建表（klines、orders、signals、positions）
- [x] 4.3 实现 klines 表的插入和查询方法
- [x] 4.4 实现 orders 表的 CRUD 方法
- [x] 4.5 实现 signals 表的插入方法
- [x] 4.6 实现 positions 表的 CRUD 方法
- [x] 4.7 实现 K 线数据的去重写入（upsert）

## 5. 交易所适配器

- [x] 5.1 实现 ExchangeAdapter 抽象基类
- [x] 5.2 实现 BinanceAdapter（基于 ccxt），支持现货和合约
- [x] 5.3 实现 fetch_ohlcv 方法，获取 K 线数据
- [x] 5.4 实现 fetch_balance 方法，获取账户余额
- [x] 5.5 实现 place_order 方法，支持市价单和限价单
- [x] 5.6 实现 cancel_order 方法
- [x] 5.7 实现 get_positions 方法，获取合约持仓
- [x] 5.8 实现自动重连逻辑（API 异常时重试）
- [x] 5.9 实现 paper trading 模拟下单和持仓管理

## 6. 技术分析模块

- [x] 6.1 实现 indicators.py，封装 TA-Lib 指标计算（RSI、EMA、MACD、布林带）
- [ ] 6.2 实现 pandas-ta 备选方案（TA-Lib 不可用时回退）
- [x] 6.3 实现 support_resistance.py，基于历史高低点聚类识别支撑/压力位
- [x] 6.4 实现成交量分布（Volume Profile）计算
- [x] 6.5 实现支撑/压力位强度评分（触碰次数 + 成交量）

## 7. 策略框架

- [x] 7.1 实现 BaseStrategy 抽象基类（on_bar 方法定义）
- [x] 7.2 实现 Signal 数据模型（strategy_name、symbol、side、price、stop_loss、leverage）
- [x] 7.3 实现策略注册和自动发现机制
- [x] 7.4 实现 SupportResistanceStrategy（支撑/压力位策略示例）
- [x] 7.5 实现策略参数从 YAML 配置加载
- [x] 7.6 实现多周期多币种策略调度逻辑

## 8. 风控模块

- [x] 8.1 实现 RiskManager 核心类
- [x] 8.2 实现杠杆上限检查（主流币 5x / 山寨币 3x）
- [x] 8.3 实现单笔止损计算和检查（仓位价值百分比）
- [x] 8.4 实现单笔最大亏损限制（保证金 15%）
- [x] 8.5 实现自动降杠杆计算
- [x] 8.6 实现日累计亏损限制（保证金 30%）
- [x] 8.7 实现单币种仓位占比限制
- [x] 8.8 实现最大同时持仓数限制
- [x] 8.9 实现主流币/山寨币自动分类

## 9. 调度引擎

- [x] 9.1 实现基于 asyncio 的多周期定时器
- [x] 9.2 实现 K 线数据拉取 → 策略执行 → 信号处理 的完整周期
- [x] 9.3 实现策略启停控制
- [x] 9.4 实现执行耗时和结果记录

## 10. 回测引擎

- [x] 10.1 实现 backtest/runner.py，封装 backtesting.py
- [x] 10.2 实现从 PostgreSQL 加载历史 K 线数据
- [x] 10.3 实现策略适配 backtesting.py 的 Backtest 类
- [x] 10.4 实现回测结果报告（收益率、最大回撤、夏普比率、胜率）
- [x] 10.5 实现参数优化（网格搜索）
- [ ] 10.6 实现回测命令行入口

## 11. 主引擎集成

- [x] 11.1 实现 main.py 入口，串联配置加载 → 数据库初始化 → 交易所连接 → 策略调度
- [x] 11.2 实现信号处理管道：策略信号 → 风控检查 → 执行/记录
- [x] 11.3 实现 paper / live_confirm / live_auto 三种模式的切换逻辑
- [x] 11.4 实现优雅退出（停止所有定时任务、保存状态）
- [x] 11.5 编写 README.md，包含安装、配置和运行说明
