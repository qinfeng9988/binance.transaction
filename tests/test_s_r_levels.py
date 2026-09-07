"""SupportResistanceStrategy 单元测试。

聚焦 Funding Rate 降杠杆逻辑（P2 bug 回归测试）：
- 降杠杆必须在候选位循环外一次性计算（修复前循环内反复相乘，5x→3x→1x 复合衰减）
- 无惩罚时杠杆不受影响

测试通过 monkeypatch identify_support_resistance 控制 S/R 候选位，
并用宽松的过滤参数让候选位只能被指定条件拦截，从而精确复现衰减路径。
"""

import pytest
from datetime import datetime, timedelta, timezone

from quant_trader.strategies.s_r_levels import SupportResistanceStrategy

STRATEGY_NAME = "support_resistance_test"


def make_klines(n: int = 60, base: float = 100.0, rising: bool = True) -> list[dict]:
    """构造 n 根 K 线（新→旧）。rising=True 收盘递增（当前周期 uptrend）。"""
    chrono = []
    for i in range(n):
        close = base + i * (0.1 if rising else -0.1)
        chrono.append({
            "timestamp": datetime(2026, 8, 1, tzinfo=timezone.utc) + timedelta(minutes=5 * i),
            "open": close - 0.02, "high": close + 0.05,
            "low": close - 0.05, "close": close,
            "volume": 1.0,
        })
    return list(reversed(chrono))


def make_level(price: float, quality: float) -> dict:
    return {
        "price": price, "strength": 5, "quality_score": quality,
        "base_score": 5.0, "trend_bonus": 0.0, "recency_bonus": 0.0,
        "has_rejection": False, "has_volume_surge": False, "surge_ratio": 1.0,
    }


LENIENT_PARAMS = {
    # 趋势：gap 阈值 0 → 单调行情必出趋势；parent 传入下跌序列 → bias=short_primary（buy 为副方向）
    "ema_fast_period": 3, "ema_slow_period": 5, "ema_gap_threshold_pct": 0,
    # ADX 全局门槛放最松
    "adx_period": 14, "adx_min": 0.0, "adx_min_high_quality": 0.0, "di_tolerance": 2.0,
    "quality_threshold_for_lower_adx": 5.5,
    "price_vs_ema_filter": False,
    # RSI 全放行（副方向做多走 counter_long_max）
    "rsi_period": 14,
    "rsi_long_min": 0.0, "rsi_long_max": 100.0,
    "rsi_short_min": 0.0, "rsi_short_max": 100.0,
    "rsi_countertrend_long_max": 100.0, "rsi_countertrend_short_min": 0.0,
    # 成交量全放行
    "volume_burst_ratio": 0.0, "volume_burst_lookback": 20,
    # S/R 候选位距离/质量门槛
    "lookback": 100, "min_touches": 1, "entry_threshold_pct": 1.0,
    "secondary_min_touches": 1, "secondary_entry_threshold_pct": 1.0,
    "secondary_min_quality": 6.5, "secondary_volume_ratio": 0.0,
    # 其余开关全关
    "require_rejection_candle": False, "require_pullback_retest": False,
    "adaptive_swing_window": False, "use_order_blocks": False,
    "use_fvg": False, "use_vah_val": False,
    "use_atr_stop": False, "stop_loss_pct": 2.0, "atr_period": 14,
    "strict_bias_mode": False, "use_parent_trend_override": False,
    "leverage": 5,
    # Funding Rate 过滤：0.01% > 0.008% 惩罚阈值，降杠杆 ×0.6
    "funding_filter_enabled": True,
    "funding_long_penalty_threshold": 0.008,
    "funding_short_block_threshold": -0.006,
    "funding_long_quality_penalty": -1.0,
    "funding_long_leverage_reduction_enabled": True,
    "funding_long_leverage_reduction": 0.6,
}


@pytest.fixture
def strategy_factory(monkeypatch):
    """构造策略实例并接管 identify_support_resistance 返回指定候选位。"""

    def _make(levels_support, levels_resistance=None, params_override=None):
        params = {**LENIENT_PARAMS, **(params_override or {})}
        monkeypatch.setattr(
            "quant_trader.strategies.s_r_levels.identify_support_resistance",
            lambda klines, **kw: {
                "support": levels_support,
                "resistance": levels_resistance or [],
            },
        )
        return SupportResistanceStrategy(STRATEGY_NAME, params, None)

    return _make


def test_fr_leverage_reduction_applies_once_not_compounded(strategy_factory):
    """FR 惩罚 + 两个候选位（第一个被质量门槛拦截）→ 杠杆只降一次。

    修复前：惩罚块在候选位循环内，leverage 逐候选位相乘 5→3→1，
    信号带 1x 出场。修复后循环外预计算一次 5→3。
    """
    current = 106.0
    # 候选位 1：质量 1.0（+FR 惩罚 -1 后 = 0 < secondary_min_quality 6.5 → 被拦截）
    # 候选位 2：质量 9.0（-1 后 = 8 ≥ 6.5 → 通过并生成信号）
    levels = [
        make_level(current * 0.995, quality=1.0),
        make_level(current * 0.995, quality=9.0),
    ]
    strategy = strategy_factory(levels)
    klines = make_klines(rising=True)
    parent = make_klines(rising=False)  # parent 下跌 → bias=short_primary → buy 为副方向

    signals = strategy.on_bar(
        "BTC/USDT", "5m", klines, [],
        parent_klines=parent, funding_rate=0.01,  # 0.01% > 惩罚阈值 0.008%
    )

    buy_signals = [s for s in signals if s.signal_type == "buy"]
    assert len(buy_signals) == 1
    assert buy_signals[0].leverage == 3  # int(5 × 0.6) = 3，而非复合衰减后的 1


def test_no_fr_penalty_keeps_leverage_unchanged(strategy_factory):
    """funding 未超惩罚阈值 → 杠杆保持原值。"""
    current = 106.0
    levels = [make_level(current * 0.995, quality=9.0)]
    strategy = strategy_factory(levels)
    klines = make_klines(rising=True)
    parent = make_klines(rising=False)

    signals = strategy.on_bar(
        "BTC/USDT", "5m", klines, [],
        parent_klines=parent, funding_rate=0.001,  # 低于 0.008% 阈值
    )

    buy_signals = [s for s in signals if s.signal_type == "buy"]
    assert len(buy_signals) == 1
    assert buy_signals[0].leverage == 5
