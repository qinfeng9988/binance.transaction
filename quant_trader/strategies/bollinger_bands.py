"""双均线交叉趋势策略（适用于低波动交易对如 PAXG/USDT）。

核心逻辑：
- 快线（EMA9）上穿慢线（EMA21）时做多
- 快线（EMA9）下穿慢线（EMA21）时做空
- 简单明了，适合低波动交易对
- 结合 RSI 过滤极端情况
"""

import logging

from ..models import Signal
from .base import BaseStrategy
from ..technical.indicators import ema, rsi, atr

logger = logging.getLogger(__name__)


def _chronological(klines: list[dict]) -> list[dict]:
    return list(reversed(klines))


class BollingerBandsStrategy(BaseStrategy):
    """双均线交叉趋势策略（使用 BollingerBandsStrategy 类名保持兼容）。"""

    def on_bar(self, symbol: str, timeframe: str, klines: list[dict],
               positions: list, parent_klines: list[dict] | None = None,
               grandparent_klines: list[dict] | None = None,
               funding_rate: float | None = None) -> list[Signal]:
        # 惰性初始化累计过滤统计（必须在 len(klines)<50 提前返回之前，
        # 否则预热期 get_filter_summary 返回全零导致 upsert 覆盖有效行）
        if not hasattr(self, "_filter_summary"):
            self._filter_summary = {
                "buy": {"checked": 0, "rsi": 0, "passed": 0},
                "sell": {"checked": 0, "rsi": 0, "passed": 0},
            }

        if len(klines) < 50:
            return []

        params = self.params

        fast_period = params.get("ema_fast_period", 9)
        slow_period = params.get("ema_slow_period", 21)
        rsi_period = params.get("rsi_period", 14)
        rsi_overbought = params.get("rsi_overbought", 75)
        rsi_oversold = params.get("rsi_oversold", 25)
        atr_period = params.get("atr_period", 14)
        atr_multiplier = params.get("atr_multiplier", 2.0)
        leverage = params.get("leverage", 2)

        chrono = _chronological(klines)

        # 计算 EMA
        fast_vals = ema(chrono, fast_period)
        slow_vals = ema(chrono, slow_period)

        current_price = klines[0]["close"]
        current_fast = fast_vals[-1]
        current_slow = slow_vals[-1]
        prev_fast = fast_vals[-2] if len(fast_vals) > 1 else current_fast
        prev_slow = slow_vals[-2] if len(slow_vals) > 1 else current_slow

        # RSI
        rsi_vals = rsi(chrono, period=rsi_period)
        current_rsi = rsi_vals[-1] if len(rsi_vals) > 0 else 50

        # ATR
        atr_vals = atr(chrono, period=atr_period)
        current_atr = atr_vals[-1] if len(atr_vals) > 0 else 0

        signals = []

        existing_pos = None
        for pos in positions:
            if getattr(pos, "symbol", None) == symbol or (isinstance(pos, dict) and pos.get("symbol") == symbol):
                existing_pos = pos
                break

        # ---- 平仓逻辑：均线反向交叉 ----
        if existing_pos:
            side = getattr(existing_pos, "side", None) or existing_pos.get("side")
            entry_price = getattr(existing_pos, "entry_price", None) or existing_pos.get("entry_price", current_price)
            stop_loss = entry_price - current_atr * atr_multiplier if side == "buy" else entry_price + current_atr * atr_multiplier

            if side == "buy" and prev_fast >= prev_slow and current_fast < current_slow:
                signals.append(Signal(
                    strategy_name=self.name, symbol=symbol, timeframe=timeframe,
                    signal_type="sell", price=current_price, stop_loss=stop_loss,
                    leverage=leverage, action="close",
                    metadata={"reason": "ema_cross_down"},
                ))
            elif side == "sell" and prev_fast <= prev_slow and current_fast > current_slow:
                signals.append(Signal(
                    strategy_name=self.name, symbol=symbol, timeframe=timeframe,
                    signal_type="buy", price=current_price, stop_loss=stop_loss,
                    leverage=leverage, action="close",
                    metadata={"reason": "ema_cross_up"},
                ))

        # ---- 开仓逻辑：均线交叉 ----
        if not existing_pos:
            # 金叉做多
            if prev_fast <= prev_slow and current_fast > current_slow:
                self._filter_summary["buy"]["checked"] += 1
                if current_rsi < rsi_overbought:
                    stop_loss = current_price - current_atr * atr_multiplier
                    signals.append(Signal(
                        strategy_name=self.name, symbol=symbol, timeframe=timeframe,
                        signal_type="buy", price=current_price, stop_loss=stop_loss, leverage=leverage,
                        rsi=round(current_rsi, 2), atr=round(current_atr, 8),
                        metadata={"fast": current_fast, "slow": current_slow, "rsi": current_rsi, "atr": current_atr},
                    ))
                    self._filter_summary["buy"]["passed"] += 1
                    logger.info("EMA金叉做多 %s %s: 价格=%.2f, EMA%d=%.2f, EMA%d=%.2f, RSI=%.1f",
                               symbol, timeframe, current_price, fast_period, current_fast, slow_period, current_slow, current_rsi)
                else:
                    self._filter_summary["buy"]["rsi"] += 1
                    logger.debug("RSI过滤做多 %s %s: RSI=%.1f >= %.0f",
                                symbol, timeframe, current_rsi, rsi_overbought)

            # 死叉做空
            elif prev_fast >= prev_slow and current_fast < current_slow:
                self._filter_summary["sell"]["checked"] += 1
                if current_rsi > rsi_oversold:
                    stop_loss = current_price + current_atr * atr_multiplier
                    signals.append(Signal(
                        strategy_name=self.name, symbol=symbol, timeframe=timeframe,
                        signal_type="sell", price=current_price, stop_loss=stop_loss, leverage=leverage,
                        rsi=round(current_rsi, 2), atr=round(current_atr, 8),
                        metadata={"fast": current_fast, "slow": current_slow, "rsi": current_rsi, "atr": current_atr},
                    ))
                    self._filter_summary["sell"]["passed"] += 1
                    logger.info("EMA死叉做空 %s %s: 价格=%.2f, EMA%d=%.2f, EMA%d=%.2f, RSI=%.1f",
                               symbol, timeframe, current_price, fast_period, current_fast, slow_period, current_slow, current_rsi)
                else:
                    self._filter_summary["sell"]["rsi"] += 1
                    logger.debug("RSI过滤做空 %s %s: RSI=%.1f <= %.0f",
                                symbol, timeframe, current_rsi, rsi_oversold)

        return signals

    def get_filter_summary(self) -> dict:
        """返回过滤统计（兼容接口，仅追踪均线交叉与 RSI 过滤）。"""
        accumulated = getattr(self, "_filter_summary", {
            "buy": {"checked": 0, "rsi": 0, "passed": 0},
            "sell": {"checked": 0, "rsi": 0, "passed": 0},
        })
        # 补齐完整字段以兼容 upsert_filter_stats；Bollinger 策略不使用其余过滤层
        _zero = {"distance": 0, "adx": 0, "di": 0, "quality": 0, "ema": 0,
                 "volume": 0, "rejection": 0, "pullback": 0,
                 "fr_hard_block": 0, "fr_penalty": 0, "add": 0,
                 "add_limit": 0, "add_quality": 0}
        return {
            "buy": {**_zero, **accumulated["buy"]},
            "sell": {**_zero, **accumulated["sell"]},
        }
