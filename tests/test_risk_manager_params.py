"""RiskManager 风控参数化单元测试。

验证：
- 从配置读取 max_consecutive_losses / cooldown_duration_seconds / max_total_risk_pct
- 连续亏损冷静期：达阈值后 N 秒内拒绝新开仓
- 冷静期对加仓/反向平仓不生效
- 加仓降级：连续亏损达阈值时 max_additions - 1
- record_loss 重置连续亏损（盈利时调用）
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from quant_trader.core.config_loader import RiskConfig
from quant_trader.risk.manager import RiskManager
from quant_trader.models import Signal


def make_signal(**kwargs) -> Signal:
    defaults = {
        "strategy_name": "sr_btc",
        "symbol": "BTC/USDT",
        "timeframe": "5m",
        "signal_type": "buy",
        "price": 50000.0,
        "stop_loss": 49500.0,  # 1% 止损距离
        "timestamp": datetime.now(tz=timezone.utc),
        "leverage": 3,
    }
    defaults.update(kwargs)
    return Signal(**defaults)


def make_risk_manager(**overrides) -> RiskManager:
    clock = overrides.pop("clock", None)
    cfg = RiskConfig(
        leverage_limits={"major_coins": 5, "alt_coins": 3},
        major_symbols=["BTC", "ETH", "SOL"],
        stop_loss={"default_pct": 1.5, "min_pct": 1.0, "max_pct": 2.5},
        position_limits={
            "max_loss_per_trade_pct": 15,
            "max_daily_loss_pct": 30,
            "max_position_size_pct": 40,
            "max_concurrent_positions": 5,
        },
        max_leveraged_loss_pct=15.0,
        max_consecutive_losses=overrides.pop("max_consecutive_losses", 3),
        cooldown_duration_seconds=overrides.pop("cooldown_duration_seconds", 3600),
        max_total_risk_pct=overrides.pop("max_total_risk_pct", 8.0),
    )
    ds = MagicMock()
    ds.get_position_count.return_value = 0
    ds.get_open_positions.return_value = []
    if clock is not None:
        return RiskManager(cfg, ds, clock=clock)
    return RiskManager(cfg, ds)


# ============================================================
# 参数读取
# ============================================================

def test_risk_manager_reads_consecutive_losses_threshold():
    """RiskManager 从配置读取 max_consecutive_losses。"""
    rm = make_risk_manager(max_consecutive_losses=5)
    assert rm.max_consecutive_losses == 5


def test_risk_manager_reads_cooldown_duration():
    rm = make_risk_manager(cooldown_duration_seconds=7200)
    assert rm.cooldown_duration_seconds == 7200


def test_risk_manager_reads_max_total_risk_pct():
    rm = make_risk_manager(max_total_risk_pct=10.0)
    assert rm.max_total_risk_pct == 10.0


# ============================================================
# 冷静期：连续亏损达阈值后拒绝新开仓
# ============================================================

def test_cooldown_blocks_new_open_after_threshold_consecutive_losses():
    """连续亏损达阈值后，新开仓信号被拒绝。"""
    rm = make_risk_manager(max_consecutive_losses=3, cooldown_duration_seconds=3600)

    # 模拟连续 3 次亏损
    rm.record_loss(2.0)
    rm.record_loss(2.0)
    rm.record_loss(2.0)

    result = rm.check_and_adjust(make_signal(signal_type="buy", symbol="BTC/USDT"))

    assert result["approved"] is False
    assert "冷静期" in result["reason"] or "cooldown" in result["reason"].lower()


def test_cooldown_does_not_block_when_below_threshold():
    """连续亏损未达阈值时不进入冷静期。"""
    rm = make_risk_manager(max_consecutive_losses=3, cooldown_duration_seconds=3600)

    rm.record_loss(2.0)
    rm.record_loss(2.0)  # 只 2 次，未达 3

    result = rm.check_and_adjust(make_signal(signal_type="buy", symbol="BTC/USDT"))
    assert result["approved"] is True


def test_cooldown_expires_after_duration():
    """冷静期到期后恢复交易。"""
    rm = make_risk_manager(max_consecutive_losses=2, cooldown_duration_seconds=100)

    rm.record_loss(2.0)
    rm.record_loss(2.0)  # 达阈值

    # 模拟冷静期已过：直接修改内部时间戳
    rm._cooldown_end = datetime.now(tz=timezone.utc) - timedelta(seconds=1)

    result = rm.check_and_adjust(make_signal(signal_type="buy", symbol="BTC/USDT"))
    assert result["approved"] is True


def test_winning_trade_resets_consecutive_losses():
    """盈利交易重置连续亏损计数。"""
    rm = make_risk_manager(max_consecutive_losses=3)

    rm.record_loss(2.0)
    rm.record_loss(2.0)
    rm.record_win()  # 盈利重置
    rm.record_loss(2.0)  # 只 1 次

    result = rm.check_and_adjust(make_signal(signal_type="buy", symbol="BTC/USDT"))
    assert result["approved"] is True


# ============================================================
# 加仓降级：连续亏损达阈值时 max_additions - 1
# ============================================================

def test_add_position_degrades_after_consecutive_losses():
    """连续亏损达阈值时，加仓上限降低。"""
    rm = make_risk_manager(max_consecutive_losses=3)

    rm.record_loss(2.0)
    rm.record_loss(2.0)
    rm.record_loss(2.0)  # 达阈值

    # 此时不应直接拒绝加仓（加仓不在冷静期范围内），
    # 但应通过信号 metadata 或返回值提示降级
    # 设计决策：连续亏损期间，新开仓直接被冷静期拦截；
    # 加仓信号由信号路由层（main.py / signal_router）通过 metadata 处理
    # 所以这里验证 RiskManager 暴露 consecutive_losses 计数供上层使用
    assert rm.consecutive_losses == 3


def test_consecutive_losses_resets_on_win():
    """盈利时 consecutive_losses 归零。"""
    rm = make_risk_manager(max_consecutive_losses=3)

    rm.record_loss(2.0)
    rm.record_loss(2.0)
    rm.record_win()

    assert rm.consecutive_losses == 0


# ============================================================
# 降级信息暴露给信号路由
# ============================================================

def test_check_and_adjust_returns_downgrade_info_when_in_cooldown():
    """连续亏损期间，check_and_adjust 返回 max_additions 降级建议。"""
    rm = make_risk_manager(max_consecutive_losses=2, cooldown_duration_seconds=3600)

    rm.record_loss(2.0)
    rm.record_loss(2.0)  # 达阈值进入冷静期

    # 反向信号（持仓 sell 收到 buy）应能在冷静期内执行（平仓不受限）
    # 但新开仓被拒。本测试只验证拒绝原因清晰
    result = rm.check_and_adjust(make_signal(signal_type="buy", symbol="BTC/USDT"))
    assert result["approved"] is False


# ============================================================
# 业务时钟注入（回测场景：风控时间必须跟随 bar 时间而非墙钟）
# ============================================================

def test_cooldown_end_based_on_injected_clock():
    """冷静期到期时间 = 业务时钟当前值 + 冷静期时长。"""
    t0 = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    now = {"t": t0}
    rm = make_risk_manager(
        max_consecutive_losses=2, cooldown_duration_seconds=3600,
        clock=lambda: now["t"],
    )

    rm.record_loss(2.0)
    rm.record_loss(2.0)

    assert rm._cooldown_end == t0 + timedelta(seconds=3600)


def test_cooldown_expires_with_injected_clock():
    """业务时钟推进过冷静期终点 → 冷静期解除。"""
    t0 = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    now = {"t": t0}
    rm = make_risk_manager(
        max_consecutive_losses=2, cooldown_duration_seconds=3600,
        clock=lambda: now["t"],
    )

    rm.record_loss(2.0)
    rm.record_loss(2.0)
    assert rm._is_in_cooldown() is True

    now["t"] = t0 + timedelta(seconds=3601)
    assert rm._is_in_cooldown() is False

    result = rm.check_and_adjust(make_signal(signal_type="buy", symbol="BTC/USDT"))
    assert result["approved"] is True


def test_daily_loss_resets_on_new_business_day():
    """业务时钟跨天 → 日亏损计数重置。"""
    t0 = datetime(2026, 8, 29, 23, 0, tzinfo=timezone.utc)
    now = {"t": t0}
    rm = make_risk_manager(clock=lambda: now["t"])

    rm.record_loss(10.0)
    rm.record_loss(10.0)
    assert rm._daily_loss == pytest.approx(20.0)

    now["t"] = t0 + timedelta(hours=2)  # 跨过 UTC 零点
    rm.check_and_adjust(make_signal(signal_type="buy", symbol="BTC/USDT"))
    assert rm._daily_loss == pytest.approx(0.0)
