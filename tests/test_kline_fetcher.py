"""kline_fetcher 模块单元测试。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from quant_trader.core.kline_fetcher import (
    BaseKlineFetcher,
    RESTKlineFetcher,
    WebSocketKlineFetcher,
    merge_kline_update,
)


def _kline(ts, close):
    """构造测试用 K 线 dict。"""
    return {"timestamp": ts, "open": 100, "high": 101, "low": 99, "close": close, "volume": 1.0}


def test_base_fetcher_is_abstract():
    """BaseKlineFetcher 是抽象基类，不能直接实例化。"""
    with pytest.raises(TypeError):
        BaseKlineFetcher(on_kline=lambda *_: None)


def test_base_fetcher_subclass_must_implement_abstract_methods():
    """子类必须实现所有抽象方法才能实例化。"""

    class IncompleteFetcher(BaseKlineFetcher):
        pass

    with pytest.raises(TypeError):
        IncompleteFetcher(on_kline=lambda *_: None)


def test_base_fetcher_concrete_subclass_can_instantiate():
    """完整实现的子类可以实例化。"""

    class StubFetcher(BaseKlineFetcher):
        async def start(self, symbol_timeframes):
            pass

        async def stop(self):
            pass

        async def subscribe(self, new_items):
            pass

        async def unsubscribe(self, removed_items):
            pass

        @property
        def is_running(self):
            return False

    fetcher = StubFetcher(on_kline=lambda *_: None)
    assert fetcher.is_running is False


# ---------- merge_kline_update ----------

def test_merge_kline_update_empty_cache():
    """缓存为空时，新 K 线直接作为初始数据，事件为 update。"""
    result, event = merge_kline_update([], _kline(1000, 50.0))
    assert result == [_kline(1000, 50.0)]
    assert event == "update"


def test_merge_kline_update_same_timestamp():
    """新 K 线时间戳与缓存首根相同 → 替换，事件为 update。"""
    cached = [_kline(1000, 50.0), _kline(900, 49.0)]
    result, event = merge_kline_update(cached, _kline(1000, 51.0))
    assert result[0]["close"] == 51.0
    assert result[1] == _kline(900, 49.0)
    assert len(result) == 2
    assert event == "update"


def test_merge_kline_update_new_timestamp_means_close():
    """新 K 线时间戳大于缓存首根 → 插入到最前，事件为 closed。"""
    cached = [_kline(1000, 50.0), _kline(900, 49.0)]
    result, event = merge_kline_update(cached, _kline(2000, 51.0))
    assert result[0]["timestamp"] == 2000
    assert result[1]["timestamp"] == 1000
    assert result[2]["timestamp"] == 900
    assert len(result) == 3
    assert event == "closed"


def test_merge_kline_update_truncates_to_max_length():
    """缓存超过 200 根时自动截断尾部。"""
    cached = [_kline(ts, float(ts)) for ts in range(200, 0, -1)]  # 200 根
    new_kline = _kline(1000, 999.0)
    result, event = merge_kline_update(cached, new_kline)
    assert len(result) == 200
    assert result[0]["timestamp"] == 1000
    assert event == "closed"


def test_merge_kline_update_outdated_timestamp_ignored():
    """新 K 线时间戳小于缓存首根 → 忽略（防止历史数据污染）。"""
    cached = [_kline(1000, 50.0), _kline(900, 49.0)]
    result, event = merge_kline_update(cached, _kline(800, 48.0))
    assert result == cached  # 缓存不变
    assert event == "update"


# ---------- RESTKlineFetcher ----------

def _make_mock_exchange():
    """构造一个模拟 BinanceAdapter。"""
    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0},
    ])
    return exchange


def test_rest_fetcher_initial_state():
    """RESTKlineFetcher 在 start 前 is_running 为 False。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300},
    )
    assert fetcher.is_running is False


def test_rest_fetcher_starts_and_stops():
    """start 后 is_running 为 True，stop 后为 False。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
    )

    asyncio.run(fetcher.start({("BTC/USDT", "5m")}))
    assert fetcher.is_running is True
    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes

    asyncio.run(fetcher.stop())
    assert fetcher.is_running is False


def test_rest_fetcher_subscribe_adds_new_subscription():
    """subscribe 启动新增 (symbol, tf) 的循环。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300, "1h": 3600},
        buffer_seconds=0,
    )

    asyncio.run(fetcher.start({("BTC/USDT", "5m")}))
    asyncio.run(fetcher.subscribe({("ETH/USDT", "5m")}))

    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes
    assert ("ETH/USDT", "5m") in fetcher._symbol_timeframes

    asyncio.run(fetcher.stop())


def test_rest_fetcher_unsubscribe_removes_subscription():
    """unsubscribe 取消对应循环。"""
    exchange = _make_mock_exchange()
    fetcher = RESTKlineFetcher(
        exchange=exchange,
        on_kline=lambda *_: None,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
    )

    asyncio.run(fetcher.start({("BTC/USDT", "5m"), ("ETH/USDT", "5m")}))
    asyncio.run(fetcher.unsubscribe({("ETH/USDT", "5m")}))

    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes
    assert ("ETH/USDT", "5m") not in fetcher._symbol_timeframes

    asyncio.run(fetcher.stop())


# ---------- WebSocketKlineFetcher ----------

def test_ws_fetcher_initial_state():
    """WebSocketKlineFetcher 在 start 前 is_running 为 False。"""
    exchange = _make_mock_exchange()
    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda *_: None,
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )
    assert fetcher.is_running is False


def test_ws_fetcher_bootstrap_calls_rest_and_notifies():
    """start 时先通过 REST 拉取历史 K 线填充缓存。"""
    exchange = _make_mock_exchange()
    received = []

    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda sym, tf, klines, event: received.append((sym, tf, event)),
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )

    # 直接测试 bootstrap 方法，不启动 ws_loop
    asyncio.run(fetcher._bootstrap({("BTC/USDT", "5m")}))

    exchange.fetch_ohlcv.assert_called_once_with("BTC/USDT", "5m", limit=151)
    assert received == [("BTC/USDT", "5m", "closed")]


def test_ws_fetcher_subscribe_updates_symbol_set():
    """subscribe 添加新 (symbol, tf) 到订阅集合。"""
    exchange = _make_mock_exchange()
    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda *_: None,
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300, "1h": 3600},
        proxy=None,
    )

    asyncio.run(fetcher.subscribe({("ETH/USDT", "5m")}))
    assert ("ETH/USDT", "5m") in fetcher._symbol_timeframes


def test_ws_fetcher_unsubscribe_removes_from_symbol_set():
    """unsubscribe 从订阅集合移除。"""
    exchange = _make_mock_exchange()
    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda *_: None,
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )
    fetcher._symbol_timeframes = {("BTC/USDT", "5m"), ("ETH/USDT", "5m")}

    asyncio.run(fetcher.unsubscribe({("ETH/USDT", "5m")}))
    assert ("ETH/USDT", "5m") not in fetcher._symbol_timeframes
    assert ("BTC/USDT", "5m") in fetcher._symbol_timeframes


def test_ws_fetcher_merge_incoming_message():
    """收到 WebSocket 推送后，正确合并到缓存并触发回调。"""
    exchange = _make_mock_exchange()
    received_events = []

    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda sym, tf, klines, event: received_events.append((event, klines[0]["close"])),
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )

    # 模拟已有缓存
    fetcher._kline_caches[("BTC/USDT", "5m")] = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0},
    ]

    # 同时间戳更新
    fetcher._handle_ws_message(
        "BTC/USDT", "5m",
        [[1000, 100, 101, 99, 100.5, 1.0]],
    )
    assert received_events[-1] == ("update", 100.5)

    # 新时间戳 → closed
    fetcher._handle_ws_message(
        "BTC/USDT", "5m",
        [[2000, 100, 101, 99, 101.0, 1.0]],
    )
    assert received_events[-1] == ("closed", 101.0)


def test_ws_fetcher_normalizes_unified_symbol():
    """watch_ohlcv_for_symbols 返回统一 symbol ('XAU/USDT:USDT')，
    需归一化为用户配置的原始格式 ('XAU/USDT')，与 REST/数据库保持一致。"""
    exchange = _make_mock_exchange()
    received_symbols = []

    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda sym, tf, klines, event: received_symbols.append(sym),
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )

    # 预注册映射（模拟 _bootstrap 注册的结果）
    fetcher._register_symbol_mapping("XAU/USDT:USDT", "XAU/USDT")
    fetcher._kline_caches[("XAU/USDT", "5m")] = [
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0},
    ]

    # 推送统一格式 symbol
    fetcher._handle_ws_message(
        "XAU/USDT:USDT", "5m",
        [[1000, 100, 101, 99, 100.5, 1.0]],
    )

    assert received_symbols == ["XAU/USDT"]  # 归一化后


def test_ws_fetcher_normalize_unknown_symbol_passthrough():
    """未注册映射的 symbol 直接原样返回（不丢失数据）。"""
    exchange = _make_mock_exchange()
    fetcher = WebSocketKlineFetcher(
        rest_exchange=exchange,
        on_kline=lambda *_: None,
        on_fallback_to_rest=lambda: None,
        timeframes_seconds={"5m": 300},
        proxy=None,
    )
    # 映射表为空，_ws 也为 None → 直接返回原 symbol
    assert fetcher._normalize_symbol("BTC/USDT") == "BTC/USDT"


# ---------- 端到端集成测试 ----------

def test_full_pipeline_rest_only():
    """端到端：REST 模式下，KlineDataManager 收到 closed 事件并更新价格。"""
    from quant_trader.core.kline_manager import KlineDataManager

    exchange = MagicMock()
    exchange.fetch_ohlcv = AsyncMock(return_value=[
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0},
    ])
    data_store = MagicMock()
    manager = KlineDataManager(
        exchange=exchange,
        data_store=data_store,
        timeframes_seconds={"5m": 300},
        buffer_seconds=0,
        proxy=None,
    )

    # 模拟 REST fetcher 直接回调
    klines = [
        {"timestamp": 2000, "open": 100, "high": 101, "low": 99, "close": 101.0, "volume": 1.0},
        {"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0},
    ]
    manager._on_kline_update("BTC/USDT", "5m", klines, "closed", "rest")

    # 实时价格已更新
    assert manager.get_realtime_price("BTC/USDT") == 101.0
    # K 线 cache 已更新
    assert manager.get_klines("BTC/USDT", "5m") == klines
    # 数据库已写入
    assert data_store.insert_klines.called


def test_full_pipeline_ws_then_rest_fallback():
    """端到端：WebSocket 降级到 REST，再恢复切回 ws。"""
    from quant_trader.core.kline_manager import KlineDataManager

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
    manager._symbol_timeframes = {("BTC/USDT", "5m")}

    async def _run():
        # 1. 触发降级（内部 asyncio.create_task 需要事件循环）
        manager._fallback_to_rest()
        await asyncio.sleep(0)
        assert manager._active_source == "rest"
        assert manager.rest_fetcher is not None

        # 2. 模拟 REST 数据回调
        klines_rest = [{"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1.0}]
        manager._on_kline_update("BTC/USDT", "5m", klines_rest, "closed", "rest")
        assert manager.get_realtime_price("BTC/USDT") == 100.0

        # 3. WebSocket 恢复，推送 update 事件 → 应切回 ws
        klines_ws = [{"timestamp": 1000, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1.0}]
        manager._on_kline_update("BTC/USDT", "5m", klines_ws, "update", "ws")
        await asyncio.sleep(0)  # 让 create_task(stop) 调度执行

    asyncio.run(_run())
    assert manager._active_source == "ws"
    assert manager.get_realtime_price("BTC/USDT") == 100.5  # ws 数据覆盖了

