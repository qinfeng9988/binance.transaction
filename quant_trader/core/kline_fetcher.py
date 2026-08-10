"""K 线数据获取层 - 提供 REST 和 WebSocket 两种实现。

设计目标：
1. 统一接口（BaseKlineFetcher），KlineDataManager 可透明切换
2. WebSocket 优先，REST 降级
3. 支持动态增减订阅（配置热重载）
"""

import asyncio
import logging
import time as _time
from abc import ABC, abstractmethod
from collections.abc import Callable

logger = logging.getLogger(__name__)


class BaseKlineFetcher(ABC):
    """K 线获取器抽象基类。

    子类需实现 start/stop/subscribe/unsubscribe 和 is_running 属性。
    收到 K 线数据时通过 on_kline 回调通知上层。

    回调签名:
        on_kline(symbol: str, timeframe: str, klines: list[dict], event: str)
        event 取值:
            "update" - K 线正在形成（WebSocket 实时推送）
            "closed" - K 线已收盘（策略执行时机）
    """

    def __init__(self, on_kline: Callable[[str, str, list[dict], str], None]):
        self._on_kline = on_kline
        self._symbol_timeframes: set[tuple[str, str]] = set()

    @abstractmethod
    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动 K 线获取循环。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止获取，释放资源。"""

    @abstractmethod
    async def subscribe(self, new_items: set[tuple[str, str]]) -> None:
        """动态新增订阅。"""

    @abstractmethod
    async def unsubscribe(self, removed_items: set[tuple[str, str]]) -> None:
        """动态移除订阅。"""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """是否正在运行（连接活跃）。"""

    def _notify(self, symbol: str, timeframe: str, klines: list[dict], event: str) -> None:
        """触发 K 线更新回调。"""
        if self._on_kline is not None:
            self._on_kline(symbol, timeframe, klines, event)


_MAX_KLINE_CACHE = 200


def merge_kline_update(cached: list[dict], new_kline: dict) -> tuple[list[dict], str]:
    """把单根新 K 线合并到缓存。

    返回 (merged_list, event):
        event="update": 新 K 线与缓存首根是同一根（时间戳相同），仅更新
        event="closed": 新 K 线时间戳更大，意味着上一根已收盘，新根开始

    缓存按时间戳降序排列（最新在前）。最大长度 _MAX_KLINE_CACHE，超出则截断尾部。
    """
    if not cached:
        return [new_kline], "update"

    new_ts = new_kline["timestamp"]
    head_ts = cached[0]["timestamp"]

    if new_ts == head_ts:
        merged = [new_kline] + cached[1:]
        return merged, "update"

    if new_ts > head_ts:
        merged = [new_kline] + cached
        if len(merged) > _MAX_KLINE_CACHE:
            merged = merged[:_MAX_KLINE_CACHE]
        return merged, "closed"

    # new_ts < head_ts: 历史/乱序数据，忽略
    return cached, "update"


class RESTKlineFetcher(BaseKlineFetcher):
    """REST 轮询式 K 线获取器。

    从现有 KlineDataManager._fetch_loop 迁移而来。
    每次 fetch 后通过 on_kline 回调通知，event 固定为 "closed"。
    """

    def __init__(
        self,
        exchange,
        on_kline: Callable[[str, str, list[dict], str], None],
        timeframes_seconds: dict[str, int],
        buffer_seconds: int = 1,
    ):
        super().__init__(on_kline)
        self.exchange = exchange
        self.timeframes_seconds = timeframes_seconds
        self.buffer_seconds = buffer_seconds
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._running = False

    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动所有 (symbol, tf) 的轮询循环。"""
        self._running = True
        self._symbol_timeframes = set(symbol_timeframes)
        for symbol, timeframe in symbol_timeframes:
            self._start_one(symbol, timeframe)
        logger.info("RESTKlineFetcher 已启动: %d 个订阅", len(self._tasks))

    def _start_one(self, symbol: str, timeframe: str) -> None:
        """启动单个 (symbol, tf) 的循环。"""
        key = (symbol, timeframe)
        if key in self._tasks and not self._tasks[key].done():
            return  # 已在运行
        task = asyncio.create_task(self._fetch_loop(symbol, timeframe))
        self._tasks[key] = task

    async def _fetch_loop(self, symbol: str, timeframe: str) -> None:
        """单个 (symbol, tf) 的轮询循环。对齐到 K 线收盘时刻。"""
        interval = self.timeframes_seconds.get(timeframe, 300)
        logger.info("REST 拉取已启动: %s %s (间隔=%d秒)", symbol, timeframe, interval)

        while self._running:
            try:
                klines = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=151)
                if klines:
                    # ccxt 返回最旧在前，策略需要最新在前
                    klines.reverse()
                    self._notify(symbol, timeframe, klines, "closed")
            except Exception:
                logger.exception("REST K 线拉取异常: %s %s", symbol, timeframe)

            # 对齐到下一根 K 线收盘后 buffer_seconds 秒
            now = _time.time()
            next_execution = ((int(now) // interval) + 1) * interval + self.buffer_seconds
            sleep_time = max(1, next_execution - now)
            await asyncio.sleep(sleep_time)

    async def stop(self) -> None:
        """停止所有轮询循环。"""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._symbol_timeframes.clear()
        logger.info("RESTKlineFetcher 已停止")

    async def subscribe(self, new_items: set[tuple[str, str]]) -> None:
        """动态新增订阅。"""
        for symbol, timeframe in new_items:
            self._symbol_timeframes.add((symbol, timeframe))
            self._start_one(symbol, timeframe)
        logger.info("RESTKlineFetcher 新增订阅: %d", len(new_items))

    async def unsubscribe(self, removed_items: set[tuple[str, str]]) -> None:
        """动态移除订阅。"""
        for key in removed_items:
            task = self._tasks.pop(key, None)
            if task and not task.done():
                task.cancel()
            self._symbol_timeframes.discard(key)
        logger.info("RESTKlineFetcher 移除订阅: %d", len(removed_items))

    @property
    def is_running(self) -> bool:
        return self._running
