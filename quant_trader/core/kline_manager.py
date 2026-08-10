"""K 线数据管理器 - 协调 WebSocket 和 REST 两种 K 线获取方式。

设计目标：
1. WebSocket 优先，断连时降级到 REST，重连后切回
2. 维护 K 线缓存和实时价格缓存
3. 提供订阅接口（Queue），策略层无感知数据来源
4. 支持动态增减订阅（配置热重载）
"""

import asyncio
import logging
from datetime import datetime, timezone

from .data_store import DataStore, KlineRecord
from .kline_fetcher import RESTKlineFetcher, WebSocketKlineFetcher

logger = logging.getLogger(__name__)


class KlineDataManager:
    """K 线数据管理器 - 协调两个 fetcher，对外提供统一接口。"""

    def __init__(
        self,
        exchange,
        data_store: DataStore,
        timeframes_seconds: dict[str, int],
        buffer_seconds: int = 1,
        proxy: str | None = None,
    ):
        self.exchange = exchange
        self.data_store = data_store
        self._timeframes_seconds = timeframes_seconds
        self.buffer_seconds = buffer_seconds
        self.proxy = proxy

        # (symbol, timeframe) -> list[dict]  K 线缓存
        self._klines_cache: dict[tuple[str, str], list[dict]] = {}
        # symbol -> 实时价格
        self._realtime_prices: dict[str, float] = {}
        # (symbol, timeframe) -> list[asyncio.Queue]  订阅者
        self._subscribers: dict[tuple[str, str], list[asyncio.Queue]] = {}
        # 当前订阅的所有 (symbol, timeframe)
        self._symbol_timeframes: set[tuple[str, str]] = set()

        # 两个 fetcher
        self.ws_fetcher: WebSocketKlineFetcher | None = None
        self.rest_fetcher: RESTKlineFetcher | None = None
        # 当前数据来源："ws" | "rest"
        self._active_source: str = "ws"

    def _on_kline_update(
        self,
        symbol: str,
        timeframe: str,
        klines: list[dict],
        event: str,
        source: str,
    ) -> None:
        """Fetcher 回调入口：更新缓存、存数据库、通知订阅者。"""
        if not klines:
            return

        # 始终更新实时价格和 K 线缓存
        self._realtime_prices[symbol] = klines[0]["close"]
        self._klines_cache[(symbol, timeframe)] = klines

        # WebSocket 恢复推送 → 切回 ws，停止 rest
        if source == "ws" and self._active_source == "rest":
            self._active_source = "ws"
            logger.info("WebSocket 恢复，切回 ws 数据源")
            if self.rest_fetcher is not None:
                asyncio.create_task(self.rest_fetcher.stop())

        # closed 事件：存数据库
        if event == "closed":
            self._store_klines(klines, symbol, timeframe)

        # 通知订阅者
        key = (symbol, timeframe)
        for queue in self._subscribers.get(key, []):
            try:
                queue.put_nowait({"klines": klines, "event": event})
            except asyncio.QueueFull:
                pass

    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动 K 线管理：创建并启动 WebSocket fetcher。"""
        self._symbol_timeframes = set(symbol_timeframes)

        # 创建 fetcher
        self.ws_fetcher = WebSocketKlineFetcher(
            rest_exchange=self.exchange,
            on_kline=lambda sym, tf, kl, ev: self._on_kline_update(sym, tf, kl, ev, "ws"),
            on_fallback_to_rest=self._fallback_to_rest,
            timeframes_seconds=self._timeframes_seconds,
            proxy=self.proxy,
        )

        # 启动 WebSocket fetcher
        await self.ws_fetcher.start(symbol_timeframes)
        logger.info("KlineDataManager 已启动，%d 个订阅", len(symbol_timeframes))

    def _fallback_to_rest(self) -> None:
        """WebSocket 3次重连失败时触发：启动 REST fetcher。"""
        if self.rest_fetcher is not None and self.rest_fetcher.is_running:
            return  # REST 已在运行
        logger.warning("降级到 REST K 线拉取")
        self._active_source = "rest"
        self.rest_fetcher = RESTKlineFetcher(
            exchange=self.exchange,
            on_kline=lambda sym, tf, kl, ev: self._on_kline_update(sym, tf, kl, ev, "rest"),
            timeframes_seconds=self._timeframes_seconds,
            buffer_seconds=self.buffer_seconds,
        )
        asyncio.create_task(self.rest_fetcher.start(self._symbol_timeframes))

    async def update_subscriptions(self, new_set: set[tuple[str, str]]) -> None:
        """配置热重载后更新订阅集合。"""
        added = new_set - self._symbol_timeframes
        removed = self._symbol_timeframes - new_set

        if not added and not removed:
            return

        # 更新 fetcher 订阅
        if self.ws_fetcher is not None and self.ws_fetcher.is_running:
            if added:
                await self.ws_fetcher.subscribe(added)
            if removed:
                await self.ws_fetcher.unsubscribe(removed)

        if self.rest_fetcher is not None and self.rest_fetcher.is_running:
            if added:
                await self.rest_fetcher.subscribe(added)
            if removed:
                await self.rest_fetcher.unsubscribe(removed)

        # 更新本地集合
        self._symbol_timeframes = set(new_set)

        # 清理被移除的缓存
        for key in removed:
            self._klines_cache.pop(key, None)
            self._subscribers.pop(key, None)

        logger.info("订阅已更新: 新增=%d, 移除=%d", len(added), len(removed))

    def subscribe(self, symbol: str, timeframe: str) -> asyncio.Queue:
        """订阅 K 线更新事件。返回 asyncio.Queue，消息格式：
            {"klines": list[dict], "event": "update" | "closed"}
        """
        key = (symbol, timeframe)
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscribers[key].append(queue)
        return queue

    def get_klines(self, symbol: str, timeframe: str) -> list[dict] | None:
        """获取当前缓存的 K 线数据（不触发拉取）。"""
        return self._klines_cache.get((symbol, timeframe))

    def get_realtime_price(self, symbol: str) -> float | None:
        """获取实时价格（来自 WebSocket 推送或 REST 最近一次拉取）。"""
        return self._realtime_prices.get(symbol)

    def _store_klines(self, klines: list[dict], symbol: str, timeframe: str):
        """将 K 线数据存入数据库（仅 closed 事件触发）。"""
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

    async def shutdown(self) -> None:
        """优雅关闭。"""
        if self.ws_fetcher is not None:
            await self.ws_fetcher.stop()
        if self.rest_fetcher is not None:
            await self.rest_fetcher.stop()
        logger.info("KlineDataManager 已关闭")
