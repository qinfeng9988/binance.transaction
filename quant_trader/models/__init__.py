"""全局数据模型。"""

from dataclasses import dataclass, field
from datetime import datetime


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
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)
