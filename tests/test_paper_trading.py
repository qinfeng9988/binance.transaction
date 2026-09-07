"""PaperTradingEngine 单元测试。

验证：
- place_order 开仓的余额扣减
- 加仓达到上限被忽略时，保证金和手续费必须退还（bug #1 回归测试）
- 被忽略的订单不写入数据库（不产生幽灵订单行）
"""

import pytest
from datetime import datetime, timezone

from quant_trader.exchange.paper_trading import PaperTradingEngine


class FakeDataStore:
    """记录 insert_order / update_position 调用的假数据层。"""

    def __init__(self):
        self.inserted_orders = []
        self.updated_stops = []

    def insert_order(self, record) -> int:
        self.inserted_orders.append(record)
        return len(self.inserted_orders)

    def open_position(self, position, total_fee: float = 0.0) -> int:
        return 1

    def update_position(self, *args, **kwargs):
        self.updated_stops.append(kwargs.get("stop_loss"))

    def insert_entry(self, entry) -> int:
        return 1

    def get_open_positions(self):
        return []

    def get_position_count(self) -> int:
        return 0


def test_open_order_deducts_margin_and_fee():
    """开仓扣减保证金 + 手续费。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    margin = 50000.0 * 0.1 / 5          # 1000
    fee = 50000.0 * 0.1 * 0.0004        # 2.0
    assert eng.balance == pytest.approx(100000.0 - margin - fee)


def test_ignored_add_refunds_margin_and_fee():
    """加仓达到 max_additions 上限被忽略 → 余额必须保持不变。

    回归背景：修复前 place_order 先扣保证金+手续费，_update_position 返回
    "ignored" 后不在退款分支内，导致余额凭空减少（每次泄漏约 10% 仓位的
    保证金+手续费），且幽灵订单以负数量写入 orders 表。
    """
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    balance_after_open = eng.balance

    order = eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                            leverage=5, stop_loss=49000.0,
                            metadata={"max_additions": 0})

    assert order is not None
    assert order.order_type == "ignored"
    # 余额不受被忽略订单影响
    assert eng.balance == pytest.approx(balance_after_open)
    # 持仓数量不变
    assert eng.positions["BTC/USDT"].quantity == pytest.approx(0.1)


def test_ignored_add_not_persisted_to_db():
    """被忽略的加仓单不写入 orders 表（不产生幽灵负数量订单行）。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    ds = FakeDataStore()

    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0, data_store=ds)
    assert len(ds.inserted_orders) == 1

    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0, data_store=ds,
                    metadata={"max_additions": 0})

    # 仍只有开仓那一笔订单
    assert len(ds.inserted_orders) == 1


# ============================================================
# intrabar 止损检查（回测用，bug #7 回归测试）
# ============================================================

def test_intrabar_stop_triggers_on_low_pierce_long():
    """持多单，bar 盘中 low 击穿止损但收盘收回 → 止损应在盘中触发，成交价为止损价。

    回归背景：修复前回测只用收盘价判断止损，盘中插针击穿但收盘收回的行情
    不触发止损，回测系统性偏乐观。
    """
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    # bar: 盘中最低 48800 击穿止损 49000，收盘 49800 收回
    eng.update_prices("BTC/USDT", 49800.0, None,
                      bar_open=50000.0, bar_high=50200.0, bar_low=48800.0)

    assert "BTC/USDT" not in eng.positions
    trade = eng.trades_closed[-1]
    assert trade["exit_price"] == pytest.approx(49000.0)  # 止损价成交，非收盘价


def test_gap_open_below_stop_fills_at_open_long():
    """持多单，开盘即跳空低于止损 → 以更差的开盘价成交。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    eng.update_prices("BTC/USDT", 48500.0, None,
                      bar_open=48000.0, bar_high=48600.0, bar_low=47800.0)

    assert "BTC/USDT" not in eng.positions
    trade = eng.trades_closed[-1]
    assert trade["exit_price"] == pytest.approx(48000.0)  # 跳空开盘价，非止损价


def test_intrabar_stop_not_triggered_when_low_above_stop():
    """盘中 low 未触及止损 → 持仓保留。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    eng.update_prices("BTC/USDT", 49800.0, None,
                      bar_open=50000.0, bar_high=50200.0, bar_low=49100.0)

    assert "BTC/USDT" in eng.positions


def test_intrabar_stop_triggers_on_high_pierce_short():
    """持空单，bar 盘中 high 击穿止损 → 以止损价成交。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "sell", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=51000.0)

    eng.update_prices("BTC/USDT", 50500.0, None,
                      bar_open=50000.0, bar_high=51200.0, bar_low=49900.0)

    assert "BTC/USDT" not in eng.positions
    trade = eng.trades_closed[-1]
    assert trade["exit_price"] == pytest.approx(51000.0)


def test_close_only_check_still_works_without_bar_data():
    """不传 bar OHLC（实盘逐笔场景）→ 保持原收盘/现价判断行为。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    # 现价跌破止损 → 触发，以现价成交
    eng.update_prices("BTC/USDT", 48800.0, None)

    assert "BTC/USDT" not in eng.positions
    trade = eng.trades_closed[-1]
    assert trade["exit_price"] == pytest.approx(48800.0)


# ============================================================
# funding 结算（bug #8 回归测试）
# ============================================================

def test_apply_funding_long_pays_positive_rate():
    """持多单 + 正 funding → 多头付费，余额减少。

    notional = 50000 × 0.1 = 5000，rate 0.01% → payment = 0.5
    """
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    balance_before = eng.balance

    delta = eng.apply_funding("BTC/USDT", 0.01)

    assert delta == pytest.approx(-0.5)   # 余额变动（负 = 账户支出）
    assert eng.balance == pytest.approx(balance_before - 0.5)


def test_apply_funding_short_receives_positive_rate():
    """持空单 + 正 funding → 空头收费，余额增加。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "sell", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=51000.0)
    balance_before = eng.balance

    delta = eng.apply_funding("BTC/USDT", 0.01)

    assert delta == pytest.approx(0.5)
    assert eng.balance == pytest.approx(balance_before + 0.5)


def test_apply_funding_negative_rate_reversed():
    """负 funding → 多头收费、空头付费。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    balance_before = eng.balance

    delta = eng.apply_funding("BTC/USDT", -0.02)

    assert delta == pytest.approx(1.0)
    assert eng.balance == pytest.approx(balance_before + 1.0)


def test_apply_funding_without_position_returns_none():
    """无持仓时 apply_funding 不做任何事。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    assert eng.apply_funding("BTC/USDT", 0.01) is None


def test_summary_reports_total_funding_paid():
    """账户概览包含累计 funding 净支出（正数 = 账户净付出）。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    eng.apply_funding("BTC/USDT", 0.01)   # 付 0.5
    eng.apply_funding("BTC/USDT", -0.02)  # 收 1.0

    summary = eng.get_summary()
    assert summary["total_funding_paid"] == pytest.approx(0.5 - 1.0)  # 净收 0.5 → 负值


# ============================================================
# 平仓后通知风控（record_loss / record_win 接线）
# ============================================================

class RecordingRisk:
    """记录 record_loss / record_win 调用的假风控。"""

    def __init__(self):
        self.losses = []
        self.wins = 0

    def record_loss(self, loss_pct: float):
        self.losses.append(loss_pct)

    def record_win(self):
        self.wins += 1


def test_stop_loss_close_records_loss_pct_of_margin():
    """止损平仓 → record_loss（亏损占保证金百分比）。

    多 0.1@50000 L5，现价 48000 触发：
    net_pnl = (48000-50000)×0.1 − 48000×0.1×0.0004 = −200 − 1.92 = −201.92
    margin = 50000×0.1/5 = 1000 → loss_pct = 20.192
    """
    risk = RecordingRisk()
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap",
                             risk_manager=risk)
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    eng.update_prices("BTC/USDT", 48000.0)

    assert risk.losses == [pytest.approx(20.192)]
    assert risk.wins == 0


def test_profitable_close_records_win():
    """盈利平仓 → record_win。"""
    risk = RecordingRisk()
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap",
                             risk_manager=risk)
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    eng.close_position("BTC/USDT", 51000.0)

    assert risk.wins == 1
    assert risk.losses == []


def test_partial_close_records_event():
    """部分平仓也计入风控事件（盈利部分 → record_win）。"""
    risk = RecordingRisk()
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap",
                             risk_manager=risk)
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    eng.partial_close_position("BTC/USDT", 51000.0, 0.5)

    assert risk.wins == 1


def test_engine_without_risk_manager_still_closes():
    """未传 risk_manager（默认 None）→ 平仓正常，不报错。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    net_pnl = eng.close_position("BTC/USDT", 51000.0)

    assert net_pnl is not None


# ============================================================
# 重启恢复（restore_from_db 余额重建）
# ============================================================

def test_restore_from_db_deducts_entry_fees():
    """重启恢复余额必须扣除入场手续费。

    回归背景：DB positions.pnl = 实现盈亏（只扣平仓费），入场费在开仓时从
    余额扣除且从不回流；restore 若只加回 pnl，每次重启余额虚增 Σ入场费。
    场景：一笔已平仓 pnl=100（total_fee=6，其中入场费 2），初始 100000
    → 恢复余额 = 100000 + 100 − 2 = 100098（而非 100100）。
    """
    from unittest.mock import MagicMock

    entry_time = datetime(2026, 8, 1, tzinfo=timezone.utc)
    exit_time = datetime(2026, 8, 2, tzinfo=timezone.utc)
    closed_row = (1, "BTC/USDT", "buy", 50000.0, 0.1, 5, entry_time,
                  51000.0, exit_time, 100.0, 1, 6.0)

    ds = MagicMock()
    conn = MagicMock()
    cursor = MagicMock()
    ds._get_conn.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cursor
    # 依次返回：1) orders 表最大 id  2) 已平仓 positions 行
    cursor.fetchall.side_effect = [[(7,)], [closed_row]]
    ds.get_open_positions.return_value = []
    ds.get_total_entry_fees.return_value = 2.0

    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.restore_from_db(ds)

    assert eng.balance == pytest.approx(100000.0 + 100.0 - 2.0)


# ============================================================
# 加仓与止损的交互（P2 bug 回归测试）
# ============================================================

def test_add_order_cannot_loosen_trailed_stop_long():
    """加仓单带更松的止损 → 不能把已上移的跟踪止损改回去，且内存与 DB 一致。

    回归背景：修复前加仓分支无条件把新 stop_loss 写入 DB（内存不变），
    重启后恢复出更松的止损；两处不一致且丢失跟踪止损。
    """
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    ds = FakeDataStore()
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0, data_store=ds)
    eng.update_stop_loss("BTC/USDT", 49800.0)  # 跟踪止损已上移

    eng.place_order("BTC/USDT", "buy", amount=0.1, price=49500.0,
                    leverage=5, stop_loss=48000.0,  # 加仓信号带更松的止损
                    data_store=ds, metadata={"max_additions": 1})

    pos = eng.positions["BTC/USDT"]
    assert pos.stop_loss == pytest.approx(49800.0)      # 内存保留跟踪止损
    assert ds.updated_stops[-1] == pytest.approx(49800.0)  # DB 同样不被改松


def test_add_order_can_tighten_stop_long():
    """加仓单带更紧（更高）的止损 → 允许上移。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)

    eng.place_order("BTC/USDT", "buy", amount=0.1, price=49500.0,
                    leverage=5, stop_loss=49500.0,
                    metadata={"max_additions": 1})

    assert eng.positions["BTC/USDT"].stop_loss == pytest.approx(49500.0)


def test_add_order_can_set_stop_when_current_is_zero():
    """原仓位无止损（0）→ 加仓可以设置止损。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=0.0)

    eng.place_order("BTC/USDT", "buy", amount=0.1, price=49500.0,
                    leverage=5, stop_loss=48000.0,
                    metadata={"max_additions": 1})

    assert eng.positions["BTC/USDT"].stop_loss == pytest.approx(48000.0)


def test_add_order_short_cannot_loosen_stop():
    """空单加仓带更高（更松）的止损 → 保留原止损。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "sell", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=51000.0)
    eng.update_stop_loss("BTC/USDT", 50500.0)  # 跟踪止损已下移

    eng.place_order("BTC/USDT", "sell", amount=0.1, price=50500.0,
                    leverage=5, stop_loss=52000.0,
                    metadata={"max_additions": 1})

    assert eng.positions["BTC/USDT"].stop_loss == pytest.approx(50500.0)


# ============================================================
# 混合杠杆加仓的保证金守恒（对账发现的余额泄漏）
# ============================================================

def test_mixed_leverage_add_margin_conserved_on_full_close():
    """加仓杠杆 ≠ 持仓杠杆时，平仓返还的保证金必须等于逐笔扣掉的总额。

    回归背景：保证金扣减按每笔订单自身杠杆（p×q/L_order），但返还按
    持仓首笔杠杆（avg×qty/L_position）。低杠杆加仓单（FR 降杠杆 ×0.6 /
    震荡降仓 ×0.5 会产生 1x/2x 加仓单）挂到高杠杆持仓上，返还额系统性
    少于扣减额——一次 SOL 5m 回测凭空蒸发 3176 USDT。
    场景：开仓 0.1@50000 L5（保证金 1000），加仓 0.1@50000 L1（保证金 5000），
    原价全平（raw pnl=0，平仓费 50000×0.2×0.0004=4）。
    修复前：返还 avg×0.2/5 = 2000 → 凭空少 4000。
    """
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=1, stop_loss=49000.0,
                    metadata={"max_additions": 1})
    balance_before_close = eng.balance

    eng.close_position("BTC/USDT", 50000.0)

    # 保证金全额返还 1000 + 5000，扣除平仓费 4
    assert eng.balance == pytest.approx(balance_before_close + 1000.0 + 5000.0 - 4.0)


def test_mixed_leverage_add_margin_conserved_on_partial_close():
    """部分平仓按已占用保证金比例返还（混合杠杆下同样守恒）。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=1, stop_loss=49000.0,
                    metadata={"max_additions": 1})
    balance_before = eng.balance

    # 平掉一半：应返还保证金 6000 × 50% = 3000（raw pnl=0，费 50000×0.1×0.0004=2）
    eng.partial_close_position("BTC/USDT", 50000.0, 0.5)

    assert eng.balance == pytest.approx(balance_before + 3000.0 - 2.0)


def test_total_equity_uses_accumulated_margin():
    """总权益计算使用累计保证金（混合杠杆下不为近似值）。"""
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=1, stop_loss=49000.0,
                    metadata={"max_additions": 1})

    # 未实现盈亏 0 → 权益 = 初始资金 − 手续费，与杠杆无关
    equity = eng.get_total_equity()
    fees = 50000.0 * 0.1 * 0.0004 * 2
    assert equity == pytest.approx(100000.0 - fees)


def test_summary_tracks_entry_fees_separately():
    """summary 分别给出开仓手续费与平仓手续费，且初始+净盈亏可对账期末资金。

    回归背景：报告的 总盈亏 是"扣除平仓费后"的净值，总手续费 只统计平仓费；
    用户拿 总盈亏−总手续费 对账会漏掉开仓费，误以为收益少了近一半。
    """
    eng = PaperTradingEngine(initial_balance=100000.0, default_type="swap")
    eng.place_order("BTC/USDT", "buy", amount=0.1, price=50000.0,
                    leverage=5, stop_loss=49000.0)
    eng.close_position("BTC/USDT", 50000.0)

    summary = eng.get_summary()
    fee = 50000.0 * 0.1 * 0.0004
    assert summary["total_entry_fees"] == pytest.approx(fee)      # 开仓费
    assert summary["total_fees"] == pytest.approx(fee)            # 平仓费（原有口径）
    # 对账：初始 + Σnet(0) − 开仓费 − 平仓费 = 期末
    assert 100000.0 - fee - fee == pytest.approx(summary["balance"])
