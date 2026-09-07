"""周期注册表（time_utils.PERIODS）单元测试。"""

import pytest

from quant_trader.core.time_utils import (
    DEFAULT_QUERY_TIMEFRAMES,
    PERIODS,
    is_valid_timeframe,
    parent_timeframe,
    parse_timeframe,
    supported_timeframes,
    timeframe_seconds,
    validate_timeframes,
)


def test_periods_cover_high_timeframes():
    """注册表必须包含此前缺失的 30m/3d/1w。"""
    for tf in ("30m", "3d", "1w"):
        assert tf in PERIODS


def test_timeframe_seconds():
    assert timeframe_seconds("5m") == 300
    assert timeframe_seconds("3d") == 259200
    assert timeframe_seconds("1w") == 604800


def test_timeframe_seconds_unknown_returns_none():
    assert timeframe_seconds("5mm") is None


def test_parent_timeframe():
    assert parent_timeframe("5m") == "1h"
    assert parent_timeframe("30m") == "1h"
    assert parent_timeframe("1d") == "1w"
    assert parent_timeframe("3d") == "1w"


def test_parent_timeframe_terminal_is_none():
    assert parent_timeframe("1w") is None


def test_is_valid_timeframe():
    assert is_valid_timeframe("1h") is True
    assert is_valid_timeframe("5mm") is False


def test_validate_timeframes_raises_on_invalid():
    with pytest.raises(ValueError):
        validate_timeframes(["5m", "5mm"], where="test")


def test_validate_timeframes_passes_on_valid():
    validate_timeframes(["5m", "1h", "1w"], where="test")


def test_parse_timeframe_remains_generic():
    """parse_timeframe 仍是通用解析，接受任意间隔（不受注册表限制）。"""
    assert parse_timeframe("2h").total_seconds() == 7200
    assert parse_timeframe("45m").total_seconds() == 2700


def test_supported_timeframes_is_superset_of_default_query():
    assert set(DEFAULT_QUERY_TIMEFRAMES) <= supported_timeframes()