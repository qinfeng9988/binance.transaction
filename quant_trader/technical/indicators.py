"""技术指标计算封装。

优先使用 TA-Lib（C 底层，速度快），不可用时回退到纯 numpy 实现。
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)

# 尝试导入 TA-Lib
try:
    import talib
    HAS_TALIB = True
except ImportError:
    HAS_TALIB = False
    logger.warning("TA-Lib 不可用，使用纯 numpy 备选方案")


def _extract_closes(klines: list[dict]) -> np.ndarray:
    """从 K 线数据中提取收盘价数组。"""
    return np.array([k["close"] for k in klines], dtype=float)


def rsi(klines: list[dict], period: int = 14) -> np.ndarray:
    """计算 RSI（相对强弱指标）。"""
    closes = _extract_closes(klines)
    if HAS_TALIB:
        return talib.RSI(closes, timeperiod=period)

    # 纯 numpy 实现
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.zeros_like(closes)
    avg_loss = np.zeros_like(closes)
    avg_gain[period] = gains[:period].mean() if len(gains) >= period else 0
    avg_loss[period] = losses[:period].mean() if len(losses) >= period else 0

    for i in range(period + 1, len(closes)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gains[i - 1]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + losses[i - 1]) / period

    rs = avg_gain / np.where(avg_loss == 0, 1e-10, avg_loss)
    return 100 - (100 / (1 + rs))


def ema(klines: list[dict], period: int = 21) -> np.ndarray:
    """计算 EMA（指数移动平均）。"""
    closes = _extract_closes(klines)
    if HAS_TALIB:
        return talib.EMA(closes, timeperiod=period)

    multiplier = 2 / (period + 1)
    ema_vals = np.zeros_like(closes)
    ema_vals[period - 1] = closes[:period].mean()
    for i in range(period, len(closes)):
        ema_vals[i] = (closes[i] * multiplier) + (ema_vals[i - 1] * (1 - multiplier))
    return ema_vals


def macd(klines: list[dict], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算 MACD。

    返回: (macd线, 信号线, 柱状图)
    """
    closes = _extract_closes(klines)
    if HAS_TALIB:
        macd_line, signal_line, histogram = talib.MACD(closes, fastperiod=fast, slowperiod=slow, signalperiod=signal)
        return macd_line, signal_line, histogram

    fast_ema = ema(klines, fast)
    slow_ema = ema(klines, slow)
    macd_line = fast_ema - slow_ema

    # 信号线 = MACD 的 EMA
    multiplier = 2 / (signal + 1)
    signal_line = np.zeros_like(macd_line)
    signal_line[signal - 1] = macd_line[:signal].mean() if len(macd_line) >= signal else 0
    for i in range(signal, len(macd_line)):
        signal_line[i] = (macd_line[i] * multiplier) + (signal_line[i - 1] * (1 - multiplier))

    return macd_line, signal_line, macd_line - signal_line


def bollinger_bands(klines: list[dict], period: int = 20, std_dev: float = 2.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算布林带。

    返回: (上轨, 中轨, 下轨)
    """
    closes = _extract_closes(klines)
    if HAS_TALIB:
        return talib.BBANDS(closes, timeperiod=period, nbdevup=std_dev, nbdevdn=std_dev)

    middle = ema(klines, period)
    std = np.array([
        np.std(closes[max(0, i - period):i + 1]) if i >= period - 1 else 0
        for i in range(len(closes))
    ])
    return middle + std_dev * std, middle, middle - std_dev * std
