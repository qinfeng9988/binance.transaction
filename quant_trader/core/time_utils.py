"""时间工具函数。"""

import re
from datetime import datetime, timedelta, timezone


def parse_timeframe(timeframe: str) -> timedelta:
    """将 K 线周期字符串转为 timedelta。

    支持: "1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"
    """
    m = re.match(r"^(\d+)(m|h|d|w)$", timeframe.lower())
    if not m:
        raise ValueError(f"不支持的 timeframe: {timeframe}")
    value, unit = int(m.group(1)), m.group(2)
    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    return timedelta(seconds=value * multipliers[unit])


def bar_close_time(kline_timestamp: datetime, timeframe: str) -> datetime:
    """计算 K 线收盘时间 = 开盘时间 + 周期时长。

    参数:
        kline_timestamp: K 线开盘时间（带时区）
        timeframe: K 线周期，如 "5m", "1h"
    """
    return kline_timestamp + parse_timeframe(timeframe)
