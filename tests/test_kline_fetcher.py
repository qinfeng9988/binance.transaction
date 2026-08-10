"""kline_fetcher 模块单元测试。"""

import pytest

from quant_trader.core.kline_fetcher import BaseKlineFetcher


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
