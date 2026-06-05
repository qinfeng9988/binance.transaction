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
    action: str = "open"       # "open" / "update_stop" / "partial_close"
    close_ratio: float = 1.0   # 平仓比例（1.0 = 全平）

    def __post_init__(self):
        """将 numpy 等外部数值类型转为 Python 原生类型，避免数据库序列化报错。"""
        self.price = float(self.price)
        self.stop_loss = float(self.stop_loss)
        self.leverage = int(self.leverage)
