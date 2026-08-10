"""状态序列化工具。

用于把引擎内部对象（MockPosition、datetime 等）转换成可 JSON 序列化的结构，
供状态 HTTP 服务返回给 status 客户端。
"""

import json
import logging
from dataclasses import asdict
from datetime import datetime

logger = logging.getLogger(__name__)


class _StatusJSONEncoder(json.JSONEncoder):
    """自定义 JSON 编码器，处理 datetime 和 dataclass。"""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def json_dumps(obj, **kwargs):
    """把对象序列化为 JSON 字符串，自动处理 datetime。"""
    return json.dumps(obj, cls=_StatusJSONEncoder, **kwargs)


def serialize_position(pos):
    """把 MockPosition 序列化为 dict。"""
    return asdict(pos)


async def _fetch_realtime_price(exchange, symbol: str) -> float | None:
    """获取币种实时价格（取 1m 最新收盘价，作为降级后备）。"""
    try:
        klines = await exchange.fetch_ohlcv(symbol, "1m", limit=1)
        if klines:
            return klines[0]["close"]
    except Exception:
        logger.warning("获取 %s 实时价格失败", symbol, exc_info=True)
    return None


def _get_realtime_price(engine, symbol: str) -> float | None:
    """从 kline_manager 缓存获取实时价格，无缓存时返回 None。"""
    kline_manager = getattr(engine, "kline_manager", None)
    if kline_manager is None:
        return None
    return kline_manager.get_realtime_price(symbol)


async def serialize_status(engine, data_store):
    """组装引擎当前状态，返回可 JSON 序列化的 dict。

    参数:
        engine: Engine 实例（需已初始化 paper_engine、exchange 和 data_store）。
        data_store: DataStore 实例。

    返回:
        dict，包含 summary、positions、trades、signal_stats、filter_stats。
        持仓价格会实时查询交易所最新价并重新计算未实现盈亏。
    """
    paper = engine.paper_engine

    # 持仓序列化：优先用 kline_manager 实时价格缓存，回退到 REST 请求
    positions = {}
    realtime_unrealized_pnl: dict[str, float] = {}
    for symbol, pos in paper.positions.items():
        pos_dict = serialize_position(pos)
        current_price = pos.current_price

        # 优先用缓存（WebSocket/REST 推送更新）
        cached_price = _get_realtime_price(engine, symbol)
        if cached_price is not None:
            realtime_price: float | None = cached_price
        elif engine.exchange:
            realtime_price = await _fetch_realtime_price(engine.exchange, symbol)
        else:
            realtime_price = None

        if realtime_price is not None:
            current_price = realtime_price
            pos_dict["current_price"] = current_price
            if pos.side == "buy":
                unrealized = (current_price - pos.entry_price) * pos.quantity
            else:
                unrealized = (pos.entry_price - current_price) * pos.quantity
            pos_dict["unrealized_pnl"] = unrealized

        positions[symbol] = pos_dict
        realtime_unrealized_pnl[symbol] = pos_dict.get("unrealized_pnl", 0.0)

    # 最近 20 笔历史交易
    trades = paper.trades_closed[-20:]

    # 信号与订单统计
    with data_store._get_conn().cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM signals")
        total_signals = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM orders WHERE mode = %s", ("paper",))
        total_orders = cur.fetchone()[0]

    signal_stats = {
        "total_signals": total_signals,
        "total_orders": total_orders,
        "blocked": total_signals - total_orders,
    }

    filter_stats = data_store.get_filter_stats()

    # 账户概览：基于实时价格重新计算总权益和收益率
    summary = paper.get_summary()
    if paper.positions:
        balance = paper.balance
        total_equity = balance
        for pos in paper.positions.values():
            if paper.default_type == "swap":
                margin = (pos.entry_price * pos.quantity) / pos.leverage
                total_equity += margin
            total_equity += realtime_unrealized_pnl.get(pos.symbol, 0.0)
        summary["total_equity"] = round(total_equity, 2)
        summary["return_pct"] = round(
            (total_equity - paper.initial_balance) / paper.initial_balance * 100, 2
        )

    return {
        "summary": summary,
        "positions": positions,
        "trades": trades,
        "signal_stats": signal_stats,
        "filter_stats": filter_stats,
    }
