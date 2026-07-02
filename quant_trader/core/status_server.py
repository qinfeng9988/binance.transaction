"""引擎状态 HTTP 服务。

使用 aiohttp 暴露一个轻量端点，供 `python -m quant_trader.status` 查询引擎实时状态。
"""

import logging

from aiohttp import web

from .serialization import json_dumps, serialize_status

logger = logging.getLogger(__name__)


async def _status_handler(request: web.Request) -> web.Response:
    """GET /api/v1/status 处理器。"""
    engine = request.app["engine"]
    data_store = engine.data_store

    if data_store is None:
        return web.json_response(
            {"error": "引擎尚未初始化"},
            status=503,
            dumps=json_dumps,
        )

    data = await serialize_status(engine, data_store)
    return web.json_response(data, dumps=json_dumps)


def build_status_app(engine) -> web.Application:
    """创建并配置 aiohttp Application。"""
    app = web.Application()
    app["engine"] = engine
    app.router.add_get("/api/v1/status", _status_handler)
    return app


class StatusServer:
    """管理 aiohttp AppRunner 生命周期。"""

    def __init__(self, engine, host: str = "127.0.0.1", port: int = 8080):
        self.engine = engine
        self.host = host
        self.port = port
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self):
        """启动 HTTP 服务。"""
        app = build_status_app(self.engine)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        logger.info(
            "状态 HTTP 服务已启动: http://%s:%d/api/v1/status",
            self.host,
            self.port,
        )

    async def stop(self):
        """停止 HTTP 服务。"""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.info("状态 HTTP 服务已停止")
