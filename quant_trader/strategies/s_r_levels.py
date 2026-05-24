"""支撑/压力位交易策略。

逻辑：
- 从历史 K 线中识别支撑位和压力位
- 用 EMA 判断趋势方向，过滤反向信号
- 当价格接近强支撑位且趋势向上时发出买入信号
- 当价格接近强压力位且趋势向下时发出卖出信号
- 止损设在支撑位下方（做多时）
"""

import logging

from ..models import Signal
from .base import BaseStrategy
from ..technical.indicators import ema
from ..technical.support_resistance import identify_support_resistance

logger = logging.getLogger(__name__)


def detect_trend(klines: list[dict]) -> str:
    """用双 EMA 交叉判断趋势方向。

    返回: "uptrend" / "downtrend" / "ranging"
    """
    if len(klines) < 50:
        return "ranging"

    ema_fast = ema(klines, 21)   # 快线
    ema_slow = ema(klines, 50)   # 慢线

    fast = ema_fast[-1]
    slow = ema_slow[-1]
    gap_pct = abs(fast - slow) / slow * 100

    # EMA 间距太小视为震荡
    if gap_pct < 0.3:
        return "ranging"

    return "uptrend" if fast > slow else "downtrend"


class SupportResistanceStrategy(BaseStrategy):
    """基于支撑/压力位的交易策略，带趋势过滤。"""

    def on_bar(self, symbol: str, timeframe: str, klines: list[dict],
               positions: list) -> list[Signal]:
        if len(klines) < 20:
            return []

        params = self.params
        lookback = params.get("lookback", 100)            # 回看 K 线根数
        min_touches = params.get("min_touches", 2)        # 最少触碰次数
        stop_loss_pct = params.get("stop_loss_pct", 1.5)  # 止损百分比
        entry_threshold_pct = params.get("entry_threshold_pct", 0.5)  # 入场阈值
        leverage = params.get("leverage", 1)

        # 判断趋势方向
        trend = detect_trend(klines)

        # 识别支撑/压力位
        levels = identify_support_resistance(klines, min_touches=min_touches, lookback=lookback)
        current_price = klines[0]["close"]  # 反转后 klines[0] 是最新K线

        signals = []

        # 做多条件：价格靠近支撑位 + (趋势向上 或 震荡)
        can_buy = trend in ("uptrend", "ranging")
        if can_buy:
            for support in levels["support"]:
                s_price = support["price"]
                distance_pct = (current_price - s_price) / s_price * 100

                if 0 <= distance_pct <= entry_threshold_pct:
                    stop_loss = s_price * (1 - stop_loss_pct / 100)
                    signal = Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        timeframe=timeframe,
                        signal_type="buy",
                        price=current_price,
                        stop_loss=stop_loss,
                        leverage=leverage,
                        metadata={
                            "support_level": s_price,
                            "support_strength": support["strength"],
                            "distance_to_support_pct": round(distance_pct, 3),
                            "trend": trend,
                        },
                    )
                    signals.append(signal)
                    logger.info(
                        "做多信号 %s %s: 现价=%.2f, 支撑=%.2f, 距离=%.3f%%, 趋势=%s, 止损=%.2f",
                        symbol, timeframe, current_price, s_price, distance_pct, trend, stop_loss,
                    )
                    break  # 每根 K 线只产生一个做多信号

        # 做空条件：价格靠近压力位 + (趋势向下 或 震荡)
        can_sell = trend in ("downtrend", "ranging")
        if can_sell:
            for resistance in levels["resistance"]:
                r_price = resistance["price"]
                distance_pct = (r_price - current_price) / r_price * 100

                if 0 <= distance_pct <= entry_threshold_pct:
                    stop_loss = r_price * (1 + stop_loss_pct / 100)
                    signal = Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        timeframe=timeframe,
                        signal_type="sell",
                        price=current_price,
                        stop_loss=stop_loss,
                        leverage=leverage,
                        metadata={
                            "resistance_level": r_price,
                            "resistance_strength": resistance["strength"],
                            "distance_to_resistance_pct": round(distance_pct, 3),
                            "trend": trend,
                        },
                    )
                    signals.append(signal)
                    logger.info(
                        "做空信号 %s %s: 现价=%.2f, 压力=%.2f, 距离=%.3f%%, 趋势=%s, 止损=%.2f",
                        symbol, timeframe, current_price, r_price, distance_pct, trend, stop_loss,
                    )
                    break  # 每根 K 线只产生一个做空信号

        return signals
