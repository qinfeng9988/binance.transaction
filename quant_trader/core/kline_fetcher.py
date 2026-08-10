"""K 线数据获取层 - 提供 REST 和 WebSocket 两种实现。

设计目标：
1. 统一接口（BaseKlineFetcher），KlineDataManager 可透明切换
2. WebSocket 优先，REST 降级
3. 支持动态增减订阅（配置热重载）
"""

from abc import ABC, abstractmethod
from collections.abc import Callable


class BaseKlineFetcher(ABC):
    """K 线获取器抽象基类。

    子类需实现 start/stop/subscribe/unsubscribe 和 is_running 属性。
    收到 K 线数据时通过 on_kline 回调通知上层。

    回调签名:
        on_kline(symbol: str, timeframe: str, klines: list[dict], event: str)
        event 取值:
            "update" - K 线正在形成（WebSocket 实时推送）
            "closed" - K 线已收盘（策略执行时机）
    """

    def __init__(self, on_kline: Callable[[str, str, list[dict], str], None]):
        self._on_kline = on_kline
        self._symbol_timeframes: set[tuple[str, str]] = set()

    @abstractmethod
    async def start(self, symbol_timeframes: set[tuple[str, str]]) -> None:
        """启动 K 线获取循环。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止获取，释放资源。"""

    @abstractmethod
    async def subscribe(self, new_items: set[tuple[str, str]]) -> None:
        """动态新增订阅。"""

    @abstractmethod
    async def unsubscribe(self, removed_items: set[tuple[str, str]]) -> None:
        """动态移除订阅。"""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """是否正在运行（连接活跃）。"""

    def _notify(self, symbol: str, timeframe: str, klines: list[dict], event: str) -> None:
        """触发 K 线更新回调。"""
        if self._on_kline is not None:
            self._on_kline(symbol, timeframe, klines, event)
