"""热重载回调线程安全测试（P2 bug 回归测试）。

回归背景：_on_config_change 由 watchdog 的观察者线程调用，
asyncio.create_task 在非事件循环线程调用必然抛 RuntimeError
（被 ConfigLoader 吞掉），导致热重载后 K 线订阅永不更新。
修复后使用 asyncio.run_coroutine_threadsafe 调度回事件循环。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from quant_trader.core.config_loader import StrategyConfig, StrategyGlobalConfig
from quant_trader.main import Engine


def make_engine():
    """构造轻量 Engine 并注入 mock 依赖与策略配置。"""
    engine = Engine()
    engine.kline_manager = MagicMock()
    engine.kline_manager.update_subscriptions = AsyncMock()
    engine.config._strategy_cfg = StrategyGlobalConfig(
        mode="paper",
        strategies=[StrategyConfig(
            name="s", enabled=True, symbols=["BTC/USDT"],
            timeframes=["5m"], params={},
        )],
    )
    return engine


def test_on_config_change_updates_subscriptions_from_worker_thread():
    """watchdog 线程触发回调 → 订阅更新被线程安全地调度到事件循环执行。"""
    engine = make_engine()

    async def scenario():
        engine._loop = asyncio.get_running_loop()
        # 模拟 watchdog 观察者线程调用回调
        await asyncio.to_thread(engine._on_config_change, "strategies.yaml")
        # 留给 run_coroutine_threadsafe 调度执行的时间
        await asyncio.sleep(0.05)

        engine.kline_manager.update_subscriptions.assert_awaited_once()
        new_set = engine.kline_manager.update_subscriptions.await_args.args[0]
        assert ("BTC/USDT", "5m") in new_set
        assert ("BTC/USDT", "1h") in new_set  # parent 周期也包含

    asyncio.run(scenario())


def test_on_config_change_skips_gracefully_without_loop():
    """事件循环不可用时不抛异常（引擎未启动场景）。"""
    engine = make_engine()
    engine._loop = None

    engine._on_config_change("strategies.yaml")  # 不应抛出

    engine.kline_manager.update_subscriptions.assert_not_awaited()
