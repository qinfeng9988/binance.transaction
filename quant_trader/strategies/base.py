"""策略基类。

所有交易策略必须继承此类并实现 on_bar() 方法。
"""

from abc import ABC, abstractmethod

from ..models import Signal


class BaseStrategy(ABC):
    """策略抽象基类。"""

    def __init__(self, name: str, params: dict, config_loader):
        self.name = name
        self.params = params
        self.config_loader = config_loader

    @abstractmethod
    def on_bar(self, symbol: str, timeframe: str, klines: list[dict],
               positions: list) -> list[Signal]:
        """每收到一根新 K 线时调用。

        参数:
            symbol: 交易对 (如 "BTC/USDT")
            timeframe: K 线周期 (如 "5m", "1h")
            klines: OHLCV 数据列表（最新一根在最后）
            positions: 当前持仓列表

        返回:
            Signal 对象列表（无动作为空列表）
        """
        ...
