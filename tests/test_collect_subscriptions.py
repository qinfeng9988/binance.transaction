"""collect_subscriptions 单元测试（parent 订阅缺口回归测试）。

回归背景：修复前 Engine 只订阅策略声明的 (symbol, timeframe)，parent 周期
（如 5m→1h、1h→4h）若未被任何策略声明，实盘/paper 下 parent_klines 恒为
None，require_parent_ema_aligned / strict_bias_mode / use_parent_trend_override
等过滤静默失效——而回测驱动显式拉取 parent 数据，导致回测与实盘行为不一致。
"""

from quant_trader.core.config_loader import StrategyConfig
from quant_trader.main import collect_subscriptions


def make_strategy(name="s", enabled=True, symbols=None, timeframes=None):
    return StrategyConfig(
        name=name, enabled=enabled,
        symbols=symbols or ["BTC/USDT"],
        timeframes=timeframes or ["5m"],
        params={},
    )


def test_includes_declared_timeframe_and_its_parent():
    """5m 策略 → 订阅 5m + parent 1h。"""
    subs = collect_subscriptions([make_strategy(timeframes=["5m"])])

    assert ("BTC/USDT", "5m") in subs
    assert ("BTC/USDT", "1h") in subs


def test_one_level_parent_only():
    """1h 的 parent 是 4h，但不向上追溯 4h 的 parent（1d）。"""
    subs = collect_subscriptions([make_strategy(timeframes=["1h"])])

    assert ("BTC/USDT", "1h") in subs
    assert ("BTC/USDT", "4h") in subs
    assert ("BTC/USDT", "1d") not in subs


def test_disabled_strategy_excluded():
    """disabled 策略不产生任何订阅。"""
    subs = collect_subscriptions([make_strategy(enabled=False, timeframes=["5m", "1h"])])

    assert subs == set()


def test_1d_parent_1w_subscribed():
    """1d 的 parent 1w 现已进入秒数表，应被订阅（修复此前静默丢弃回归）。"""
    subs = collect_subscriptions([make_strategy(symbols=["ETH/USDT"], timeframes=["1d"])])

    assert ("ETH/USDT", "1d") in subs
    assert ("ETH/USDT", "1w") in subs


def test_multiple_strategies_dedup():
    """多策略订阅同一 (symbol, tf) 时去重。"""
    subs = collect_subscriptions([
        make_strategy(name="a", timeframes=["5m"]),
        make_strategy(name="b", timeframes=["5m"]),
    ])

    assert len(subs) == 2  # (BTC,5m) + (BTC,1h)
