"""Binance 交易所适配器（基于 ccxt）。

封装 Binance 现货和合约交易，支持自动重连。
使用同步 ccxt + 线程池，以利用 requests 的代理支持。
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

import ccxt

logger = logging.getLogger(__name__)


class ExchangeAdapter:
    """交易所统一接口，便于后续扩展其他交易所。"""

    async def connect(self): ...
    async def close(self): ...
    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict]: ...
    async def fetch_balance(self) -> dict: ...
    async def place_order(self, symbol: str, side: str, amount: float,
                          price: float | None = None, order_type: str = "market",
                          params: dict | None = None) -> dict: ...
    async def cancel_order(self, order_id: str, symbol: str) -> bool: ...
    async def get_positions(self) -> list[dict]: ...


class BinanceAdapter(ExchangeAdapter):
    """Binance 交易所适配器，使用 ccxt 同步接口 + 线程池。

    使用同步版本的原因：
    - requests 库原生支持 HTTP_PROXY/HTTPS_PROXY 环境变量和 proxies 参数
    - aiohttp（ccxt async 底层）的代理配置较复杂且不稳定
    """

    def __init__(self, api_key: str = "", api_secret: str = "",
                 sandbox: bool = True, default_type: str = "swap",
                 proxy: str | None = "http://127.0.0.1:7890"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.sandbox = sandbox
        self.default_type = default_type          # "spot" 或 "swap"
        self.proxy = proxy                         # HTTP 代理地址
        self.exchange: ccxt.binance | None = None
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._reconnect_attempts = 0
        self._max_reconnect = 5

    def connect(self):
        """初始化交易所实例。"""
        # ccxt 中 "swap" 不是标准 defaultType，U 本位合约应使用 "future"
        ccxt_type = "future" if self.default_type == "swap" else self.default_type
        config = {
            "enableRateLimit": True,
            "options": {
                "defaultType": ccxt_type,
                # 减少启动时的 REST 请求量：
                # - fetchMargins: 不拉保证金交易对（margin/allPairs, margin/isolated/allPairs）
                # - fetchCurrencies: 不拉提币网络配置（capital/config/getall）
                # - fetchMarkets: 只加载需要的市场类型，避免无用的 dapi/spot 请求
                "fetchMargins": False,
                "fetchCurrencies": False,
                "fetchMarkets": ["linear"] if ccxt_type == "future" else ["spot"],
            },
        }
        if self.api_key and self.api_secret:
            config["apiKey"] = self.api_key
            config["secret"] = self.api_secret

        # 配置代理（解决国内网络访问 Binance 的问题）
        if self.proxy:
            config["proxies"] = {
                "http": self.proxy,
                "https": self.proxy,
            }
            logger.info("Binance 适配器: 代理已配置 %s", self.proxy)

        self.exchange = ccxt.binance(config)

        if self.sandbox:
            self.exchange.set_sandbox_mode(True)
            logger.info("Binance 适配器: 测试网模式已启用")

        logger.info("Binance 适配器已初始化 (类型=%s, 测试网=%s)",
                     self.default_type, self.sandbox)

    async def close(self):
        """关闭交易所连接。"""
        self._executor.shutdown(wait=False)
        logger.info("Binance 适配器: 连接已关闭")

    async def _run_sync(self, func, *args, **kwargs):
        """在线程池中执行同步 ccxt 调用。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, lambda: func(*args, **kwargs))

    async def _reconnect(self):
        """指数退避重连。"""
        if self._reconnect_attempts >= self._max_reconnect:
            raise RuntimeError(f"达到最大重连次数 ({self._max_reconnect})")
        delay = min(2 ** self._reconnect_attempts, 30)
        self._reconnect_attempts += 1
        logger.warning("正在重连 Binance (第 %d 次, 延迟 %d 秒)",
                       self._reconnect_attempts, delay)
        await asyncio.sleep(delay)
        self.connect()

    async def fetch_ohlcv(self, symbol: str, timeframe: str,
                          limit: int = 500, since: int | None = None) -> list[dict]:
        """获取 K 线数据。

        返回 list[dict]，字段: timestamp, open, high, low, close, volume
        含 3 次重试。

        参数:
            symbol: 交易对
            timeframe: K 线周期
            limit: 最大返回根数
            since: 起始时间戳（毫秒），用于拉取历史数据
        """
        for attempt in range(3):
            try:
                kwargs: dict = {"limit": limit}
                if since is not None:
                    kwargs["since"] = since
                ohlcv = await self._run_sync(
                    self.exchange.fetch_ohlcv, symbol, timeframe, **kwargs
                )
                self._reconnect_attempts = 0
                return [
                    {
                        "timestamp": c[0],
                        "open": c[1],
                        "high": c[2],
                        "low": c[3],
                        "close": c[4],
                        "volume": c[5],
                    }
                    for c in ohlcv
                ]
            except Exception as e:
                logger.warning("获取 K 线失败 (第 %d 次): %s", attempt + 1, e)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise

    async def fetch_balance(self) -> dict:
        """查询账户余额。"""
        try:
            balance = await self._run_sync(self.exchange.fetch_balance)
            return balance
        except Exception as e:
            logger.error("获取余额失败: %s", e)
            raise

    async def place_order(self, symbol: str, side: str, amount: float,
                          price: float | None = None, order_type: str = "market",
                          params: dict | None = None) -> dict:
        """下单。

        参数:
            symbol: 交易对 (如 "BTC/USDT")
            side: "buy" 或 "sell"
            amount: 数量
            price: 限价（限价单必填）
            order_type: "market" 或 "limit"
            params: 额外参数 (如 {"leverage": 5})
        """
        try:
            order = await self._run_sync(
                self.exchange.create_order,
                symbol, order_type, side, amount, price, params or {},
            )
            logger.info("订单已提交: %s %s 数量=%s 价格=%s",
                        side, symbol, amount, order.get("price", "市价"))
            return order
        except Exception as e:
            logger.error("下单失败: %s", e)
            raise

    async def cancel_order(self, order_id: str, symbol: str) -> bool:
        """取消订单。"""
        try:
            await self._run_sync(self.exchange.cancel_order, order_id, symbol)
            return True
        except Exception as e:
            logger.error("撤单失败: %s", e)
            return False

    async def get_positions(self) -> list[dict]:
        """查询合约当前持仓。"""
        try:
            positions = await self._run_sync(self.exchange.fetch_positions)
            return [
                p for p in positions
                if float(p.get("contracts", 0)) > 0
            ]
        except Exception as e:
            logger.error("获取持仓失败: %s", e)
            return []

    async def fetch_funding_rate(self, symbol: str) -> dict | None:
        """获取当前资金费率（Funding Rate）。

        返回 dict: {"fundingRate": float, "fundingTime": int, ...}
        或 None（API 失败时）。

        注意：Binance funding rate 每 8 小时结算一次（00:00/08:00/16:00 UTC），
        但当前预测值每 10 秒更新，可通过此接口获取实时值。
        """
        try:
            result = await self._run_sync(self.exchange.fetch_funding_rate, symbol)
            self._reconnect_attempts = 0
            return result
        except Exception as e:
            logger.warning("获取 funding rate 失败 %s: %s", symbol, e)
            return None

    async def fetch_funding_rate_history(
        self, symbol: str, since: int | None = None, limit: int = 100,
    ) -> list[dict]:
        """获取历史资金费率（用于回测）。

        参数:
            symbol: 交易对
            since: 起始时间戳（毫秒）
            limit: 最大返回条数

        返回:
            list[dict]，每个元素含 fundingRate, fundingTime 等字段
        """
        try:
            kwargs: dict = {"limit": limit}
            if since is not None:
                kwargs["since"] = since
            result = await self._run_sync(
                self.exchange.fetch_funding_rate_history, symbol, **kwargs
            )
            self._reconnect_attempts = 0
            return result if isinstance(result, list) else []
        except Exception as e:
            logger.warning("获取 funding rate 历史失败 %s: %s", symbol, e)
            return []
