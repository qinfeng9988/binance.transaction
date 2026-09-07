"""backtest_runner 纯函数单元测试。

不依赖真实 DB 的部分（run_id 生成）+ run_backtest_loop 的 funding 结算接线。
端到端集成测试见 test_backtest_runner_integration.py。
"""

import sys
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from backtest_runner import generate_run_id, run_backtest_loop

from quant_trader.core.backtest_kline_driver import KlineEvent
from quant_trader.exchange.paper_trading import PaperTradingEngine
from quant_trader.models import Signal


def test_run_id_format_basic():
    """run_id 格式: bt_{YYYYMMDD_HHMMSS}_{symbol}_{tf}_{hash8}。"""
    run_id = generate_run_id("BTC/USDT", "5m", {"lookback": 100})

    assert run_id.startswith("bt_")
    assert "BTC-USDT" in run_id
    assert "_5m_" in run_id


def test_run_id_hash_is_8_hex_chars():
    """hash 部分是 8 位十六进制。"""
    run_id = generate_run_id("BTC/USDT", "5m", {"lookback": 100})
    hash_part = run_id.split("_")[-1]
    assert len(hash_part) == 8
    int(hash_part, 16)  # 验证是合法十六进制


def test_run_id_deterministic_for_same_params():
    """相同参数生成的 hash 部分一致（不含时间戳）。"""
    params = {"lookback": 100, "min_touches": 5.3}
    run1 = generate_run_id("BTC/USDT", "5m", params)
    run2 = generate_run_id("BTC/USDT", "5m", params)

    assert run1.split("_")[-1] == run2.split("_")[-1]


def test_run_id_differs_for_different_params():
    """不同参数生成不同 hash。"""
    run1 = generate_run_id("BTC/USDT", "5m", {"lookback": 100})
    run2 = generate_run_id("BTC/USDT", "5m", {"lookback": 200})

    assert run1.split("_")[-1] != run2.split("_")[-1]


def test_run_id_handles_colon_symbols():
    """symbol 含冒号（如 BTC/USDT:USDT）时正常生成。"""
    run_id = generate_run_id("BTC/USDT:USDT", "1h", {"x": 1})

    assert "BTC-USDT-USDT" in run_id


# ============================================================
# run_backtest_loop: funding 结算接线（bug #8 回归测试）
# ============================================================

def _make_kline(ts_epoch: float, close: float = 50000.0) -> dict:
    return {
        "timestamp": datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
        "open": close, "high": close, "low": close,
        "close": close, "volume": 1.0,
    }


class _FakeDriver:
    """产出预构造事件的假驱动器。"""

    def __init__(self, events):
        self._events = events

    def iter_klines(self, **kwargs):
        return iter(self._events)


class _StubStrategy:
    """不产生任何信号的假策略。"""

    def on_bar(self, *args, **kwargs):
        return []


def _run_loop(events, eng):
    """用给定事件跑一遍回测主循环。"""
    run_backtest_loop(
        symbol="BTC/USDT", timeframe="5m",
        strategy=_StubStrategy(), paper_engine=eng,
        risk_manager=MagicMock(), backtest_ds=MagicMock(),
        kline_driver=_FakeDriver(events),
        start=datetime.fromtimestamp(100, tz=timezone.utc),
        end=datetime.fromtimestamp(300, tz=timezone.utc),
    )


def _make_engine() -> PaperTradingEngine:
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    return eng


# 无 funding 事件的基线事件流（期末强平等其余路径完全一致）
_BASELINE_EVENTS = [
    KlineEvent(kline=_make_kline(100), window=[], parent_window=None,
               funding_rate=None, funding_ts=None),
    KlineEvent(kline=_make_kline(200), window=[], parent_window=None,
               funding_rate=None, funding_ts=None),
    KlineEvent(kline=_make_kline(300), window=[], parent_window=None,
               funding_rate=None, funding_ts=None),
]


def test_run_backtest_loop_applies_funding_on_crossing():
    """持仓期间跨越 funding 事件 → 按 notional × rate 结算进余额。

    差分断言：与无 funding 的基线跑相比，余额差 = 两次结算 0.5 + 1.0。
    （期末强平的保证金返还/手续费两边一致，自然抵消。）
    """
    events = [
        KlineEvent(kline=_make_kline(100), window=[], parent_window=None,
                   funding_rate=None, funding_ts=None),
        KlineEvent(kline=_make_kline(200), window=[], parent_window=None,
                   funding_rate=0.01, funding_ts=150_000),
        KlineEvent(kline=_make_kline(300), window=[], parent_window=None,
                   funding_rate=0.02, funding_ts=250_000),
    ]
    baseline = _make_engine()
    funded = _make_engine()

    _run_loop(_BASELINE_EVENTS, baseline)
    _run_loop(events, funded)

    assert baseline.balance == pytest.approx(funded.balance + 1.5)
    assert funded.get_summary()["total_funding_paid"] == pytest.approx(1.5)


def test_run_backtest_loop_no_double_charge_for_same_funding_ts():
    """同一 funding 事件未被新事件跨越 → 不重复结算（差 = 一次 0.5）。"""
    events = [
        KlineEvent(kline=_make_kline(100), window=[], parent_window=None,
                   funding_rate=None, funding_ts=None),
        KlineEvent(kline=_make_kline(200), window=[], parent_window=None,
                   funding_rate=0.01, funding_ts=150_000),
        KlineEvent(kline=_make_kline(300), window=[], parent_window=None,
                   funding_rate=0.01, funding_ts=150_000),  # 仍是同一事件
    ]
    baseline = _make_engine()
    funded = _make_engine()

    _run_loop(_BASELINE_EVENTS, baseline)
    _run_loop(events, funded)

    assert baseline.balance == pytest.approx(funded.balance + 0.5)


# ============================================================
# run_backtest_loop: position_value_ratio 仓位比例接线
# ============================================================

class _SignalingStrategy:
    """第一根 bar 发出一个开多信号，其后不再发信号。"""

    def __init__(self):
        self._fired = False

    def on_bar(self, *args, **kwargs):
        if self._fired:
            return []
        self._fired = True
        return [Signal(
            strategy_name="test_strategy", symbol="BTC/USDT", timeframe="5m",
            signal_type="buy", price=50000.0, stop_loss=49000.0, leverage=5,
        )]


def _run_loop_with_open_signal(eng, ratio):
    """事件流中产生一次开仓，返回下单数量。"""
    from unittest.mock import patch
    # 时间戳须 > signal_cooldown_seconds(220)，否则首根 bar 被冷却期跳过
    events = [
        KlineEvent(kline=_make_kline(1000), window=[1], parent_window=None,
                   funding_rate=None, funding_ts=None),
        KlineEvent(kline=_make_kline(1300), window=[1], parent_window=None,
                   funding_rate=None, funding_ts=None),
    ]
    rm = MagicMock()
    rm.check_and_adjust.return_value = {"approved": True, "leverage": 5}
    amounts = []
    orig_place = eng.place_order

    def spy_place(**kwargs):
        amounts.append(kwargs["amount"])
        return orig_place(**kwargs)

    with patch.object(eng, "place_order", side_effect=spy_place):
        kwargs = dict(
            symbol="BTC/USDT", timeframe="5m",
            strategy=_SignalingStrategy(), paper_engine=eng,
            risk_manager=rm, backtest_ds=MagicMock(),
            kline_driver=_FakeDriver(events),
            start=datetime.fromtimestamp(1000, tz=timezone.utc),
            end=datetime.fromtimestamp(1300, tz=timezone.utc),
        )
        if ratio is not None:
            kwargs["position_value_ratio"] = ratio
        run_backtest_loop(**kwargs)
    return amounts


def test_run_backtest_loop_default_ratio_0_1():
    """不传 position_value_ratio 时沿用默认 0.1：数量 = 余额×0.1/价格。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    amounts = _run_loop_with_open_signal(eng, ratio=None)

    assert len(amounts) == 1
    assert amounts[0] == pytest.approx(100000.0 * 0.1 / 50000.0)


def test_run_backtest_loop_custom_ratio_scales_quantity():
    """position_value_ratio=0.2 → 下单数量翻倍。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    amounts = _run_loop_with_open_signal(eng, ratio=0.2)

    assert len(amounts) == 1
    assert amounts[0] == pytest.approx(100000.0 * 0.2 / 50000.0)
