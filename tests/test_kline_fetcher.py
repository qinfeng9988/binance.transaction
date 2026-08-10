"""kline_fetcher 模块单元测试。"""

import pytest

from quant_trader.core.kline_fetcher import BaseKlineFetcher, merge_kline_update


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

