"""时间工具函数与交易周期注册表。

PERIODS 是"程序支持哪些周期、各自秒数与父级周期"的唯一来源；秒数映射与
父级周期映射均由它派生，避免多处硬编码漂移。parse_timeframe 是任意间隔的
通用解析器，与注册表职责不同（详见函数 docstring）。
"""

import re
from datetime import datetime, timedelta, timezone

# 交易周期注册表（唯一来源）：周期名 -> (秒数, 父级周期)。
# 父级周期用于上级趋势确认（detect_trend_with_parent）；终端周期父级为 None。
PERIODS: dict[str, tuple[int, str | None]] = {
    "1m": (60, "15m"),
    "3m": (180, "1h"),
    "5m": (300, "1h"),
    "15m": (900, "1h"),
    "30m": (1800, "1h"),
    "1h": (3600, "4h"),
    "4h": (14400, "1d"),
    "1d": (86400, "1w"),
    "3d": (259200, "1w"),
    "1w": (604800, None),
}

# 手动交易查询工具的默认周期清单（manual_config.yaml 未配置 query_timeframes 时回退）。
# 与量化策略的 timeframes 相互独立。
DEFAULT_QUERY_TIMEFRAMES: tuple[str, ...] = (
    "5m", "15m", "30m", "1h", "4h", "1d", "3d", "1w",
)


def timeframe_seconds(timeframe: str) -> int | None:
    """返回周期对应的秒数；周期不在注册表时返回 None。"""
    entry = PERIODS.get(timeframe)
    return entry[0] if entry else None


def parent_timeframe(timeframe: str) -> str | None:
    """返回周期的父级周期；周期未知或为终端周期时返回 None。"""
    entry = PERIODS.get(timeframe)
    return entry[1] if entry else None


def supported_timeframes() -> set[str]:
    """返回注册表内所有受支持的周期名。"""
    return set(PERIODS.keys())


def is_valid_timeframe(timeframe: str) -> bool:
    """判断周期是否在注册表内。"""
    return timeframe in PERIODS


def validate_timeframes(timeframes, where: str) -> None:
    """校验周期列表均在注册表内；含非法周期时抛 ValueError。"""
    for tf in timeframes:
        if not is_valid_timeframe(tf):
            raise ValueError(f"{where} 包含非法周期 '{tf}'")


def parse_timeframe(timeframe: str) -> timedelta:
    """将周期字符串转为 timedelta（通用解析，接受任意数字 + m/h/d/w 单位组合）。

    与注册表 PERIODS 职责不同：本函数用于任意间隔的时长换算（如
    bar_close_time），注册表用于校验周期合法性、派生秒数与父级周期。
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