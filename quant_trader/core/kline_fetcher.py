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

import ccxt.pro

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


class WebSocketKlineFetcher(BaseKlineFetcher):
    """WebSocket 推送式 K 线获取器。

    使用 ccxt.pro.binance 的 watch_ohlcv_for_symbols 订阅 K 线推送。
    断连时前 3 次快速重连（指数退避），超过 3 次触发降级回调，
    之后在后台以 10min/30min 间隔持续重连，重连成功后自动恢复。
    """

    def __init__(
        self,
        rest_exchange,
        on_kline: Callable[[str, str, list[dict], str], None],
        on_fallback_to_rest: Callable[[], None],
        timeframes_seconds: dict[str, int],
        proxy: str | None = None,
        max_fast_reconnect: int = 3,
        bg_retry_short_delay: int = 600,
        bg_retry_long_delay: int = 1800,
        bg_retry_short_count: int = 2,
    ):
        super().__init__(on_kline)
        self.rest_exchange = rest_exchange
        self.on_fallback_to_rest = on_fallback_to_rest
        self.timeframes_seconds = timeframes_seconds
        self.proxy = proxy
        self.max_fast_reconnect = max_fast_reconnect
        self.bg_retry_short_delay = bg_retry_short_delay
        self.bg_retry_long_delay = bg_retry_long_delay
        self.bg_retry_short_count = bg_retry_short_count

        self._ws: ccxt.pro.binance | None = None
        self._ws_task: asyncio.Task | None = None
        self._running = False
        self._connected = False  # WebSocket 连接是否活跃
        self._reconnect_attempt = 0
        self._fallback_triggered = False  # 是否已触发降级
        # (symbol, tf) → list[dict] 本地缓存（WebSocket 推送合并维护）
        self._kline_caches: dict[tuple[str, str], list[dict]] = {}

    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动 WebSocket 获取：先 REST bootstrap，再启动 ws 循环。"""
        self._running = True
        self._symbol_timeframes = set(symbol_timeframes)
        await self._bootstrap(symbol_timeframes)
        self._ws_task = asyncio.create_task(self._ws_loop())

    async def _bootstrap(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """通过 REST 拉取每个 (symbol, tf) 的初始 151 根 K 线。"""
        for symbol, timeframe in symbol_timeframes:
            try:
                klines = await self.rest_exchange.fetch_ohlcv(symbol, timeframe, limit=151)
                if klines:
                    klines.reverse()  # 最新在前
                    self._kline_caches[(symbol, timeframe)] = klines
                    self._notify(symbol, timeframe, klines, "closed")
                    logger.info("WebSocket bootstrap 完成: %s %s (%d 根)", symbol, timeframe, len(klines))
            except Exception:
                logger.exception("WebSocket bootstrap 失败: %s %s", symbol, timeframe)

    async def _ws_loop(self) -> None:
        """主循环：连接 → 订阅 → watch，异常时重连。"""
        while self._running:
            try:
                await self._connect()
                await self._watch()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("WebSocket 异常: %s", e)
                await self._handle_disconnect()

    async def _connect(self) -> None:
        """创建 ccxt.pro.binance 实例并配置代理。"""
        # 每次重连创建新实例，避免状态污染
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass

        config: dict = {
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        }
        if self.proxy:
            # ccxt.pro 使用 aiohttp，通过 wsProxy/wssProxy 配置代理
            config["options"]["wsProxy"] = self.proxy
            config["options"]["wssProxy"] = self.proxy

        self._ws = ccxt.pro.binance(config)
        await self._ws.load_markets()

    async def _watch(self) -> None:
        """订阅并监听 K 线推送。"""
        subscriptions = [list(st) for st in self._symbol_timeframes]
        if not subscriptions:
            logger.warning("WebSocket 无订阅，等待 5 秒...")
            await asyncio.sleep(5)
            return

        logger.info("WebSocket 订阅: %s", subscriptions)
        current_subscriptions = set(self._symbol_timeframes)
        while self._running and self._symbol_timeframes == current_subscriptions:
            result = await self._ws.watch_ohlcv_for_symbols(subscriptions)
            # result 格式: {symbol: {timeframe: [[ts, o, h, l, c, v], ...]}}
            for symbol, tf_dict in result.items():
                for timeframe, candles in tf_dict.items():
                    self._handle_ws_message(symbol, timeframe, candles)

        # 订阅集合发生变化 → 退出内层循环，重新订阅
        if self._running:
            logger.info("订阅集合变化，重新订阅 WebSocket")

    def _handle_ws_message(self, symbol: str, timeframe: str, candles: list[list]) -> None:
        """处理 WebSocket 推送：把单根 K 线合并到缓存。"""
        if not candles:
            return

        # 取最新一根（candles[-1] 是最新的）
        raw = candles[-1]
        new_kline = {
            "timestamp": raw[0],
            "open": raw[1],
            "high": raw[2],
            "low": raw[3],
            "close": raw[4],
            "volume": raw[5],
        }

        key = (symbol, timeframe)
        cached = self._kline_caches.get(key, [])
        merged, event = merge_kline_update(cached, new_kline)
        self._kline_caches[key] = merged

        # 连接活跃标记
        if not self._connected:
            self._connected = True
            self._reconnect_attempt = 0
            self._fallback_triggered = False
            logger.info("WebSocket 连接已恢复: %s %s", symbol, timeframe)

        self._notify(symbol, timeframe, merged, event)

    async def _handle_disconnect(self) -> None:
        """处理断连：前3次快速重连，之后触发降级并后台慢速重连。"""
        self._connected = False
        self._reconnect_attempt += 1

        if self._reconnect_attempt <= self.max_fast_reconnect:
            delay = 2 ** self._reconnect_attempt  # 2s, 4s, 8s
            logger.warning(
                "WebSocket 断线，快速重连 (第%d次, 延迟%d秒)",
                self._reconnect_attempt, delay,
            )
            await asyncio.sleep(delay)
            return

        # 超过 3 次 → 触发降级（只触发一次）
        if not self._fallback_triggered:
            self._fallback_triggered = True
            logger.error("WebSocket 3次重连失败，降级到 REST")
            try:
                self.on_fallback_to_rest()
            except Exception:
                logger.exception("降级回调异常")

        # 后台慢速重连
        if self._reconnect_attempt <= self.max_fast_reconnect + self.bg_retry_short_count:
            delay = self.bg_retry_short_delay  # 10 分钟
        else:
            delay = self.bg_retry_long_delay  # 30 分钟
        logger.warning("WebSocket 后台重连 (第%d次, 延迟%d秒)", self._reconnect_attempt, delay)
        await asyncio.sleep(delay)

    async def stop(self) -> None:
        """停止 WebSocket 获取。"""
        self._running = False
        self._connected = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("WebSocketKlineFetcher 已停止")

    async def subscribe(self, new_items: set[tuple[str, str]]) -> None:
        """动态新增订阅。下一轮 watch 会自动包含新订阅。"""
        # 先 bootstrap 新订阅的历史数据
        await self._bootstrap(new_items)
        for item in new_items:
            self._symbol_timeframes.add(item)
        logger.info("WebSocketKlineFetcher 新增订阅: %d", len(new_items))

    async def unsubscribe(self, removed_items: set[tuple[str, str]]) -> None:
        """动态移除订阅。"""
        for item in removed_items:
            self._symbol_timeframes.discard(item)
            self._kline_caches.pop(item, None)
        logger.info("WebSocketKlineFetcher 移除订阅: %d", len(removed_items))

    @property
    def is_running(self) -> bool:
        """WebSocket 是否正在推送数据（连接活跃）。"""
        return self._connected
