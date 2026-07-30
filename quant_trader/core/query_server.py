"""引擎状态与查询 HTTP 服务。

使用 aiohttp 暴露端点：
- GET /api/v1/status      : 账户状态查询（原有功能）
- GET /api/v1/query       : S/R 查询（手动交易辅助）
- GET /api/v1/attribution : 交易归因查询（信号/持仓/时间段）
"""

import logging
import time
from pathlib import Path

import yaml
from aiohttp import web

from .serialization import json_dumps, serialize_status

logger = logging.getLogger(__name__)

# 查询结果缓存: (symbol, timeframe) -> (timestamp, response_dict)
_query_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_TTL = 60  # 缓存有效期（秒）

# 支持的时间粒度
SUPPORTED_TIMEFRAMES = {"15m", "30m", "1h", "4h", "1d"}


def _load_manual_config() -> dict:
    """加载手动交易辅助配置文件。"""
    config_path = Path(__file__).parent.parent / "config" / "manual_config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        logger.warning("加载手动交易配置失败，使用默认值")
        return {}


def _normalize_symbol(symbol: str) -> str:
    """标准化交易对符号，自动补全 /USDT 后缀。"""
    symbol = symbol.strip().upper()
    if not symbol.endswith("/USDT"):
        symbol = f"{symbol}/USDT"
    return symbol


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


async def _query_handler(request: web.Request) -> web.Response:
    """GET /api/v1/query 处理器 - 手动交易 S/R 查询。"""
    engine = request.app["engine"]

    # 解析查询参数
    raw_symbol = request.query.get("symbol")
    timeframe = request.query.get("timeframe", "15m")
    mode = request.query.get("mode", "pre_trade")  # pre_trade 或 position

    # 持仓模式参数
    entry_price = request.query.get("entry_price")
    side = request.query.get("side")
    qty = request.query.get("qty")

    # 参数校验
    if not raw_symbol:
        return web.json_response(
            {"error": "缺少必需参数: symbol"},
            status=400,
            dumps=json_dumps,
        )

    # 标准化 symbol
    symbol = _normalize_symbol(raw_symbol)

    # 校验 timeframe
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return web.json_response(
            {"error": f"不支持的时间粒度: {timeframe}", "hint": f"支持的粒度: {', '.join(sorted(SUPPORTED_TIMEFRAMES))}"},
            status=400,
            dumps=json_dumps,
        )

    # 检查缓存（仅 pre_trade 模式缓存，position 模式因入场价不同每次都算）
    cache_key = (symbol, timeframe)
    if mode == "pre_trade" and cache_key in _query_cache:
        cached_ts, cached_data = _query_cache[cache_key]
        if time.time() - cached_ts < _CACHE_TTL:
            return web.json_response(cached_data, dumps=json_dumps)

    # 获取 K 线数据
    klines = engine.kline_manager.get_klines(symbol, timeframe)
    if not klines:
        return web.json_response(
            {
                "error": f"无 K 线数据: {symbol} {timeframe}",
                "hint": "该币种/周期组合未在引擎中配置。查询工具将自动从 Binance 拉取数据。",
            },
            status=404,
            dumps=json_dumps,
        )

    # 获取上级周期 K 线
    from ..main import PARENT_TIMEFRAME
    parent_tf = PARENT_TIMEFRAME.get(timeframe)
    parent_klines = None
    if parent_tf:
        parent_klines = engine.kline_manager.get_klines(symbol, parent_tf)

    # 调用纯分析函数
    from ..technical.support_resistance import analyze_sr_levels

    analysis = analyze_sr_levels(klines, parent_klines=parent_klines)

    if "error" in analysis:
        return web.json_response(
            {"error": analysis["error"]},
            status=400,
            dumps=json_dumps,
        )

    # 加载手动交易配置
    manual_config = _load_manual_config()
    atr_multiplier = manual_config.get("atr_multiplier", 5.5)
    show_levels = manual_config.get("show_levels", 5)

    # 计算手动交易建议
    current_price = analysis["current_price"]
    atr_val = analysis["atr"]

    # 计算建议止损位
    long_stop = current_price - atr_val * atr_multiplier if atr_val > 0 else None
    short_stop = current_price + atr_val * atr_multiplier if atr_val > 0 else None

    # 持仓模式：计算结构化止损
    position_analysis = None
    if mode == "position" and entry_price and side:
        try:
            entry = float(entry_price)
            qty_val = float(qty) if qty else 0
            is_long = side.lower() == "long"

            # 计算当前盈亏
            if is_long:
                unrealized_pnl = (current_price - entry) * qty_val
                profit_pct = (current_price - entry) / entry * 100 if entry != 0 else 0
            else:
                unrealized_pnl = (entry - current_price) * qty_val
                profit_pct = (entry - current_price) / entry * 100 if entry != 0 else 0

            # 计算结构化止损（基于 S/R 位）
            all_levels = analysis["levels"]["support"] + analysis["levels"]["resistance"]
            structured_stop = None

            if is_long:
                # 做多：找低于当前价的最近支撑
                supports = [l for l in all_levels if l["price"] < current_price]
                supports.sort(key=lambda x: x["price"], reverse=True)
                if supports:
                    s_price = supports[0]["price"]
                    buffer_pct = manual_config.get("structured_stop", {}).get("buffer_pct", 0.3)
                    structured_stop = s_price * (1 - buffer_pct / 100)
                    if current_price > entry:
                        # 浮盈：保本止损
                        structured_stop = max(structured_stop, entry)
                    else:
                        # 浮亏：止损必须低于当前价
                        structured_stop = min(structured_stop, current_price)
            else:
                # 做空：找高于当前价的最近压力
                resistances = [l for l in all_levels if l["price"] > current_price]
                resistances.sort(key=lambda x: x["price"])
                if resistances:
                    r_price = resistances[0]["price"]
                    buffer_pct = manual_config.get("structured_stop", {}).get("buffer_pct", 0.3)
                    structured_stop = r_price * (1 + buffer_pct / 100)
                    if current_price < entry:
                        # 浮盈：保本止损
                        structured_stop = min(structured_stop, entry)
                    else:
                        # 浮亏：止损必须高于当前价
                        structured_stop = max(structured_stop, current_price)

            # 计算止盈目标（基于 S/R 位质量分排序）
            tp1, tp1_source, tp1_quality = None, None, None
            tp2, tp2_source, tp2_quality = None, None, None

            if is_long:
                # 做多止盈：从阻力位中按质量分降序取
                tp_candidates = sorted(
                    [l for l in all_levels if l["price"] > current_price],
                    key=lambda x: x["quality_score"], reverse=True,
                )
                if len(tp_candidates) >= 1:
                    tp1 = tp_candidates[0]["price"]
                    tp1_source = "sr"
                    tp1_quality = tp_candidates[0]["quality_score"]
                if len(tp_candidates) >= 2:
                    tp2 = tp_candidates[1]["price"]
                    tp2_source = "sr"
                    tp2_quality = tp_candidates[1]["quality_score"]
                # ATR 备选
                if tp1 is None and atr_val > 0:
                    tp1 = entry + 2 * atr_val
                    tp1_source = "atr"
                if tp2 is None and atr_val > 0:
                    tp2 = entry + 3 * atr_val
                    tp2_source = "atr"
            else:
                # 做空止盈：从支撑位中按质量分降序取
                tp_candidates = sorted(
                    [l for l in all_levels if l["price"] < current_price],
                    key=lambda x: x["quality_score"], reverse=True,
                )
                if len(tp_candidates) >= 1:
                    tp1 = tp_candidates[0]["price"]
                    tp1_source = "sr"
                    tp1_quality = tp_candidates[0]["quality_score"]
                if len(tp_candidates) >= 2:
                    tp2 = tp_candidates[1]["price"]
                    tp2_source = "sr"
                    tp2_quality = tp_candidates[1]["quality_score"]
                # ATR 备选
                if tp1 is None and atr_val > 0:
                    tp1 = entry - 2 * atr_val
                    tp1_source = "atr"
                if tp2 is None and atr_val > 0:
                    tp2 = entry - 3 * atr_val
                    tp2_source = "atr"

            position_analysis = {
                "entry_price": entry,
                "current_price": current_price,
                "side": side,
                "quantity": qty_val,
                "unrealized_pnl": round(unrealized_pnl, 2),
                "profit_pct": round(profit_pct, 2),
                "suggested_stop": round(structured_stop, 2) if structured_stop else None,
                "atr_stop_long": round(long_stop, 2) if long_stop else None,
                "atr_stop_short": round(short_stop, 2) if short_stop else None,
                "tp1": round(tp1, 2) if tp1 else None,
                "tp1_source": tp1_source,
                "tp1_quality": round(tp1_quality, 2) if tp1_quality else None,
                "tp2": round(tp2, 2) if tp2 else None,
                "tp2_source": tp2_source,
                "tp2_quality": round(tp2_quality, 2) if tp2_quality else None,
            }
        except (ValueError, TypeError):
            return web.json_response(
                {"error": "持仓参数格式错误: entry_price, qty 必须为数字"},
                status=400,
                dumps=json_dumps,
            )

    # 按当前价过滤支撑位和压力位
    supports = [s for s in analysis["levels"]["support"] if s["price"] < current_price]
    resistances = [r for r in analysis["levels"]["resistance"] if r["price"] > current_price]

    # 构建响应
    response = {
        "symbol": symbol,
        "timeframe": timeframe,
        "mode": mode,
        "current_price": current_price,
        "timestamp": analysis["timestamp"],
        "trend": analysis["trend"],
        "parent_trend": analysis["parent_trend"],
        "bias": analysis["bias"],
        "adx": analysis["adx"],
        "rsi": analysis["rsi"],
        "atr": atr_val,
        "volume_ratio": analysis["volume_ratio"],
        "is_ranging": analysis["is_ranging"],
        "levels": {
            "support": supports[:show_levels],
            "resistance": resistances[:show_levels],
        },
        "suggested_stops": {
            "atr_multiplier": atr_multiplier,
            "long_stop": round(long_stop, 2) if long_stop else None,
            "short_stop": round(short_stop, 2) if short_stop else None,
        },
    }

    if position_analysis:
        response["position_analysis"] = position_analysis

    # 缓存 pre_trade 结果
    if mode == "pre_trade":
        _query_cache[cache_key] = (time.time(), response)

    return web.json_response(response, dumps=json_dumps)


async def _attribution_handler(request: web.Request) -> web.Response:
    """GET /api/v1/attribution 处理器 - 交易归因查询。"""
    engine = request.app["engine"]
    data_store = engine.data_store

    if data_store is None:
        return web.json_response(
            {"error": "引擎尚未初始化"},
            status=503,
            dumps=json_dumps,
        )

    # 解析查询参数
    query_type = request.query.get("type", "signal")  # signal / position / time_range
    signal_id = request.query.get("signal_id")
    position_id = request.query.get("position_id")
    start_time = request.query.get("start_time")
    end_time = request.query.get("end_time")

    try:
        if query_type == "signal":
            sid = int(signal_id) if signal_id else None
            data = data_store.get_attribution_by_signal(signal_id=sid)
        elif query_type == "position":
            pid = int(position_id) if position_id else None
            data = data_store.get_attribution_by_position(position_id=pid)
        elif query_type == "time_range":
            if not start_time or not end_time:
                return web.json_response(
                    {"error": "time_range 查询需要 start_time 和 end_time 参数"},
                    status=400,
                    dumps=json_dumps,
                )
            data = data_store.get_attribution_by_time_range(start_time, end_time)
        else:
            return web.json_response(
                {"error": f"不支持的查询类型: {query_type}", "hint": "支持: signal, position, time_range"},
                status=400,
                dumps=json_dumps,
            )
    except (ValueError, TypeError) as e:
        return web.json_response(
            {"error": f"参数格式错误: {e}"},
            status=400,
            dumps=json_dumps,
        )

    return web.json_response({"type": query_type, "data": data}, dumps=json_dumps)


def build_status_app(engine) -> web.Application:
    """创建并配置 aiohttp Application。"""
    app = web.Application()
    app["engine"] = engine
    app.router.add_get("/api/v1/status", _status_handler)
    app.router.add_get("/api/v1/query", _query_handler)
    app.router.add_get("/api/v1/attribution", _attribution_handler)
    return app


class QueryServer:
    """管理 aiohttp AppRunner 生命周期。"""

    def __init__(self, engine, host: str = "127.0.0.1", port: int = 58080):
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
            "查询 HTTP 服务已启动: http://%s:%d/api/v1/status, http://%s:%d/api/v1/query, http://%s:%d/api/v1/attribution",
            self.host, self.port, self.host, self.port, self.host, self.port,
        )

    async def stop(self):
        """停止 HTTP 服务。"""
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.info("查询 HTTP 服务已停止")
