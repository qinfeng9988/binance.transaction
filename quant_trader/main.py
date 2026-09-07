"""主引擎 - 协调配置加载、交易所连接、策略调度、风控和信号处理。"""

import asyncio
import logging
import random
import time as _time
from pathlib import Path

from .core.config_loader import ConfigLoader
from .core.data_store import DataStore, OrderRecord, SignalRecord
from .core.kline_manager import KlineDataManager
from .core.logger import setup_logging
from .core.query_server import QueryServer
from .core.signal_router import handle_signal
from .core.time_utils import PERIODS
from .exchange.binance_adapter import BinanceAdapter
from .exchange.paper_trading import PaperTradingEngine
from .models import Signal
from .risk.manager import RiskManager

logger = logging.getLogger(__name__)

# 周期到秒的映射（从 time_utils.PERIODS 派生，单一来源）
TIMEFRAME_SECONDS = {name: seconds for name, (seconds, _parent) in PERIODS.items()}

# 当前周期 → 上级周期映射（多周期趋势确认；从 PERIODS 派生，终端周期无父级不在此列）
PARENT_TIMEFRAME = {name: parent for name, (_seconds, parent) in PERIODS.items() if parent}


def collect_subscriptions(strategies) -> set[tuple[str, str]]:
    """收集需要订阅的 (symbol, timeframe) 集合，含 parent 周期。

    parent 周期用于多周期趋势确认（detect_trend_with_parent）。若不订阅，
    实盘/paper 下 parent_klines 恒为 None，require_parent_ema_aligned /
    strict_bias_mode / use_parent_trend_override 等过滤会静默失效，
    与回测（驱动器显式拉取 parent 数据）行为不一致。

    parent 周期来自注册表（time_utils.PERIODS），父级均在 TIMEFRAME_SECONDS
    内（含 30m/3d/1w），不会再发生 1d→1w 被静默丢弃的情况。
    """
    subs: set[tuple[str, str]] = set()
    for strategy in strategies:
        if not strategy.enabled:
            continue
        for symbol in strategy.symbols:
            for tf in strategy.timeframes:
                subs.add((symbol, tf))
                parent_tf = PARENT_TIMEFRAME.get(tf)
                if parent_tf and parent_tf in TIMEFRAME_SECONDS:
                    subs.add((symbol, parent_tf))
    return subs


def position_value_ratio_for(strategy_name: str, strategies) -> float:
    """按策略名解析开仓仓位占余额比例（策略参数 position_value_ratio）。

    未配置或找不到策略时回退默认 0.1（余额的 10%），与历史行为一致。
    回测（backtest_runner）与实盘 paper（Engine._handle_signal）共用
    同一参数语义，保证两条路径仓位行为一致。
    """
    for s in strategies:
        if s.name == strategy_name:
            return float(s.params.get("position_value_ratio", 0.1))
    return 0.1


class Engine:
    """交易引擎核心，负责调度策略、处理信号、管理生命周期。"""

    def __init__(self):
        self.config = ConfigLoader()
        self.data_store: DataStore | None = None
        self.exchange: BinanceAdapter | None = None
        self.paper_engine: PaperTradingEngine | None = None
        self.risk_manager: RiskManager | None = None
        self.status_server: QueryServer | None = None
        self._running = False
        self._paused = False
        self._strategy_cache: dict | None = None
        self.kline_manager: KlineDataManager | None = None
        self._last_signal_time: dict[str, float] = {}  # symbol → 最后处理信号的时间戳（epoch秒）
        self._signal_cooldown = 220                      # 同币种最短信号间隔（秒），与回测保持一致
        self._funding_rate_cache: dict[str, tuple[float, float]] = {}  # symbol → (rate_pct, timestamp)
        self._FUNDING_RATE_TTL = 60                      # funding rate 缓存有效期（秒）
        self._loop: asyncio.AbstractEventLoop | None = None  # 主事件循环（热重载回调跨线程调度用）

    def initialize(self):
        """初始化：加载配置、连接数据库和交易所。"""
        setup_logging()
        logger.info("正在初始化量化交易引擎...")

        # 加载所有配置
        exchange_cfg = self.config.load_static()
        self.config.load_strategies()
        self.config.load_risk()

        # 连接数据库
        self.data_store = DataStore(
            host=exchange_cfg.db_host,
            port=exchange_cfg.db_port,
            dbname=exchange_cfg.db_name,
            user=exchange_cfg.db_user,
            password=exchange_cfg.db_password,
        )
        self.data_store.connect()

        # 初始化风控模块（需在模拟引擎之前，引擎平仓时回调风控）
        self.risk_manager = RiskManager(self.config.risk, self.data_store)

        # 初始化模拟交易引擎（平仓时通知风控，驱动连续亏损冷静期/日亏损限制）
        self.paper_engine = PaperTradingEngine(
            initial_balance=100000.0,
            default_type=exchange_cfg.default_type,
            risk_manager=self.risk_manager,
        )
        logger.info("模拟交易引擎已初始化: 初始资金=%.0f USDT", 100000.0)

        # 从数据库恢复持仓状态（程序重启后继续之前的模拟交易）
        self.paper_engine.restore_from_db(self.data_store)

        # 连接交易所（行情数据）
        self.exchange = BinanceAdapter(
            api_key=exchange_cfg.api_key,
            api_secret=exchange_cfg.api_secret,
            sandbox=exchange_cfg.sandbox,
            default_type=exchange_cfg.default_type,
            proxy=exchange_cfg.proxy or None,
        )
        self.exchange.connect()

        # 初始化 K 线数据管理器
        self.kline_manager = KlineDataManager(
            exchange=self.exchange,
            data_store=self.data_store,
            timeframes_seconds=TIMEFRAME_SECONDS,
            buffer_seconds=exchange_cfg.kline_buffer_seconds,
            proxy=exchange_cfg.proxy or None,
        )

        # 初始化状态 HTTP 服务
        self.status_server = QueryServer(
            self,
            host=exchange_cfg.status_host,
            port=exchange_cfg.status_port,
        )

        # 启动配置文件热重载监听
        self.config.start_watching(on_change=self._on_config_change)

        logger.info("引擎初始化完成。模式: %s", self.config.strategies.mode)

    def _on_config_change(self, filename: str):
        """配置热重载回调：清空策略缓存 + 更新 K 线订阅。"""
        logger.info("配置已重新加载: %s", filename)
        self._strategy_cache = None  # 缓存失效，下次重建

        # 仅 strategies.yaml 变更时才更新订阅。
        # 本回调在 watchdog 观察者线程执行，必须用 run_coroutine_threadsafe
        # 把协程调度回主事件循环（asyncio.create_task 在非循环线程会抛
        # RuntimeError 且被 config_loader 吞掉，导致订阅永不更新）
        if filename == "strategies.yaml" and self.kline_manager is not None:
            new_set = collect_subscriptions(self.config.strategies.strategies)
            if self._loop is not None and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self.kline_manager.update_subscriptions(new_set), self._loop,
                )
            else:
                logger.debug("事件循环未运行，跳过 K 线订阅更新")

    def _is_paused(self) -> bool:
        """检测 .pause 文件是否存在（紧急暂停机制）。"""
        pause_file = Path(__file__).parent.parent / ".pause"
        return pause_file.exists()

    async def _get_funding_rate(self, symbol: str) -> float | None:
        """获取当前资金费率（带缓存）。

        返回:
            funding rate（百分比），如 0.05 表示 +0.05%；
            API 失败或数据无效时返回 None。
        """
        now = _time.time()
        cached = self._funding_rate_cache.get(symbol)
        if cached and (now - cached[1]) < self._FUNDING_RATE_TTL:
            return cached[0]

        if self.exchange is None:
            return None

        try:
            result = await self.exchange.fetch_funding_rate(symbol)
            if result and "fundingRate" in result:
                rate = float(result["fundingRate"]) * 100  # 转为百分比
                self._funding_rate_cache[symbol] = (rate, now)
                logger.debug("Funding rate %s: %.4f%%", symbol, rate)
                return rate
        except Exception:
            logger.warning("获取 funding rate 失败 %s", symbol, exc_info=True)

        return None

    async def run(self):
        """启动引擎，并行运行 K 线拉取和策略调度。"""
        self._running = True
        self._loop = asyncio.get_running_loop()
        logger.info("引擎已启动")

        # 预热策略缓存
        self._load_strategies()

        # 收集所有需要去重的 (symbol, timeframe) 组合（含 parent 周期）
        symbol_timeframes = collect_subscriptions(self.config.strategies.strategies)
        strategy_configs: list[tuple[str, str, str]] = []  # (strategy_name, symbol, timeframe)

        for strategy in self.config.strategies.strategies:
            if not strategy.enabled:
                continue
            for symbol in strategy.symbols:
                for tf in strategy.timeframes:
                    strategy_configs.append((strategy.name, symbol, tf))

        # 先启动状态 HTTP 服务
        await self.status_server.start()

        # 启动 K 线拉取任务
        kline_task = asyncio.create_task(
            self.kline_manager.start(symbol_timeframes)
        )

        # 启动策略执行任务
        strategy_tasks = []
        for strategy_name, symbol, timeframe in strategy_configs:
            strategy_tasks.append(
                self._run_strategy_cycle(strategy_name, symbol, timeframe)
            )

        # 错开各策略的启动时间（随机延迟），避免多策略在同一时刻对同一币种发单冲突
        for i, task in enumerate(strategy_tasks):
            if i > 0:
                await asyncio.sleep(random.uniform(2, 8))

        # 并发运行所有任务
        await asyncio.gather(kline_task, *strategy_tasks, return_exceptions=True)

    async def _run_strategy_cycle(self, strategy_name: str, symbol: str, timeframe: str):
        """单个策略+币种+周期的循环执行。订阅 K 线更新事件，等待通知后执行策略。"""
        interval = TIMEFRAME_SECONDS.get(timeframe, 300)
        logger.info("策略周期已启动: %s %s %s (间隔=%d秒)", strategy_name, symbol, timeframe, interval)

        # 订阅 K 线更新事件
        kline_queue = self.kline_manager.subscribe(symbol, timeframe)
        logger.info("策略 %s 已订阅 %s %s K 线更新", strategy_name, symbol, timeframe)

        while self._running:
            try:
                # 检查紧急暂停
                if self._is_paused():
                    self._paused = True
                    await asyncio.sleep(min(interval, 5))
                    continue
                self._paused = False

                # 等待 K 线更新通知（最多等待 interval 秒）
                try:
                    msg = await asyncio.wait_for(kline_queue.get(), timeout=interval)
                except asyncio.TimeoutError:
                    logger.debug("等待 K 线更新超时: %s %s", symbol, timeframe)
                    continue

                klines = msg["klines"]
                event = msg["event"]
                if not klines:
                    logger.warning("收到空 K 线数据: %s %s", symbol, timeframe)
                    continue

                # 始终更新模拟持仓价格（触发止损检查）
                current_price = klines[0]["close"]
                self.paper_engine.update_prices(symbol, current_price, self.data_store)

                # 只有 closed 事件才执行策略（K 线已收盘）
                if event != "closed":
                    continue

                if len(klines) < 2:
                    logger.debug("K线数据不足，跳过策略执行: %s %s", symbol, timeframe)
                    continue

                # 执行策略 — klines[0] 是刚收盘的 K 线
                strategy = self._get_strategy(strategy_name)
                if strategy:
                    # 获取上级周期 K 线（多周期趋势确认）
                    parent_tf = PARENT_TIMEFRAME.get(timeframe)
                    parent_klines = self.kline_manager.get_klines(symbol, parent_tf) if parent_tf else None
                    # 获取当前 funding rate（带缓存）
                    funding_rate = await self._get_funding_rate(symbol)
                    signals = strategy.on_bar(symbol, timeframe, klines,
                                              self.paper_engine.positions.values(),
                                              parent_klines=parent_klines,
                                              funding_rate=funding_rate)
                    now = _time.time()
                    # 退出管理信号优先执行，无冷却期（v1.6.0 解耦后需每 bar 无条件执行）
                    for signal in signals:
                        if signal.action in ("update_stop", "partial_close"):
                            await self._handle_signal(signal)
                    # 开仓/全平信号，保持冷却期 + 每根 K 线只执行一个
                    for signal in signals:
                        if signal.action in ("update_stop", "partial_close"):
                            continue  # 退出信号已处理
                        last = self._last_signal_time.get(signal.symbol, 0)
                        if now - last < self._signal_cooldown:
                            logger.debug("跳过冷却期内的信号: %s %s", signal.symbol, signal.signal_type)
                            continue
                        await self._handle_signal(signal)
                        self._last_signal_time[signal.symbol] = now
                        break  # 每根K线只处理一个开仓信号，与回测保持一致

                # 定期打印账户概览（每 10 个周期）
                if hasattr(self, "_cycle_count"):
                    self._cycle_count += 1
                else:
                    self._cycle_count = 1

                # 同步 filter summary 到数据库（每周期同步，保证 status 命令看到最新数据）
                if strategy:
                    try:
                        fs = strategy.get_filter_summary()
                        for direction, stats in fs.items():
                            if stats.get("checked", 0) > 0:
                                self.data_store.upsert_filter_stats(
                                    strategy_name, symbol, timeframe, direction, stats,
                                )
                    except Exception:
                        logger.debug("Filter stats 同步失败", exc_info=True)

                # 定期打印账户概览（每 10 个周期）
                if self._cycle_count % 10 == 0:
                    summary = self.paper_engine.get_summary()
                    logger.info(
                        "[模拟账户] 权益=%.2f USDT, 余额=%.2f, 持仓=%d, "
                        "交易=%d, 胜率=%.1f%%, 收益=%.2f%%",
                        summary["total_equity"], summary["balance"],
                        summary["open_positions"], summary["total_trades"],
                        summary["win_rate"], summary["return_pct"],
                    )

            except Exception:
                logger.exception("策略周期执行异常: %s %s %s", strategy_name, symbol, timeframe)

    async def _handle_signal(self, signal: Signal):
        """处理策略信号：记录、风控、执行。

        paper 模式（含回测）走 signal_router.handle_signal 共享函数；
        live_confirm / live_auto 由本方法直接处理（实盘专属逻辑）。
        """
        mode = self.config.strategies.mode

        if mode == "paper":
            # paper 模式：复用 signal_router 共享函数（回测与实盘共用）
            handle_signal(
                signal=signal,
                paper_engine=self.paper_engine,
                data_store=self.data_store,
                risk_manager=self.risk_manager,
                mode="paper",
                position_value_ratio=position_value_ratio_for(
                    signal.strategy_name, self.config.strategies.strategies,
                ),
            )
            return

        # 以下为实盘专属逻辑（live_confirm / live_auto）
        logger.info(
            "信号: %s %s %s 价格=%.2f 止损=%.2f 杠杆=%dx",
            signal.symbol, signal.signal_type, signal.timeframe,
            signal.price, signal.stop_loss, signal.leverage,
        )

        # 写入信号日志
        sig_logger = logging.getLogger("signals")
        sig_logger.info(
            "%s %s %s 价格=%.2f 止损=%.2f 杠杆=%dx",
            signal.symbol, signal.signal_type, signal.strategy_name,
            signal.price, signal.stop_loss, signal.leverage,
        )

        # 写入数据库
        signal_id = self.data_store.insert_signal(SignalRecord(
            strategy_name=signal.strategy_name,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            signal_type=signal.signal_type,
            price=signal.price,
            stop_loss=signal.stop_loss,
            timestamp=signal.timestamp,
            leverage=signal.leverage,
        ))

        # 实盘模式同样需要支持非开仓类信号
        if signal.action == "update_stop":
            if self.paper_engine:
                updated = self.paper_engine.update_stop_loss(signal.symbol, signal.stop_loss)
                if updated:
                    logger.info("[止损更新] %s 新止损=%.2f", signal.symbol, signal.stop_loss)
            return

        if signal.action == "partial_close":
            if self.paper_engine:
                tp_level = signal.metadata.get("partial_tp_level") if signal.metadata else None
                net_pnl = self.paper_engine.partial_close_position(
                    signal.symbol, signal.price, signal.close_ratio, self.data_store,
                    signal_id=signal_id, partial_tp_level=tp_level,
                )
                if net_pnl is not None:
                    logger.info(
                        "[部分平仓] %s 比例=%.2f 价格=%.2f 盈亏=%.2f",
                        signal.symbol, signal.close_ratio, signal.price, net_pnl,
                    )
            return

        if signal.action == "close":
            if self.paper_engine:
                existing = self.paper_engine.positions.get(signal.symbol)
                if existing:
                    net_pnl = self.paper_engine.close_position(
                        signal.symbol, signal.price, self.data_store,
                        order_type="close", signal_id=signal_id,
                        trigger_reason=signal.metadata.get("reason", "signal_close"),
                    )
                    if net_pnl is not None:
                        logger.info(
                            "[平仓] %s 价格=%.2f 盈亏=%.2f 原因=%s",
                            signal.symbol, signal.price, net_pnl,
                            signal.metadata.get("reason", "close_signal"),
                        )
            return

        # 开仓类信号：实盘走风控 + 实盘下单
        if mode == "live_confirm":
            logger.info("[实盘确认] 信号待审批: %s %s", signal.symbol, signal.signal_type)
            # TODO: 接入通知通道（钉钉/邮件/Telegram）
        elif mode == "live_auto":
            risk_result = self.risk_manager.check_and_adjust(signal)
            if not risk_result["approved"]:
                logger.warning("[风控拒绝] %s %s: %s", signal.symbol, signal.signal_type, risk_result["reason"])
                return
            signal.leverage = risk_result["leverage"]
            await self._execute_live_trade(signal, signal_id)

    def _execute_paper_trade(self, signal: Signal, signal_id: int = None):
        """模拟交易执行。"""
        # 计算下单数量（默认用总资金的 10%）
        position_value = self.paper_engine.balance * 0.1
        quantity = position_value / signal.price

        # 检查是否为反向平仓信号（持有 buy 仓收到 sell 信号，或持有 sell 仓收到 buy 信号）
        existing_pos = self.paper_engine.positions.get(signal.symbol)
        is_closing = existing_pos is not None and signal.signal_type != existing_pos.side

        # place_order 内部会自动同步 positions 和 orders 表
        order = self.paper_engine.place_order(
            symbol=signal.symbol,
            side=signal.signal_type,
            amount=quantity,
            price=signal.price,
            leverage=signal.leverage,
            stop_loss=signal.stop_loss,
            data_store=self.data_store,
            metadata=signal.metadata,
            order_type="close" if is_closing else None,
            signal_id=signal_id,
        )

        trade_logger = logging.getLogger("trades")
        trade_logger.info(
            "[模拟成交] %s %s 价格=%.2f 数量=%.4f 杠杆=%dx 订单=%s",
            signal.symbol, signal.signal_type, signal.price,
            quantity, signal.leverage, order.order_id if order else "FAILED",
        )

    async def _execute_live_trade(self, signal: Signal, signal_id: int = None):
        """实盘下单（待 API Key 配置后启用）。"""
        quantity = (self.paper_engine.balance * 0.1) / signal.price

        # TODO: 调用真实交易所下单
        # order = await self.exchange.place_order(...)
        logger.info(
            "[实盘下单] %s %s 价格=%.2f 数量=%.4f 杠杆=%dx",
            signal.symbol, signal.signal_type, signal.price,
            quantity, signal.leverage,
        )

    def _load_strategies(self) -> dict:
        """根据配置实例化策略对象。"""
        from .strategies.s_r_levels import SupportResistanceStrategy
        from .strategies.bollinger_bands import BollingerBandsStrategy

        # 策略注册表：名称 -> 策略类
        # 多个币种策略可映射到同一个策略类，通过不同参数区分
        strategy_registry = {
            "support_resistance": SupportResistanceStrategy,
            "support_resistance_btc": SupportResistanceStrategy,
            "support_resistance_eth": SupportResistanceStrategy,
            "support_resistance_eth_5m": SupportResistanceStrategy,
            "support_resistance_sol": SupportResistanceStrategy,
            "support_resistance_bnb": SupportResistanceStrategy,
            "support_resistance_paxg": SupportResistanceStrategy,
            "bollinger_bands_paxg": BollingerBandsStrategy,
        }

        result = {}
        for cfg in self.config.strategies.strategies:
            cls = strategy_registry.get(cfg.name)
            if cls:
                result[cfg.name] = cls(cfg.name, cfg.params, self.config)

        self._strategy_cache = result
        return result

    def _get_strategy(self, name: str):
        """按名称获取策略实例（优先从缓存）。"""
        if self._strategy_cache is None:
            self._load_strategies()
        return self._strategy_cache.get(name)

    async def shutdown(self):
        """优雅退出：停止调度、关闭连接、打印账户概览。"""
        self._running = False
        logger.info("正在关闭引擎...")

        # 打印最终账户概览
        if self.paper_engine:
            summary = self.paper_engine.get_summary()
            logger.info("========== 最终账户概略 ==========")
            logger.info("初始资金:    %.2f USDT", summary["initial_balance"])
            logger.info("总权益:      %.2f USDT", summary["total_equity"])
            logger.info("可用余额:    %.2f USDT", summary["balance"])
            logger.info("总交易次数:  %d", summary["total_trades"])
            logger.info("胜率:        %.2f%%", summary["win_rate"])
            logger.info("总盈亏:      %.2f USDT", summary["total_pnl"])
            logger.info("总手续费:    %.2f USDT", summary["total_fees"])
            logger.info("收益率:      %.2f%%", summary["return_pct"])
            logger.info("==================================")

        # 停止状态 HTTP 服务
        if self.status_server:
            await self.status_server.stop()

        self.config.stop_watching()
        if self.exchange:
            await self.exchange.close()
        if self.data_store:
            self.data_store.close()
        logger.info("引擎已停止")


def main():
    """程序入口。"""
    engine = Engine()
    try:
        engine.initialize()
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        logger.info("用户中断")
        asyncio.run(engine.shutdown())
    except Exception:
        logger.exception("引擎崩溃")


if __name__ == "__main__":
    main()
