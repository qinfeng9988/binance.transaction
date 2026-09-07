"""BacktestKlineDriver 单元测试。

验证：
- 逐根推进产出 K 线事件（含 parent_window、funding_rate 上下文）
- parent 周期窗口对齐（含 parent_lookback 限制）
- funding rate 按时间戳匹配（取不超过当前时间戳的最近记录）
- K 线数据不足时返回 None 窗口
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from quant_trader.core.backtest_kline_driver import (
    BacktestKlineDriver,
    align_parent_klines,
    load_funding_rates,
    match_funding_rate,
    KlineEvent,
)


def make_kline(ts_epoch: int, close: float = 100.0) -> dict:
    """构造一根 K 线（旧→新顺序使用）。"""
    return {
        "timestamp": datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
        "open": close, "high": close, "low": close,
        "close": close, "volume": 1.0,
    }


# ============================================================
# align_parent_klines: 周期对齐算法
# ============================================================

def test_align_parent_klines_basic_alignment():
    """parent K 线时间戳 <= 当前 K 线时间戳的，按时间顺序进入窗口。"""
    # 当前周期：t=100, 200, 300
    cur = [make_kline(100), make_kline(200), make_kline(300)]
    # parent 周期：t=50, 150, 250, 350
    parent = [make_kline(50), make_kline(150), make_kline(250), make_kline(350)]

    windows = align_parent_klines(cur, parent, parent_lookback=None)

    # t=100 时：parent 中 ts<=100 的只有 t=50 → [50]
    assert len(windows[0]) == 1
    assert windows[0][0]["timestamp"].timestamp() == 50
    # t=200 时：parent 中 ts<=200 的有 t=50,150 → 新→旧 = [150, 50]
    assert len(windows[1]) == 2
    assert windows[1][0]["timestamp"].timestamp() == 150
    # t=300 时：parent 中 ts<=300 的有 t=50,150,250
    assert len(windows[2]) == 3
    assert windows[2][0]["timestamp"].timestamp() == 250


def test_align_parent_klines_returns_none_when_no_parent_data():
    """parent 数据不足时窗口为 None。"""
    cur = [make_kline(100), make_kline(200)]
    parent = [make_kline(300)]  # 所有 parent 都在 cur 之后

    windows = align_parent_klines(cur, parent, parent_lookback=None)

    assert windows == [None, None]


def test_align_parent_klines_respects_parent_lookback():
    """parent_lookback 限制窗口大小，只保留最近 N 根 parent。"""
    cur = [make_kline(1000)]
    parent = [make_kline(100), make_kline(200), make_kline(300),
              make_kline(400), make_kline(500)]

    # lookback=2：只保留最后 2 根
    windows = align_parent_klines(cur, parent, parent_lookback=2)

    assert len(windows[0]) == 2
    # 新→旧顺序：[500, 400]
    assert windows[0][0]["timestamp"].timestamp() == 500
    assert windows[0][1]["timestamp"].timestamp() == 400


def test_align_parent_klines_returns_newest_first():
    """窗口顺序必须为新→旧（与实时引擎一致）。"""
    cur = [make_kline(300)]
    parent = [make_kline(100), make_kline(200), make_kline(250)]

    windows = align_parent_klines(cur, parent, parent_lookback=None)

    assert [k["timestamp"].timestamp() for k in windows[0]] == [250, 200, 100]


# ============================================================
# match_funding_rate: 按时间戳匹配最近 funding rate
# ============================================================

def test_match_funding_rate_returns_most_recent_not_after_timestamp():
    """匹配不超过 timestamp_ms 的最近 funding rate。"""
    records = [
        {"timestamp_ms": 1000, "rate_pct": 0.01},
        {"timestamp_ms": 2000, "rate_pct": 0.02},
        {"timestamp_ms": 3000, "rate_pct": 0.03},
    ]

    # T=2500 → 取 2000
    assert match_funding_rate(2500, records) == 0.02
    # T=3000 → 取 3000（边界相等）
    assert match_funding_rate(3000, records) == 0.03
    # T=500 → 无可用 → None
    assert match_funding_rate(500, records) is None


def test_match_funding_rate_returns_none_when_records_empty():
    assert match_funding_rate(1000, []) is None


# ============================================================
# load_funding_rates: JSON 缓存加载
# ============================================================

def test_load_funding_rates_returns_empty_when_file_missing(tmp_path):
    """缓存文件不存在时返回空列表，不抛异常。"""
    result = load_funding_rates("BTC/USDT", cache_dir=tmp_path)
    assert result == []


def test_load_funding_rates_loads_from_json(tmp_path):
    """缓存文件存在时正确加载并按时间戳升序排列。"""
    import json
    cache_file = tmp_path / "BTC_USDT.json"
    records = [
        {"timestamp_ms": 3000, "rate_pct": 0.03},
        {"timestamp_ms": 1000, "rate_pct": 0.01},
        {"timestamp_ms": 2000, "rate_pct": 0.02},
    ]
    cache_file.write_text(json.dumps({"records": records}), encoding="utf-8")

    result = load_funding_rates("BTC/USDT", cache_dir=tmp_path)

    assert [r["timestamp_ms"] for r in result] == [1000, 2000, 3000]


# ============================================================
# BacktestKlineDriver: 迭代器接口
# ============================================================

def test_driver_yields_events_in_chronological_order():
    """驱动器按时间正序逐根产出 K 线事件。"""
    cur_klines = [make_kline(100), make_kline(200), make_kline(300)]
    ds = MagicMock()
    ds.get_klines_range.return_value = cur_klines
    ds.count_klines.return_value = 1000

    driver = BacktestKlineDriver(
        data_store=ds,
        adapter=None,
        window_size=2,
    )

    events = list(driver.iter_klines(
        symbol="BTC/USDT", timeframe="5m",
        start=datetime.fromtimestamp(100, tz=timezone.utc),
        end=datetime.fromtimestamp(300, tz=timezone.utc),
    ))

    assert len(events) == 2  # t=200, t=300（前 window_size-1 根为预热）
    timestamps = [e.kline["timestamp"].timestamp() for e in events]
    assert timestamps == [200, 300]


def test_driver_event_contains_parent_window_when_provided():
    """事件中包含 parent_window（新→旧顺序）。"""
    cur = [make_kline(100), make_kline(150), make_kline(200)]
    parent = [make_kline(150)]
    ds = MagicMock()
    ds.get_klines_range.side_effect = lambda sym, tf, s, e: cur if tf == "5m" else parent
    ds.count_klines.return_value = 1000

    driver = BacktestKlineDriver(data_store=ds, adapter=None, window_size=2)

    events = list(driver.iter_klines(
        symbol="BTC/USDT", timeframe="5m",
        start=datetime.fromtimestamp(100, tz=timezone.utc),
        end=datetime.fromtimestamp(200, tz=timezone.utc),
        parent_timeframe="1h",
    ))

    # window_size=2 时从 i=1 开始产出，共 2 个事件（t=150, t=200）
    # 第一个事件 t=150，parent 中 150 <= 150 → 进入窗口
    assert len(events) == 2
    assert events[0].parent_window is not None
    assert events[0].parent_window[0]["timestamp"].timestamp() == 150


def test_driver_event_funding_rate_matched_by_timestamp():
    """事件的 funding_rate 按当前 K 线时间戳匹配。"""
    cur = [make_kline(100), make_kline(150), make_kline(200)]
    ds = MagicMock()
    ds.get_klines_range.return_value = cur
    ds.count_klines.return_value = 1000

    funding_records = [
        {"timestamp_ms": 150_000, "rate_pct": 0.01},
        {"timestamp_ms": 250_000, "rate_pct": 0.02},
    ]

    driver = BacktestKlineDriver(data_store=ds, adapter=None, window_size=2)

    events = list(driver.iter_klines(
        symbol="BTC/USDT", timeframe="5m",
        start=datetime.fromtimestamp(100, tz=timezone.utc),
        end=datetime.fromtimestamp(200, tz=timezone.utc),
        funding_records=funding_records,
    ))

    # 事件 t=150 → 取 150s（边界相等）
    assert events[0].funding_rate == 0.01
    # 事件 t=200 → 仍取 150s（250s 在 200s 之后）
    assert events[1].funding_rate == 0.01


def test_driver_funding_rate_none_when_no_match():
    """无匹配 funding rate 时返回 None。"""
    cur = [make_kline(100), make_kline(200)]
    ds = MagicMock()
    ds.get_klines_range.return_value = cur
    ds.count_klines.return_value = 1000

    driver = BacktestKlineDriver(data_store=ds, adapter=None, window_size=1)

    events = list(driver.iter_klines(
        symbol="BTC/USDT", timeframe="5m",
        start=datetime.fromtimestamp(100, tz=timezone.utc),
        end=datetime.fromtimestamp(200, tz=timezone.utc),
        funding_records=[],
    ))

    assert events[0].funding_rate is None


def test_driver_event_exposes_funding_ts():
    """事件应暴露命中的 funding 记录时间戳（用于回测主循环检测 funding 事件跨越）。"""
    cur = [make_kline(100), make_kline(150), make_kline(200)]
    ds = MagicMock()
    ds.get_klines_range.return_value = cur
    ds.count_klines.return_value = 1000

    funding_records = [
        {"timestamp_ms": 150_000, "rate_pct": 0.01},
        {"timestamp_ms": 250_000, "rate_pct": 0.02},
    ]

    driver = BacktestKlineDriver(data_store=ds, adapter=None, window_size=2)

    events = list(driver.iter_klines(
        symbol="BTC/USDT", timeframe="5m",
        start=datetime.fromtimestamp(100, tz=timezone.utc),
        end=datetime.fromtimestamp(200, tz=timezone.utc),
        funding_records=funding_records,
    ))

    # 事件 t=150 命中 150s 记录；事件 t=200 仍命中 150s（250s 在未来）
    assert events[0].funding_ts == 150_000
    assert events[1].funding_ts == 150_000
