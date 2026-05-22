## 为什么

构建一个基于 Python 的虚拟货币量化交易系统，使个人交易者能够将 K 线技术分析（支撑位/压力位识别）与消息面情感分析相结合，实现从手动盯盘到自动化交易的渐进式演进。Phase 1 聚焦于搭建可扩展的系统骨架，完成 Binance API 接入、策略框架、回测引擎和风控模块，为后续的智能化升级奠定基础。

## 变更内容

- **新增** 完整的项目骨架，包含交易所适配层、策略引擎、风控模块、回测引擎和数据存储层
- **新增** 基于 YAML 的分层配置系统（静态配置/动态热重载/紧急覆盖）
- **新增** 多周期（5m/1h/4h）多币种策略调度能力
- **新增** paper trading（模拟交易）模式，支持回测验证后再上实盘
- **新增** 基于 PostgreSQL 的历史 K 线存储和交易记录系统
- **新增** Binance 交易所适配器（基于 ccxt），支持现货和合约交易
- **新增** 支撑/压力位识别策略作为首个示例策略

## 功能 (Capabilities)

### 新增功能
- `exchange-adapter`: 交易所 API 抽象层，初始支持 Binance，适配器模式便于后续扩展
- `strategy-engine`: 策略框架，支持多周期多币种运行，可配置的模拟/实盘模式
- `risk-management`: 风控系统，包含止损、杠杆限制、仓位管理和自动降杠杆机制
- `backtesting`: 回测引擎，使用历史数据验证策略表现并优化参数
- `configuration`: YAML 配置系统，分层生效策略（静态/热重载/紧急覆盖）
- `data-storage`: PostgreSQL 数据层，存储 K 线历史、交易记录和信号日志
- `scheduling`: 调度引擎，支持多周期定时任务（5m/1h/4h 等）
- `technical-analysis`: 技术分析模块，包含 TA-Lib 指标封装和支撑/压力位识别
- `logging`: 日志系统，记录交易信号、执行结果和系统状态

### 修改功能

## 影响

- **新增项目**: `quant-trader/` 目录下的完整 Python 项目
- **新增依赖**: ccxt, pandas, TA-Lib, backtesting.py, apscheduler, watchdog, pyyaml, psycopg2
- **基础设施**: 需要本地 PostgreSQL 服务（用户已有）
- **交易所**: 需要 Binance API Key（Phase 1 可使用测试网或 paper trading）
- **不影响现有代码**: 从零开始的全新项目
