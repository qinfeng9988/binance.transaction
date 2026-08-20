"""全局数据模型。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Signal:
    """策略产生的交易信号。"""
    strategy_name: str
    symbol: str
    timeframe: str
    signal_type: str           # "buy" / "sell" / "hold"
    price: float
    stop_loss: float
    leverage: int = 1
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    metadata: dict = field(default_factory=dict)
    action: str = "open"       # "open" / "update_stop" / "partial_close"
    close_ratio: float = 1.0   # 平仓比例（1.0 = 全平）
    # 策略指标字段（S/R 策略产出，Bollinger 等策略为 None）
    quality_score: float | None = None
    adx: float | None = None
    plus_di: float | None = None
    minus_di: float | None = None
    rsi: float | None = None
    volume_ratio: float | None = None
    parent_trend: str | None = None     # "uptrend" / "downtrend" / "ranging" / "neutral"
    direction_type: str | None = None   # "main" / "secondary"
    is_counter_trend: bool | None = None
    atr: float | None = None
    entry_threshold_pct: float | None = None
    distance_pct: float | None = None

    def __post_init__(self):
        """将 numpy 等外部数值类型转为 Python 原生类型，避免数据库序列化报错。"""
        self.price = float(self.price)
        self.stop_loss = float(self.stop_loss)
        self.leverage = int(self.leverage)
