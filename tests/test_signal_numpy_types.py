"""信号/记录模型：numpy 标量字段转为原生类型，避免 psycopg2 序列化报错。

回归场景：bollinger 策略用 round(np.float64, n) 产出 rsi/atr 字段，
历史版本泄漏到 insert_signal 触发 InvalidSchemaName: schema "np"。
"""

from datetime import datetime, timezone

import numpy as np

from quant_trader.core.data_store import SignalRecord
from quant_trader.models import Signal


def test_signal_converts_numpy_indicator_fields():
    s = Signal(
        strategy_name="bollinger_bands_paxg",
        symbol="PAXG/USDT",
        timeframe="5m",
        signal_type="buy",
        price=np.float64(100.5),
        stop_loss=np.float64(99.0),
        leverage=2,
        rsi=round(np.float64(55.123), 2),
        atr=round(np.float64(1.23456789), 8),
        is_counter_trend=np.bool_(False),
    )
    assert type(s.rsi) is float
    assert type(s.atr) is float
    assert type(s.is_counter_trend) is bool
    # 未设置的指标字段保持 None
    assert s.adx is None
    assert s.entry_threshold_pct is None


def test_signal_record_converts_numpy_indicator_fields():
    r = SignalRecord(
        strategy_name="bollinger_bands_paxg",
        symbol="PAXG/USDT",
        timeframe="5m",
        signal_type="buy",
        price=np.float64(100.5),
        stop_loss=np.float64(99.0),
        timestamp=datetime.now(tz=timezone.utc),
        leverage=2,
        rsi=round(np.float64(55.123), 2),
        atr=round(np.float64(1.23456789), 8),
    )
    assert type(r.rsi) is float
    assert type(r.atr) is float
    assert r.adx is None


def test_signal_quality_score_and_float_fields():
    s = Signal(
        strategy_name="support_resistance_btc",
        symbol="BTC/USDT",
        timeframe="5m",
        signal_type="buy",
        price=1.0,
        stop_loss=0.9,
        quality_score=np.float64(7.25),
        volume_ratio=np.float64(2.5),
        direction_type="main",
        is_counter_trend=False,
    )
    assert type(s.quality_score) is float
    assert type(s.volume_ratio) is float