"""signal_router 单元测试。

验证：
- update_stop 信号 → 调用 paper_engine.update_stop_loss
- partial_close 信号 → 调用 paper_engine.partial_close_position，传 signal_id
- close 信号 → 调用 paper_engine.close_position
- 开仓信号 → 走风控 + paper_engine.place_order
- 风控拒绝时不调用 place_order
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from quant_trader.core.signal_router import handle_signal
from quant_trader.models import Signal


def make_signal(**kwargs) -> Signal:
    """构造信号。"""
    defaults = {
        "strategy_name": "sr_btc",
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "signal_type": "buy",
        "price": 50000.0,
        "stop_loss": 49000.0,
        "timestamp": datetime.now(tz=timezone.utc),
        "leverage": 5,
    }
    defaults.update(kwargs)
    return Signal(**defaults)


# ============================================================
# update_stop 信号
# ============================================================

def test_handle_update_stop_calls_update_stop_loss():
    """update_stop 信号 → 调用 paper_engine.update_stop_loss。"""
    signal = make_signal(action="update_stop", stop_loss=49500.0)
    paper_engine = MagicMock()
    paper_engine.update_stop_loss.return_value = True
    data_store = MagicMock()
    data_store.insert_signal.return_value = 1
    risk_manager = MagicMock()

    result = handle_signal(
        signal=signal,
        paper_engine=paper_engine,
        data_store=data_store,
        risk_manager=risk_manager,
        mode="paper",
    )

    paper_engine.update_stop_loss.assert_called_once_with("BTC/USDT", 49500.0)
    paper_engine.partial_close_position.assert_not_called()
    paper_engine.place_order.assert_not_called()
    assert result.signal_id == 1
    assert result.action == "update_stop"


def test_handle_update_stop_returns_skipped_when_no_position():
    """update_stop 信号但无持仓 → update_stop_loss 返回 False，不报错。"""
    signal = make_signal(action="update_stop", stop_loss=49500.0)
    paper_engine = MagicMock()
    paper_engine.update_stop_loss.return_value = False
    data_store = MagicMock()
    data_store.insert_signal.return_value = 1

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=MagicMock(), mode="paper",
    )

    assert result.action == "update_stop_skipped"


# ============================================================
# partial_close 信号
# ============================================================

def test_handle_partial_close_calls_partial_close_position():
    """partial_close 信号 → 调用 paper_engine.partial_close_position，传 signal_id 和 tp_level。"""
    signal = make_signal(
        action="partial_close",
        price=51000.0,
        close_ratio=0.5,
        metadata={"partial_tp_level": "2.0R"},
    )
    paper_engine = MagicMock()
    paper_engine.partial_close_position.return_value = 100.0
    data_store = MagicMock()
    data_store.insert_signal.return_value = 42

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=MagicMock(), mode="paper",
    )

    paper_engine.partial_close_position.assert_called_once()
    call_kwargs = paper_engine.partial_close_position.call_args.kwargs
    assert call_kwargs["signal_id"] == 42
    assert call_kwargs["partial_tp_level"] == "2.0R"
    assert result.pnl == 100.0


def test_handle_partial_close_skipped_when_no_position():
    """partial_close 但无持仓 → partial_close_position 返回 None。"""
    signal = make_signal(action="partial_close", close_ratio=0.5)
    paper_engine = MagicMock()
    paper_engine.partial_close_position.return_value = None
    data_store = MagicMock()
    data_store.insert_signal.return_value = 1

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=MagicMock(), mode="paper",
    )

    assert result.action == "partial_close_skipped"


# ============================================================
# close 信号
# ============================================================

def test_handle_close_calls_close_position_when_holding():
    """close 信号且持仓 → 调用 paper_engine.close_position。"""
    signal = make_signal(action="close", price=51000.0)
    paper_engine = MagicMock()
    paper_engine.positions.get.return_value = MagicMock()  # 有持仓
    paper_engine.close_position.return_value = 200.0
    data_store = MagicMock()
    data_store.insert_signal.return_value = 5

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=MagicMock(), mode="paper",
    )

    paper_engine.close_position.assert_called_once()
    call_kwargs = paper_engine.close_position.call_args.kwargs
    assert call_kwargs["signal_id"] == 5
    assert call_kwargs["order_type"] == "close"
    assert result.pnl == 200.0


def test_handle_close_skipped_when_no_position():
    """close 信号但无持仓 → 不调用 close_position。"""
    signal = make_signal(action="close")
    paper_engine = MagicMock()
    paper_engine.positions.get.return_value = None
    data_store = MagicMock()
    data_store.insert_signal.return_value = 1

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=MagicMock(), mode="paper",
    )

    paper_engine.close_position.assert_not_called()
    assert result.action == "close_skipped"


# ============================================================
# 开仓信号（action="open"，默认）
# ============================================================

def test_handle_open_signal_passes_risk_check_and_places_order():
    """开仓信号通过风控 → paper_engine.place_order 被调用。"""
    signal = make_signal(signal_type="buy")
    paper_engine = MagicMock()
    paper_engine.balance = 100000.0
    paper_engine.positions.get.return_value = None
    mock_order = MagicMock()
    mock_order.order_id = "PAPER-1"
    paper_engine.place_order.return_value = mock_order

    risk_manager = MagicMock()
    risk_manager.check_and_adjust.return_value = {
        "approved": True, "leverage": 5, "stop_loss": 49000.0,
    }
    data_store = MagicMock()
    data_store.insert_signal.return_value = 10

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=risk_manager, mode="paper",
    )

    risk_manager.check_and_adjust.assert_called_once()
    paper_engine.place_order.assert_called_once()
    assert result.action == "open"
    assert result.order_id == "PAPER-1"


def test_handle_open_signal_rejected_by_risk():
    """风控拒绝 → 不调用 place_order。"""
    signal = make_signal(signal_type="buy")
    paper_engine = MagicMock()
    risk_manager = MagicMock()
    risk_manager.check_and_adjust.return_value = {
        "approved": False, "reason": "止损距离超限",
    }
    data_store = MagicMock()
    data_store.insert_signal.return_value = 10

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=risk_manager, mode="paper",
    )

    paper_engine.place_order.assert_not_called()
    assert result.action == "risk_rejected"
    assert "止损距离超限" in result.reason


def test_handle_open_signal_uses_risk_adjusted_leverage():
    """开仓时使用风控调整后的杠杆值。"""
    signal = make_signal(signal_type="buy", leverage=10)
    paper_engine = MagicMock()
    paper_engine.balance = 100000.0
    paper_engine.positions.get.return_value = None
    paper_engine.place_order.return_value = MagicMock(order_id="PAPER-1")

    risk_manager = MagicMock()
    risk_manager.check_and_adjust.return_value = {
        "approved": True, "leverage": 3, "stop_loss": 49000.0,  # 杠杆被降级
    }
    data_store = MagicMock()
    data_store.insert_signal.return_value = 10

    handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=risk_manager, mode="paper",
    )

    call_kwargs = paper_engine.place_order.call_args.kwargs
    assert call_kwargs["leverage"] == 3  # 用风控调整后的值


def test_handle_open_signal_reports_skipped_when_order_ignored():
    """加仓被引擎忽略（order_type="ignored"）→ 上报 open_skipped 而非 open。"""
    signal = make_signal(signal_type="buy")
    paper_engine = MagicMock()
    paper_engine.balance = 100000.0
    paper_engine.positions.get.return_value = None
    paper_engine.place_order.return_value = MagicMock(
        order_id="PAPER-2", order_type="ignored",
    )

    risk_manager = MagicMock()
    risk_manager.check_and_adjust.return_value = {
        "approved": True, "leverage": 5, "stop_loss": 49000.0,
    }
    data_store = MagicMock()
    data_store.insert_signal.return_value = 10

    result = handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=risk_manager, mode="paper",
    )

    assert result.action == "open_skipped"


# ============================================================
# 反向信号检测
# ============================================================

def test_handle_open_signal_detects_reverse_position():
    """持有 buy 收到 sell → place_order order_type="close"。"""
    signal = make_signal(signal_type="sell")
    paper_engine = MagicMock()
    paper_engine.balance = 100000.0
    existing_pos = MagicMock()
    existing_pos.side = "buy"  # 持多单
    paper_engine.positions.get.return_value = existing_pos
    paper_engine.place_order.return_value = MagicMock(order_id="PAPER-2")

    risk_manager = MagicMock()
    risk_manager.check_and_adjust.return_value = {
        "approved": True, "leverage": 3, "stop_loss": 51000.0,
    }
    data_store = MagicMock()
    data_store.insert_signal.return_value = 10

    handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=risk_manager, mode="paper",
    )

    call_kwargs = paper_engine.place_order.call_args.kwargs
    assert call_kwargs["order_type"] == "close"


def test_handle_close_passes_metadata_reason_as_trigger():
    """close 信号的 metadata.reason 作为 trigger_reason 传给引擎。"""
    signal = make_signal(action="close", price=51000.0,
                         metadata={"reason": "ema_cross_down"})
    paper_engine = MagicMock()
    paper_engine.positions.get.return_value = MagicMock()
    paper_engine.close_position.return_value = 200.0
    data_store = MagicMock()
    data_store.insert_signal.return_value = 5

    handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=MagicMock(), mode="paper",
    )

    call_kwargs = paper_engine.close_position.call_args.kwargs
    assert call_kwargs["trigger_reason"] == "ema_cross_down"


def test_handle_close_defaults_trigger_to_signal_close():
    """close 信号无 metadata.reason 时默认 trigger_reason='signal_close'。"""
    signal = make_signal(action="close")
    paper_engine = MagicMock()
    paper_engine.positions.get.return_value = MagicMock()
    paper_engine.close_position.return_value = 100.0
    data_store = MagicMock()
    data_store.insert_signal.return_value = 1

    handle_signal(
        signal=signal, paper_engine=paper_engine, data_store=data_store,
        risk_manager=MagicMock(), mode="paper",
    )

    call_kwargs = paper_engine.close_position.call_args.kwargs
    assert call_kwargs["trigger_reason"] == "signal_close"
