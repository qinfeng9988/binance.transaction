"""KlineDataManager 集成测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from quant_trader.core.kline_manager import KlineDataManager


def _make_manager():
    """构造一个 KlineDataManager，注入模拟 exchange 和 data_store。"""
    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[])
    data_store = MagicMock()
    manager = KlineDataManager(
        exchange=exchange,
        data_store=data_store,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
        proxy=None,
    )
    return manager


def test_subscribe_returns_queue():
    """subscribe 返回 asyncio.Queue。"""
    manager = _make_manager()
    queue = manager.subscribe("BTC/USDT", "5m")
    assert hasattr(queue, "get_nowait")


def test_get_realtime_price_returns_none_if_absent():
    """没有数据时 get_realtime_price 返回 None。"""
    manager = _make_manager()
    assert manager.get_realtime_price("BTC/USDT") is None


def test_on_kline_update_updates_realtime_price():
    """update 事件更新实时价格缓存。"""
    manager = _make_manager()
    klines = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0},
    ]
    manager._on_kline_update("BTC/USDT", "5m", klines, "update", "ws")
    assert manager.get_realtime_price("BTC/USDT") == 100.5


def test_on_kline_closed_stores_to_db():
    """closed 事件触发数据库写入。"""
    manager = _make_manager()
    klines = [
        {"timestamp": 2000, "open": 100, "high": 101, "low": 99, "close": 101.0, "volume": 1.0},
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0},
    ]
    manager._on_kline_update("BTC/USDT", "5m", klines, "closed", "ws")
    assert manager.data_store.insert_klines.called
    assert manager.get_realtime_price("BTC/USDT") == 101.0


def test_on_kline_ws_recovers_stops_rest():
    """WebSocket 恢复推送时，KlineDataManager 切回 ws 并停止 rest_fetcher。"""
    manager = _make_manager()
    manager._active_source = "rest"

    # 模拟 rest_fetcher 已启动
    manager.rest_fetcher = MagicMock()
    manager.rest_fetcher.stop = AsyncMock()

    klines = [{"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0}]

    # _on_kline_update 内部会 asyncio.create_task(stop)，需要事件循环
    async def _run():
        manager._on_kline_update("BTC/USDT", "5m", klines, "update", "ws")
        # create_task 需要 yield 让任务调度执行
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert manager._active_source == "ws"


def test_update_subscriptions_diffs_correctly():
    """update_subscriptions 正确计算新增和移除。"""
    manager = _make_manager()
    manager._symbol_timeframes = {("BTC/USDT", "5m"), ("ETH/USDT", "5m")}

    # 模拟 fetcher
    manager.ws_fetcher = MagicMock()
    manager.ws_fetcher.is_running = False  # WebSocket 未连接
    manager.ws_fetcher.subscribe = AsyncMock()
    manager.ws_fetcher.unsubscribe = AsyncMock()
    manager.rest_fetcher = MagicMock()
    manager.rest_fetcher.is_running = False
    manager.rest_fetcher.subscribe = AsyncMock()
    manager.rest_fetcher.unsubscribe = AsyncMock()

    new_set = {("BTC/USDT", "5m"), ("SOL/USDT", "5m")}  # 移除 ETH，新增 SOL
    asyncio.run(manager.update_subscriptions(new_set))

    assert manager._symbol_timeframes == new_set
