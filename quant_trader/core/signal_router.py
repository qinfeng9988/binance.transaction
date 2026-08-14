"""信号路由函数。

从 main.py._handle_signal 抽取的核心逻辑，回测和实盘 paper 模式共用。

职责：
1. 写入 signals 表
2. 根据 signal.action 分发：
   - update_stop → 更新止损
   - partial_close → 部分平仓
   - close → 全平
   - open（默认）→ 风控检查 → 下单（含反向平仓检测）

注意：
- 实盘 live_confirm / live_auto 模式由 main.py 自行处理，
  本函数只处理 paper 模式（含回测）
- 回测和实盘 paper 共用本函数，确保信号路由逻辑一致
"""

import logging
from dataclasses import dataclass
from typing import Literal

from ..core.data_store import SignalRecord
from ..models import Signal

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """信号处理结果，用于回测统计和日志。"""
    signal_id: int
    action: Literal[
        "update_stop", "update_stop_skipped",
        "partial_close", "partial_close_skipped",
        "close", "close_skipped",
        "open", "risk_rejected",
    ]
    pnl: float | None = None
    order_id: str | None = None
    reason: str | None = None


def handle_signal(
    signal: Signal,
    paper_engine,
    data_store,
    risk_manager,
    mode: str = "paper",
    position_value_ratio: float = 0.1,
) -> SignalResult:
    """处理策略信号：记录、风控、执行。

    参数:
        signal: 策略产出的 Signal 对象
        paper_engine: PaperTradingEngine 实例
        data_store: DataStore 或 BacktestDataStore 实例
        risk_manager: RiskManager 实例（开仓信号需要）
        mode: 交易模式，"paper" 走模拟下单（回测固定为 paper）
        position_value_ratio: 开仓时使用余额的比例（默认 0.1 = 10%）

    返回:
        SignalResult 描述处理结果
    """
    logger.info(
        "信号: %s %s %s 价格=%.2f 止损=%.2f 杠杆=%dx action=%s",
        signal.symbol, signal.signal_type, signal.timeframe,
        signal.price, signal.stop_loss, signal.leverage, signal.action,
    )

    # 写信号日志
    sig_logger = logging.getLogger("signals")
    sig_logger.info(
        "%s %s %s 价格=%.2f 止损=%.2f 杠杆=%dx",
        signal.symbol, signal.signal_type, signal.strategy_name,
        signal.price, signal.stop_loss, signal.leverage,
    )

    # 写入 signals 表
    signal_id = data_store.insert_signal(SignalRecord(
        strategy_name=signal.strategy_name,
        symbol=signal.symbol,
        timeframe=signal.timeframe,
        signal_type=signal.signal_type,
        price=signal.price,
        stop_loss=signal.stop_loss,
        timestamp=signal.timestamp,
        leverage=signal.leverage,
    ))

    # ============ update_stop ============
    if signal.action == "update_stop":
        updated = paper_engine.update_stop_loss(signal.symbol, signal.stop_loss)
        if updated:
            logger.info("[止损更新] %s 新止损=%.2f", signal.symbol, signal.stop_loss)
            return SignalResult(signal_id=signal_id, action="update_stop")
        return SignalResult(signal_id=signal_id, action="update_stop_skipped")

    # ============ partial_close ============
    if signal.action == "partial_close":
        tp_level = signal.metadata.get("partial_tp_level") if signal.metadata else None
        net_pnl = paper_engine.partial_close_position(
            signal.symbol, signal.price, signal.close_ratio, data_store,
            signal_id=signal_id, partial_tp_level=tp_level,
        )
        if net_pnl is not None:
            logger.info(
                "[部分平仓] %s 比例=%.2f 价格=%.2f 盈亏=%.2f",
                signal.symbol, signal.close_ratio, signal.price, net_pnl,
            )
            return SignalResult(
                signal_id=signal_id, action="partial_close", pnl=net_pnl,
            )
        return SignalResult(signal_id=signal_id, action="partial_close_skipped")

    # ============ close（反向平仓或全平信号）============
    if signal.action == "close":
        existing = paper_engine.positions.get(signal.symbol)
        if not existing:
            return SignalResult(signal_id=signal_id, action="close_skipped")
        net_pnl = paper_engine.close_position(
            signal.symbol, signal.price, data_store,
            order_type="close", signal_id=signal_id,
            trigger_reason=signal.metadata.get("reason", "signal_close"),
        )
        if net_pnl is not None:
            logger.info(
                "[平仓] %s 价格=%.2f 盈亏=%.2f 原因=%s",
                signal.symbol, signal.price, net_pnl,
                signal.metadata.get("reason", "close_signal"),
            )
            return SignalResult(
                signal_id=signal_id, action="close", pnl=net_pnl,
            )
        return SignalResult(signal_id=signal_id, action="close_skipped")

    # ============ 开仓类信号（action="open" 或其他）============
    # 实盘 live_confirm / live_auto 由 main.py 自行处理，本函数只处理 paper 模式
    if mode != "paper":
        logger.warning("signal_router 仅处理 paper 模式，当前 mode=%s", mode)
        return SignalResult(signal_id=signal_id, action="risk_rejected", reason=f"unsupported mode: {mode}")

    # 风控检查
    risk_result = risk_manager.check_and_adjust(signal)
    if not risk_result["approved"]:
        logger.warning(
            "[风控拒绝] %s %s: %s",
            signal.symbol, signal.signal_type, risk_result["reason"],
        )
        return SignalResult(
            signal_id=signal_id, action="risk_rejected",
            reason=risk_result["reason"],
        )

    # 应用风控调整后的杠杆
    signal.leverage = risk_result["leverage"]

    # 计算下单数量
    position_value = paper_engine.balance * position_value_ratio
    quantity = position_value / signal.price

    # 检测反向平仓（持 buy 收 sell 或持 sell 收 buy）
    existing_pos = paper_engine.positions.get(signal.symbol)
    is_closing = existing_pos is not None and signal.signal_type != existing_pos.side

    # 下单
    order = paper_engine.place_order(
        symbol=signal.symbol,
        side=signal.signal_type,
        amount=quantity,
        price=signal.price,
        leverage=signal.leverage,
        stop_loss=signal.stop_loss,
        data_store=data_store,
        metadata=signal.metadata,
        order_type="close" if is_closing else None,
        signal_id=signal_id,
    )

    if order is None:
        logger.warning("[下单失败] %s %s 余额不足", signal.symbol, signal.signal_type)
        return SignalResult(
            signal_id=signal_id, action="risk_rejected", reason="余额不足",
        )

    trade_logger = logging.getLogger("trades")
    trade_logger.info(
        "[模拟成交] %s %s 价格=%.2f 数量=%.4f 杠杆=%dx 订单=%s",
        signal.symbol, signal.signal_type, signal.price,
        quantity, signal.leverage, order.order_id,
    )

    return SignalResult(
        signal_id=signal_id, action="open", order_id=order.order_id,
    )
