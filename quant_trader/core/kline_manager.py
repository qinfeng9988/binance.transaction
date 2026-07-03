"""K 线数据拉取模块 - 负责统一拉取、缓存和存储 K 线数据。

设计目标：
1. 按 (symbol, timeframe) 去重拉取，避免重复请求
2. K 线更新后触发事件，策略模块订阅执行
3. 支持多周期趋势确认所需的 parent_klines 缓存
4. 将 K 线数据存入数据库
"""

import asyncio
import logging
import time as _time
from datetime import datetime, timezone

from ..exchange.binance_adapter import BinanceAdapter
from ..core.data_store import DataStore, KlineRecord

logger = logging.getLogger(__name__)


class KlineDataManager:
    """K 线数据管理器 - 统一拉取、缓存、存储、分发 K 线数据。"""

    def __init__(self, exchange: BinanceAdapter, data_store: DataStore, buffer_seconds: int = 1):
        self.exchange = exchange
        self.data_store = data_store
        self.buffer_seconds = buffer_seconds
        # (symbol, timeframe) -> list[dict]  K 线缓存
        self._klines_cache: dict[tuple[str, str], list[dict]] = {}
        # (symbol, timeframe) -> list[asyncio.Queue]  等待 K 线的订阅者
        self._subscribers: dict[tuple[str, str], list[asyncio.Queue]] = {}
        self._running = False

    async def start(self, symbol_timeframes: set[tuple[str, str]]):
        """启动 K 线拉取任务。

        参数:
            symbol_timeframes: {(symbol, timeframe), ...} 需要去重的 K 线组合
        """
        self._running = True
        tasks = []
        for symbol, timeframe in symbol_timeframes:
            tasks.append(self._fetch_loop(symbol, timeframe))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _fetch_loop(self, symbol: str, timeframe: str):
        """单个 (symbol, timeframe) 的 K 线拉取循环。"""
        from ..main import TIMEFRAME_SECONDS
        interval = TIMEFRAME_SECONDS.get(timeframe, 300)
        logger.info("K 线拉取已启动: %s %s (间隔=%d秒)", symbol, timeframe, interval)

        while self._running:
            try:
                # 拉取最新 K 线
                klines = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=151)
                if not klines:
                    logger.warning("无 K 线数据: %s %s", symbol, timeframe)
                    await asyncio.sleep(interval)
                    continue

                # 反转：ccxt 返回最旧在前，策略需要最新在前
                klines.reverse()

                # 缓存 K 线数据
                self._klines_cache[(symbol, timeframe)] = klines

                # 存入数据库
                self._store_klines(klines, symbol, timeframe)

                # 通知所有订阅者
                key = (symbol, timeframe)
                if key in self._subscribers:
                    for queue in self._subscribers[key]:
                        try:
                            queue.put_nowait(klines)
                        except asyncio.QueueFull:
                            pass

            except Exception:
                logger.exception("K 线拉取异常: %s %s", symbol, timeframe)

            # 对齐到下一个K线收盘后buffer_seconds秒执行（使用Unix时间戳对齐到整点）
            now = _time.time()
            next_execution = ((int(now) // interval) + 1) * interval + self.buffer_seconds
            sleep_time = max(1, next_execution - now)
            await asyncio.sleep(sleep_time)

    def _store_klines(self, klines: list[dict], symbol: str, timeframe: str):
        """将 K 线数据存入数据库。"""
        records = []
        for kline in klines:
            record = KlineRecord(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(kline["timestamp"] / 1000, tz=timezone.utc),
                open=kline["open"],
                high=kline["high"],
                low=kline["low"],
                close=kline["close"],
                volume=kline["volume"],
            )
            records.append(record)
        self.data_store.insert_klines(records)

    def subscribe(self, symbol: str, timeframe: str) -> asyncio.Queue:
        """订阅 K 线更新事件。

        返回:
            asyncio.Queue: 每次 K 线更新时会收到最新的 K 线数据
        """
        key = (symbol, timeframe)
        queue = asyncio.Queue(maxsize=1)
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscribers[key].append(queue)
        return queue

    def get_klines(self, symbol: str, timeframe: str) -> list[dict] | None:
        """获取当前缓存的 K 线数据（不触发拉取）。"""
        return self._klines_cache.get((symbol, timeframe))

    async def shutdown(self):
        """优雅关闭。"""
        self._running = False
        logger.info("K 线拉取模块已关闭")
