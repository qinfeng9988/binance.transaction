"""主引擎 - 协调配置加载、交易所连接、策略调度、风控和信号处理。"""

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.config_loader import ConfigLoader
from core.data_store import DataStore, OrderRecord, SignalRecord
from core.logger import setup_logging
from exchange.binance_adapter import BinanceAdapter
from exchange.paper_trading import PaperTradingEngine
from models import Signal
from risk.manager import RiskManager

logger = logging.getLogger(__name__)

# 周期到秒的映射
TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


class Engine:
    """交易引擎核心，负责调度策略、处理信号、管理生命周期。"""

    def __init__(self):
        self.config = ConfigLoader()
        self.data_store: DataStore | None = None
        self.exchange: BinanceAdapter | None = None
        self.paper_engine: PaperTradingEngine | None = None
        self.risk_manager: RiskManager | None = None
        self._running = False
        self._paused = False
        self._strategy_cache: dict | None = None

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

        # 初始化模拟交易引擎
        self.paper_engine = PaperTradingEngine(
            initial_balance=100000.0,
            default_type=exchange_cfg.default_type,
        )
        logger.info("模拟交易引擎已初始化: 初始资金=%.0f USDT", 100000.0)

        # 从数据库恢复持仓状态（程序重启后继续之前的模拟交易）
        self.paper_engine.restore_from_db(self.data_store)

        # 初始化风控模块
        self.risk_manager = RiskManager(self.config.risk, self.data_store)

        # 连接交易所（行情数据）
        self.exchange = BinanceAdapter(
            api_key=exchange_cfg.api_key,
            api_secret=exchange_cfg.api_secret,
            sandbox=exchange_cfg.sandbox,
            default_type=exchange_cfg.default_type,
            proxy=exchange_cfg.proxy or None,
        )
        self.exchange.connect()

        # 启动配置文件热重载监听
        self.config.start_watching(on_change=self._on_config_change)

        logger.info("引擎初始化完成。模式: %s", self.config.strategies.mode)

    def _on_config_change(self, filename: str):
        """配置热重载回调，清空策略缓存。"""
        logger.info("配置已重新加载: %s", filename)
        self._strategy_cache = None  # 缓存失效，下次重建

    def _is_paused(self) -> bool:
        """检测 .pause 文件是否存在（紧急暂停机制）。"""
        pause_file = Path(__file__).parent.parent / ".pause"
        return pause_file.exists()

    async def run(self):
        """启动引擎，并行运行所有策略周期。"""
        self._running = True
        logger.info("引擎已启动")

        # 收集所有 (策略, 币种, 周期) 组合
        self._load_strategies()  # 预热缓存
        tasks = []

        seen: set[tuple[str, str, str]] = set()
        for strategy in self.config.strategies.strategies:
            if not strategy.enabled:
                continue
            for symbol in strategy.symbols:
                for tf in strategy.timeframes:
                    key = (strategy.name, symbol, tf)
                    if key not in seen:
                        seen.add(key)
                        tasks.append(self._run_cycle(strategy.name, symbol, tf))

        # 并发运行所有周期任务
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_cycle(self, strategy_name: str, symbol: str, timeframe: str):
        """单个策略+币种+周期的循环执行。"""
        interval = TIMEFRAME_SECONDS.get(timeframe, 300)
        logger.info("周期已启动: %s %s %s (间隔=%d秒)", strategy_name, symbol, timeframe, interval)

        while self._running:
            try:
                # 检查紧急暂停
                if self._is_paused():
                    self._paused = True
                    await asyncio.sleep(min(interval, 5))
                    continue
                self._paused = False

                # 拉取最新 K 线
                klines = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=200)
                if not klines:
                    logger.warning("无 K 线数据: %s %s", symbol, timeframe)
                    await asyncio.sleep(interval)
                    continue

                # 反转：ccxt 返回最旧在前，策略需要最新在前
                klines.reverse()

                # 存入数据库
                self._store_klines(klines, symbol, timeframe)

                # 更新模拟持仓价格（触发止损检查）
                current_price = klines[-1]["close"]
                self.paper_engine.update_prices(symbol, current_price, self.data_store)

                # 执行策略
                strategy = self._get_strategy(strategy_name)
                if strategy:
                    signals = strategy.on_bar(symbol, timeframe, klines, self.paper_engine.positions.values())
                    for signal in signals:
                        await self._handle_signal(signal)

                # 定期打印账户概览（每 10 个周期）
                if hasattr(self, "_cycle_count"):
                    self._cycle_count += 1
                else:
                    self._cycle_count = 1

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
                logger.exception("周期执行异常: %s %s %s", strategy_name, symbol, timeframe)

            await asyncio.sleep(interval)

    async def _handle_signal(self, signal: Signal):
        """处理策略信号：记录、风控、执行。"""
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
        self.data_store.insert_signal(SignalRecord(
            strategy_name=signal.strategy_name,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            signal_type=signal.signal_type,
            price=signal.price,
            stop_loss=signal.stop_loss,
            timestamp=signal.timestamp,
            leverage=signal.leverage,
        ))

        # 根据运行模式处理
        mode = self.config.strategies.mode
        if mode == "paper":
            # 模拟交易：风控检查后通过模拟引擎下单
            risk_result = self.risk_manager.check_and_adjust(signal)
            if not risk_result["approved"]:
                logger.warning("[风控拒绝] %s %s: %s", signal.symbol, signal.signal_type, risk_result["reason"])
                return

            signal.leverage = risk_result["leverage"]
            self._execute_paper_trade(signal)

        elif mode == "live_confirm":
            # 实盘+人工确认：推送通知等待确认
            logger.info("[实盘确认] 信号待审批: %s %s", signal.symbol, signal.signal_type)
            # TODO: 接入通知通道（钉钉/邮件/Telegram）

        elif mode == "live_auto":
            # 实盘全自动：风控检查后自动下单
            risk_result = self.risk_manager.check_and_adjust(signal)
            if not risk_result["approved"]:
                logger.warning("[风控拒绝] %s %s: %s", signal.symbol, signal.signal_type, risk_result["reason"])
                return

            signal.leverage = risk_result["leverage"]
            await self._execute_live_trade(signal)

    def _execute_paper_trade(self, signal: Signal):
        """模拟交易执行。"""
        from core.data_store import PositionRecord
        from datetime import datetime, timezone

        # 计算下单数量（默认用总资金的 10%）
        position_value = self.paper_engine.balance * 0.1
        quantity = position_value / signal.price

        # 检查是否为反向平仓信号（持有 buy 仓收到 sell 信号，或持有 sell 仓收到 buy 信号）
        existing_pos = self.paper_engine.positions.get(signal.symbol)
        is_closing = existing_pos is not None and signal.signal_type != existing_pos.side

        if is_closing:
            # 反向信号 = 平仓，不需要新建持仓记录（由 sync_position_to_db 处理）
            order = self.paper_engine.place_order(
                symbol=signal.symbol,
                side=signal.signal_type,
                amount=quantity,
                price=signal.price,
                leverage=signal.leverage,
                stop_loss=signal.stop_loss,
                data_store=self.data_store,
            )
            if order:
                self.data_store.insert_order(OrderRecord(
                    symbol=signal.symbol,
                    side=signal.signal_type,
                    price=signal.price,
                    quantity=existing_pos.quantity,  # 平仓数量为现有持仓数量
                    leverage=signal.leverage,
                    timestamp=signal.timestamp,
                    mode="paper",
                    order_id=order.order_id,
                    status=order.status,
                ))
        else:
            # 开仓或加仓
            order = self.paper_engine.place_order(
                symbol=signal.symbol,
                side=signal.signal_type,
                amount=quantity,
                price=signal.price,
                leverage=signal.leverage,
                stop_loss=signal.stop_loss,
                data_store=self.data_store,
            )

            if order:
                # 记录到数据库 - 订单
                self.data_store.insert_order(OrderRecord(
                    symbol=signal.symbol,
                    side=signal.signal_type,
                    price=signal.price,
                    quantity=quantity,
                    leverage=signal.leverage,
                    timestamp=signal.timestamp,
                    mode="paper",
                    order_id=order.order_id,
                    status=order.status,
                ))

                # 如果是新建持仓（非加仓），写入持仓记录到数据库
                if existing_pos is None:
                    pos = self.paper_engine.positions.get(signal.symbol)
                    if pos:
                        self.data_store.open_position(PositionRecord(
                            symbol=signal.symbol,
                            side=signal.signal_type,
                            entry_price=signal.price,
                            quantity=quantity,
                            leverage=signal.leverage,
                            entry_time=datetime.now(tz=timezone.utc),
                            stop_loss=signal.stop_loss,
                            status="open",
                        ))

        trade_logger = logging.getLogger("trades")
        trade_logger.info(
            "[模拟成交] %s %s 价格=%.2f 数量=%.4f 杠杆=%dx 订单=%s",
            signal.symbol, signal.signal_type, signal.price,
            quantity, signal.leverage, order.order_id if order else "FAILED",
        )

    async def _execute_live_trade(self, signal: Signal):
        """实盘下单（待 API Key 配置后启用）。"""
        quantity = (self.paper_engine.balance * 0.1) / signal.price

        # TODO: 调用真实交易所下单
        # order = await self.exchange.place_order(...)
        logger.info(
            "[实盘下单] %s %s 价格=%.2f 数量=%.4f 杠杆=%dx",
            signal.symbol, signal.signal_type, signal.price,
            quantity, signal.leverage,
        )

    def _store_klines(self, klines: list[dict], symbol: str, timeframe: str):
        """将 K 线数据写入数据库。"""
        from core.data_store import KlineRecord
        records = [
            KlineRecord(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(k["timestamp"] / 1000, tz=timezone.utc),
                open=k["open"],
                high=k["high"],
                low=k["low"],
                close=k["close"],
                volume=k["volume"],
            )
            for k in klines
        ]
        self.data_store.insert_klines(records)

    def _load_strategies(self) -> dict:
        """根据配置实例化策略对象。"""
        from strategies.s_r_levels import SupportResistanceStrategy

        # 策略注册表：名称 -> 策略类
        strategy_registry = {
            "support_resistance": SupportResistanceStrategy,
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
