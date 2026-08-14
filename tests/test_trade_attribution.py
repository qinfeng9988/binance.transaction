"""交易归因功能单元测试。

验证 position_entries / position_entry_closures 在各种交易场景下的正确性：
- 开仓 → 止损全平
- 开仓 → 加仓 → 全平
- 开仓 → 加仓 → 部分平仓 → 再平仓
- FIFO 扣减顺序
- 手续费分摊
- 归因盈亏一致性
"""

import pytest
from unittest.mock import MagicMock, call
from datetime import datetime, timezone

from quant_trader.exchange.paper_trading import PaperTradingEngine, MockPosition
from quant_trader.core.data_store import EntryRecord, ClosureRecord


# ============================================================
# 辅助函数
# ============================================================

def create_engine() -> PaperTradingEngine:
    """创建模拟交易引擎（合约模式）。"""
    return PaperTradingEngine(initial_balance=100000.0, default_type="swap")


def create_mock_data_store() -> MagicMock:
    """创建模拟 DataStore，记录 entry/closure 调用。"""
    ds = MagicMock()

    # 模拟 open_position 返回递增的 position_id
    ds._next_position_id = 1
    def mock_open_position(position_record, total_fee=0.0):
        pid = ds._next_position_id
        ds._next_position_id += 1
        return pid
    ds.open_position.side_effect = mock_open_position

    # 模拟 insert_entry 返回递增的 entry_id
    ds._next_entry_id = 1
    _entries_store = []

    def mock_insert_entry(entry_record):
        eid = ds._next_entry_id
        ds._next_entry_id += 1
        entry_record.id = eid
        _entries_store.append(entry_record)
        return eid
    ds.insert_entry.side_effect = mock_insert_entry

    # 模拟 insert_closure 返回递增的 closure_id
    ds._next_closure_id = 1
    def mock_insert_closure(closure_record):
        cid = ds._next_closure_id
        ds._next_closure_id += 1
        return cid
    ds.insert_closure.side_effect = mock_insert_closure

    def mock_get_open_entries(position_id):
        return [e for e in _entries_store
                if e.position_id == position_id and e.status == "open"]
    ds.get_open_entries.side_effect = mock_get_open_entries

    # 模拟 update_entry：更新 _entries 中的记录
    def mock_update_entry(entry_id, remaining_qty, status):
        for e in _entries_store:
            if e.id == entry_id:
                e.remaining_qty = remaining_qty
                e.status = status
                break
    ds.update_entry.side_effect = mock_update_entry

    # 模拟 insert_order（不需要返回值）
    ds.insert_order.return_value = 1

    return ds


# ============================================================
# 场景 1：开仓 → 止损全平
# ============================================================

class TestOpenThenStopLoss:
    """开仓后止损全平：1 entry, 1 closure。"""

    def test_single_entry_and_closure(self):
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓：买入 0.1 BTC @ 60000
        order = engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )

        # 验证：1 条 entry
        assert ds.insert_entry.call_count == 1
        entry = ds.insert_entry.call_args[0][0]
        assert entry.seq == 1
        assert entry.orig_qty == pytest.approx(0.1)
        assert entry.remaining_qty == pytest.approx(0.1)
        assert entry.entry_price == pytest.approx(60000)
        assert entry.signal_id == 101

        # 止损全平 @ 59000
        pnl = engine.close_position(
            "BTC/USDT", exit_price=59000, data_store=ds,
            order_type="stop_loss_close", signal_id=None,
        )

        # 验证：1 条 closure
        assert ds.insert_closure.call_count == 1
        closure = ds.insert_closure.call_args[0][0]
        assert closure.entry_id == 1  # entry #1
        assert closure.close_qty == pytest.approx(0.1)
        assert closure.entry_price == pytest.approx(60000)
        assert closure.exit_price == pytest.approx(59000)
        assert closure.reason == "STOP_LOSS"
        assert closure.trigger_reason == "stop_loss"
        assert closure.signal_id is None  # 止损无信号

        # 验证：归因盈亏 = (59000 - 60000) * 0.1 - 平仓手续费 - 入场手续费
        fee = 59000 * 0.1 * 0.0004  # 平仓手续费
        entry_fee = 60000 * 0.1 * 0.0004  # 入场手续费全额分摊
        expected_pnl = (59000 - 60000) * 0.1 - fee - entry_fee
        assert closure.pnl == pytest.approx(expected_pnl, abs=0.01)
        assert closure.fee == pytest.approx(fee, abs=0.01)

        # 验证：entry 已关闭
        assert ds.update_entry.call_count == 1
        update_call = ds.update_entry.call_args
        assert update_call[0][0] == 1  # entry_id
        assert update_call[0][1] == pytest.approx(0.0)  # remaining_qty
        assert update_call[0][2] == "closed"


# ============================================================
# 场景 2：开仓 → 加仓 → 全平
# ============================================================

class TestOpenAddThenClose:
    """开仓 + 加仓后全平：2 entries, 2 closures。"""

    def test_two_entries_two_closures(self):
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓：0.1 BTC @ 60000 (signal #101)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
            metadata={"max_additions": 1},
        )

        # 加仓：0.1 BTC @ 62000 (signal #102)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=102,
            metadata={"max_additions": 1},
        )

        # 验证：2 条 entries
        assert ds.insert_entry.call_count == 2
        entry1 = ds.insert_entry.call_args_list[0][0][0]
        entry2 = ds.insert_entry.call_args_list[1][0][0]
        assert entry1.seq == 1
        assert entry1.entry_price == pytest.approx(60000)
        assert entry1.signal_id == 101
        assert entry2.seq == 2
        assert entry2.entry_price == pytest.approx(62000)
        assert entry2.signal_id == 102

        # 全平 @ 64000
        pnl = engine.close_position(
            "BTC/USDT", exit_price=64000, data_store=ds,
            order_type="take_profit_close", signal_id=201,
        )

        # 验证：2 条 closures（FIFO：entry1 先平，entry2 后平）
        assert ds.insert_closure.call_count == 2
        c1 = ds.insert_closure.call_args_list[0][0][0]
        c2 = ds.insert_closure.call_args_list[1][0][0]

        # closure #1: entry #1, 0.1@60000→64000
        assert c1.entry_id == 1
        assert c1.close_qty == pytest.approx(0.1)
        assert c1.entry_price == pytest.approx(60000)
        assert c1.exit_price == pytest.approx(64000)

        # closure #2: entry #2, 0.1@62000→64000
        assert c2.entry_id == 2
        assert c2.close_qty == pytest.approx(0.1)
        assert c2.entry_price == pytest.approx(62000)
        assert c2.exit_price == pytest.approx(64000)

        # 验证：归因盈亏之和 = 总盈亏（归因口径扣双端手续费）
        total_fee = 64000 * 0.2 * 0.0004
        entry_fees = 60000 * 0.1 * 0.0004 + 62000 * 0.1 * 0.0004
        total_raw_pnl = (64000 - 60000) * 0.1 + (64000 - 62000) * 0.1
        total_net_pnl = total_raw_pnl - total_fee - entry_fees
        assert (c1.pnl + c2.pnl) == pytest.approx(total_net_pnl, abs=0.01)

        # 验证：手续费分摊之和 = 总手续费
        assert (c1.fee + c2.fee) == pytest.approx(total_fee, abs=0.01)

    def test_stop_loss_attribution(self):
        """加仓后止损：验证不同入场价的归因盈亏差异。"""
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓 0.1 @ 60000 (signal #101)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
            metadata={"max_additions": 1},
        )
        # 加仓 0.1 @ 62000 (signal #102)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=102,
            metadata={"max_additions": 1},
        )

        # 止损 @ 59000
        engine.close_position(
            "BTC/USDT", exit_price=59000, data_store=ds,
            order_type="stop_loss_close", signal_id=None,
        )

        c1 = ds.insert_closure.call_args_list[0][0][0]
        c2 = ds.insert_closure.call_args_list[1][0][0]

        # signal #101 的入场价更低，止损亏损更小
        # signal #102 的入场价更高，止损亏损更大
        assert c1.pnl > c2.pnl  # entry1@60000 亏得少，entry2@62000 亏得多

        # 验证具体数值（归因 pnl 扣平仓手续费分摊 + 入场手续费全额分摊）
        total_fee = 59000 * 0.2 * 0.0004
        fee_per_entry = total_fee / 2  # 数量相同，手续费均摊
        entry_fee1 = 60000 * 0.1 * 0.0004
        entry_fee2 = 62000 * 0.1 * 0.0004
        expected_pnl1 = (59000 - 60000) * 0.1 - fee_per_entry - entry_fee1
        expected_pnl2 = (59000 - 62000) * 0.1 - fee_per_entry - entry_fee2
        assert c1.pnl == pytest.approx(expected_pnl1, abs=0.01)
        assert c2.pnl == pytest.approx(expected_pnl2, abs=0.01)


# ============================================================
# 场景 3：开仓 → 加仓 → 部分平仓 → 再平仓
# ============================================================

class TestPartialCloseAcrossEntries:
    """部分平仓跨越多条 entry，验证 FIFO 扣减和多次 closure。"""

    def test_partial_close_crosses_entries(self):
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓 0.1 @ 60000 (signal #101)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
            metadata={"max_additions": 1},
        )
        # 加仓 0.1 @ 62000 (signal #102)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=102,
            metadata={"max_additions": 1},
        )

        # 部分平仓 0.12 BTC @ 64000（60% 仓位）
        # FIFO: 先扣 entry#1 全部 0.1，再扣 entry#2 的 0.02
        net_pnl = engine.partial_close_position(
            "BTC/USDT", close_price=64000, close_ratio=0.6,
            data_store=ds, signal_id=201, partial_tp_level="2.0R",
        )

        # 验证：2 条 closures
        assert ds.insert_closure.call_count == 2
        c1 = ds.insert_closure.call_args_list[0][0][0]
        c2 = ds.insert_closure.call_args_list[1][0][0]

        # closure #1: entry#1 全部扣完 0.1@60000→64000
        assert c1.entry_id == 1
        assert c1.close_qty == pytest.approx(0.1)
        assert c1.entry_price == pytest.approx(60000)
        assert c1.exit_price == pytest.approx(64000)
        assert c1.reason == "CLOSE_PARTIAL"
        assert c1.trigger_reason == "2.0R"

        # closure #2: entry#2 部分扣 0.02@62000→64000
        assert c2.entry_id == 2
        assert c2.close_qty == pytest.approx(0.02)
        assert c2.entry_price == pytest.approx(62000)
        assert c2.exit_price == pytest.approx(64000)
        assert c2.reason == "CLOSE_PARTIAL"
        assert c2.trigger_reason == "2.0R"

        # 验证：entry#1 已关闭，entry#2 仍 open
        update_calls = ds.update_entry.call_args_list
        # entry#1: remaining=0, status='closed'
        assert update_calls[0][0][0] == 1  # entry_id
        assert update_calls[0][0][1] == pytest.approx(0.0)  # remaining_qty
        assert update_calls[0][0][2] == "closed"
        # entry#2: remaining=0.08, status='open'
        assert update_calls[1][0][0] == 2  # entry_id
        assert update_calls[1][0][1] == pytest.approx(0.08)  # remaining_qty
        assert update_calls[1][0][2] == "open"

        # 验证：手续费分摊
        total_fee = 64000 * 0.12 * 0.0004
        assert (c1.fee + c2.fee) == pytest.approx(total_fee, abs=0.01)
        # 按数量比例分摊
        assert c1.fee == pytest.approx(total_fee * (0.1 / 0.12), abs=0.01)
        assert c2.fee == pytest.approx(total_fee * (0.02 / 0.12), abs=0.01)

    def test_partial_close_then_stop_loss(self):
        """部分平仓后再止损：验证同一 entry 有多条 closure。"""
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓 0.1 @ 60000 (signal #101)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
            metadata={"max_additions": 1},
        )
        # 加仓 0.1 @ 62000 (signal #102)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=102,
            metadata={"max_additions": 1},
        )

        # 部分平仓 0.12 @ 64000
        engine.partial_close_position(
            "BTC/USDT", close_price=64000, close_ratio=0.6,
            data_store=ds, signal_id=201,
        )

        # 剩余 0.08 BTC，止损 @ 58000
        engine.close_position(
            "BTC/USDT", exit_price=58000, data_store=ds,
            order_type="stop_loss_close", signal_id=None,
        )

        # 验证：共 3 条 closures（2 来自部分平仓，1 来自止损）
        assert ds.insert_closure.call_count == 3
        c3 = ds.insert_closure.call_args_list[2][0][0]

        # 第 3 条 closure: entry#2 剩余 0.08@62000→58000
        assert c3.entry_id == 2  # 仍是 entry#2
        assert c3.close_qty == pytest.approx(0.08)
        assert c3.entry_price == pytest.approx(62000)
        assert c3.exit_price == pytest.approx(58000)
        assert c3.reason == "STOP_LOSS"
        assert c3.trigger_reason == "stop_loss"
        assert c3.signal_id is None

        # 验证：entry#2 的归因盈亏 = 部分平仓盈亏 + 止损盈亏（归因口径扣双端手续费）
        c2 = ds.insert_closure.call_args_list[1][0][0]
        entry2_total_pnl = c2.pnl + c3.pnl
        # entry#2 总共 0.1@62000，部分平 0.02@64000 + 止损 0.08@58000
        expected = (64000 - 62000) * 0.02 + (58000 - 62000) * 0.08
        # 减去手续费（平仓分摊）+ entry#2 全部入场手续费（0.02+0.08=0.1 全部平完）
        total_fee_entry2 = c2.fee + c3.fee
        entry2_entry_fee = 62000 * 0.1 * 0.0004
        expected_net = expected - total_fee_entry2 - entry2_entry_fee
        assert entry2_total_pnl == pytest.approx(expected_net, abs=0.01)


# ============================================================
# 场景 4：FIFO 顺序验证
# ============================================================

class TestFIFOOrder:
    """验证 FIFO 扣减顺序：先开的仓先平。"""

    def test_three_entries_fifo(self):
        """3 次加仓后部分平仓，验证 FIFO 顺序。"""
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓 0.1 @ 60000 (signal #101)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
            metadata={"max_additions": 2},
        )
        # 加仓 0.1 @ 62000 (signal #102)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=102,
            metadata={"max_additions": 2},
        )
        # 加仓 0.1 @ 64000 (signal #103)
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=64000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=103,
            metadata={"max_additions": 2},
        )

        # 部分平仓 0.15 @ 65000
        # FIFO: entry#1 0.1 (全扣) → entry#2 0.05 (部分扣)
        engine.partial_close_position(
            "BTC/USDT", close_price=65000, close_ratio=0.5,
            data_store=ds, signal_id=201,
        )

        assert ds.insert_closure.call_count == 2
        c1 = ds.insert_closure.call_args_list[0][0][0]
        c2 = ds.insert_closure.call_args_list[1][0][0]

        # 先扣 entry#1（最早入场）
        assert c1.entry_id == 1
        assert c1.close_qty == pytest.approx(0.1)
        assert c1.entry_price == pytest.approx(60000)

        # 再扣 entry#2（第二入场）
        assert c2.entry_id == 2
        assert c2.close_qty == pytest.approx(0.05)
        assert c2.entry_price == pytest.approx(62000)

        # entry#3 未被扣减
        # 验证 update_entry 只调用了 2 次（entry#1 closed, entry#2 partial）
        assert ds.update_entry.call_count == 2


# ============================================================
# 场景 5：手续费分摊验证
# ============================================================

class TestFeeAllocation:
    """验证手续费按各 entry 被扣减数量比例分摊。"""

    def test_fee_proportional_allocation(self):
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓 0.2 @ 60000
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.2, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
            metadata={"max_additions": 1},
        )
        # 加仓 0.1 @ 62000
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=102,
            metadata={"max_additions": 1},
        )

        # 全平 0.3 @ 64000
        engine.close_position(
            "BTC/USDT", exit_price=64000, data_store=ds,
            order_type="close", signal_id=201,
        )

        c1 = ds.insert_closure.call_args_list[0][0][0]
        c2 = ds.insert_closure.call_args_list[1][0][0]

        total_fee = 64000 * 0.3 * 0.0004

        # entry#1 扣 0.2，占 2/3
        assert c1.fee == pytest.approx(total_fee * (0.2 / 0.3), abs=0.01)
        # entry#2 扣 0.1，占 1/3
        assert c2.fee == pytest.approx(total_fee * (0.1 / 0.3), abs=0.01)
        # 总和 = 总手续费
        assert (c1.fee + c2.fee) == pytest.approx(total_fee, abs=0.01)


# ============================================================
# 场景 6：归因盈亏一致性验证
# ============================================================

class TestPnLConsistency:
    """验证归因盈亏之和 = 实际总盈亏。"""

    def test_total_attribution_equals_realized_pnl(self):
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓 0.1 @ 60000
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
            metadata={"max_additions": 1},
        )
        # 加仓 0.1 @ 62000
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=102,
            metadata={"max_additions": 1},
        )

        # 部分平仓 0.12 @ 64000
        partial_pnl = engine.partial_close_position(
            "BTC/USDT", close_price=64000, close_ratio=0.6,
            data_store=ds, signal_id=201,
        )

        # 止损剩余 0.08 @ 58000
        stop_pnl = engine.close_position(
            "BTC/USDT", exit_price=58000, data_store=ds,
            order_type="stop_loss_close", signal_id=None,
        )

        # 归因盈亏之和
        total_attribution_pnl = sum(
            c[0][0].pnl for c in ds.insert_closure.call_args_list
        )

        # 实际总盈亏（引擎返回值 = 毛利 - 平仓手续费）
        total_realized_pnl = partial_pnl + stop_pnl

        # 一致性恒等式：归因 pnl 额外扣了入场手续费分摊
        # 归因之和 + 入场手续费总和 = 引擎实现盈亏之和（全部平仓时）
        total_entry_fees = 60000 * 0.1 * 0.0004 + 62000 * 0.1 * 0.0004
        assert total_attribution_pnl + total_entry_fees == pytest.approx(
            total_realized_pnl, abs=0.01,
        )

    def test_sell_side_attribution(self):
        """做空场景的归因验证。"""
        engine = create_engine()
        ds = create_mock_data_store()

        # 开空 0.1 @ 60000 (signal #101)
        engine.place_order(
            symbol="BTC/USDT", side="sell", amount=0.1, price=60000,
            leverage=1, stop_loss=61000, data_store=ds, signal_id=101,
            metadata={"max_additions": 1},
        )
        # 加空 0.1 @ 58000 (signal #102)
        engine.place_order(
            symbol="BTC/USDT", side="sell", amount=0.1, price=58000,
            leverage=1, stop_loss=61000, data_store=ds, signal_id=102,
            metadata={"max_additions": 1},
        )

        # 止盈全平 @ 56000
        pnl = engine.close_position(
            "BTC/USDT", exit_price=56000, data_store=ds,
            order_type="take_profit_close", signal_id=201,
        )

        assert ds.insert_closure.call_count == 2
        c1 = ds.insert_closure.call_args_list[0][0][0]
        c2 = ds.insert_closure.call_args_list[1][0][0]

        # 做空盈亏 = (entry_price - exit_price) * qty
        # entry#1: (60000 - 56000) * 0.1 = +400
        # entry#2: (58000 - 56000) * 0.1 = +200
        # 归因 pnl 扣平仓手续费分摊 + 入场手续费全额分摊
        total_fee = 56000 * 0.2 * 0.0004
        entry_fee1 = 60000 * 0.1 * 0.0004
        entry_fee2 = 58000 * 0.1 * 0.0004
        expected_pnl1 = (60000 - 56000) * 0.1 - total_fee * (0.1 / 0.2) - entry_fee1
        expected_pnl2 = (58000 - 56000) * 0.1 - total_fee * (0.1 / 0.2) - entry_fee2

        assert c1.pnl == pytest.approx(expected_pnl1, abs=0.01)
        assert c2.pnl == pytest.approx(expected_pnl2, abs=0.01)

        # 归因之和 + 入场手续费总和 = 引擎返回盈亏（毛利 - 平仓手续费）
        assert (c1.pnl + c2.pnl) + entry_fee1 + entry_fee2 == pytest.approx(pnl, abs=0.01)


# ============================================================
# 场景 7：止损触发验证
# ============================================================

class TestStopLossTrigger:
    """验证 update_prices 触发止损时归因正确。"""

    def test_stop_loss_via_update_prices(self):
        engine = create_engine()
        ds = create_mock_data_store()

        # 开仓 0.1 @ 60000, 止损=59000
        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )

        # 价格跌到止损位
        engine.update_prices("BTC/USDT", 58999, ds)

        # 验证：止损触发了归因
        assert ds.insert_closure.call_count == 1
        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "STOP_LOSS"
        assert closure.trigger_reason == "stop_loss"
        assert closure.signal_id is None
        assert closure.exit_price == pytest.approx(58999)

    def test_stop_loss_not_triggered(self):
        """价格未触及止损，不应产生 closure。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )

        # 价格在止损位之上
        engine.update_prices("BTC/USDT", 59500, ds)

        assert ds.insert_closure.call_count == 0


# ============================================================
# 场景 8：signal_id 传递验证
# ============================================================

class TestSignalIdPropagation:
    """验证 signal_id 在各流程中正确传递。"""

    def test_signal_id_in_entry(self):
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, data_store=ds, signal_id=42,
        )

        entry = ds.insert_entry.call_args[0][0]
        assert entry.signal_id == 42

    def test_no_signal_id_in_entry(self):
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, data_store=ds,
        )

        entry = ds.insert_entry.call_args[0][0]
        assert entry.signal_id is None

    def test_signal_id_in_closure_close(self):
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, data_store=ds, signal_id=101,
        )

        engine.close_position(
            "BTC/USDT", exit_price=64000, data_store=ds,
            order_type="close", signal_id=201,
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.signal_id == 201

    def test_signal_id_in_closure_partial(self):
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, data_store=ds, signal_id=101,
        )

        engine.partial_close_position(
            "BTC/USDT", close_price=64000, close_ratio=0.5,
            data_store=ds, signal_id=202,
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.signal_id == 202


# ============================================================
# 场景 9：entry_seq_counter 恢复验证
# ============================================================

class TestEntrySeqCounter:
    """验证 MockPosition.entry_seq_counter 正确递增。"""

    def test_seq_counter_increments(self):
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, data_store=ds, signal_id=101,
            metadata={"max_additions": 2},
        )
        assert engine.positions["BTC/USDT"].entry_seq_counter == 1

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=62000,
            leverage=1, data_store=ds, signal_id=102,
            metadata={"max_additions": 2},
        )
        assert engine.positions["BTC/USDT"].entry_seq_counter == 2

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=64000,
            leverage=1, data_store=ds, signal_id=103,
            metadata={"max_additions": 2},
        )
        assert engine.positions["BTC/USDT"].entry_seq_counter == 3


# ============================================================
# 场景 10：reason 枚举 + trigger_reason 传播（方案 C）
# ============================================================

class TestReasonEnumAndTrigger:
    """验证 reason 枚举（事件类型）与 trigger_reason（触发原因）分离。"""

    def test_close_signal_maps_to_close_full(self):
        """order_type='close' → reason='CLOSE_FULL'，trigger_reason 透传。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )
        engine.close_position(
            "BTC/USDT", exit_price=61000, data_store=ds,
            order_type="close", signal_id=201,
            trigger_reason="ema_cross_down",
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "CLOSE_FULL"
        assert closure.trigger_reason == "ema_cross_down"

    def test_close_default_trigger_is_signal_close(self):
        """不传 trigger_reason 时默认 'signal_close'。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )
        engine.close_position(
            "BTC/USDT", exit_price=61000, data_store=ds,
            order_type="close", signal_id=201,
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "CLOSE_FULL"
        assert closure.trigger_reason == "signal_close"

    def test_take_profit_close_maps_to_close_full(self):
        """order_type='take_profit_close' → reason='CLOSE_FULL'。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )
        engine.close_position(
            "BTC/USDT", exit_price=62000, data_store=ds,
            order_type="take_profit_close", signal_id=201,
            trigger_reason="take_profit",
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "CLOSE_FULL"
        assert closure.trigger_reason == "take_profit"

    def test_end_of_data_maps_to_end_of_data(self):
        """order_type='end_of_data' → reason='END_OF_DATA'（回测结束强平）。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )
        engine.close_position(
            "BTC/USDT", exit_price=60500, data_store=ds,
            order_type="end_of_data", signal_id=None,
            trigger_reason="end_of_backtest",
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "END_OF_DATA"
        assert closure.trigger_reason == "end_of_backtest"

    def test_reverse_signal_close_gets_reverse_trigger(self):
        """持多仓收到反向卖单 → close，trigger_reason='reverse_signal'。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )
        assert ds.insert_closure.call_count == 0

        # 反向信号：持 buy 收 sell（order_type 提示 close）
        engine.place_order(
            symbol="BTC/USDT", side="sell", amount=0.1, price=61000,
            leverage=1, stop_loss=0, data_store=ds, signal_id=201,
            order_type="close",
        )

        # 反向平仓产生了 closure
        assert ds.insert_closure.call_count == 1
        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "CLOSE_FULL"
        assert closure.trigger_reason == "reverse_signal"

    def test_stop_loss_close_maps_to_stop_loss_enum(self):
        """order_type='stop_loss_close' → reason='STOP_LOSS'。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )
        engine.close_position(
            "BTC/USDT", exit_price=58999, data_store=ds,
            order_type="stop_loss_close", signal_id=None,
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "STOP_LOSS"

    def test_partial_close_without_level_has_null_trigger(self):
        """partial_close 未传 tp_level → trigger_reason=None。"""
        engine = create_engine()
        ds = create_mock_data_store()

        engine.place_order(
            symbol="BTC/USDT", side="buy", amount=0.1, price=60000,
            leverage=1, stop_loss=59000, data_store=ds, signal_id=101,
        )
        engine.partial_close_position(
            "BTC/USDT", close_price=61000, close_ratio=0.5,
            data_store=ds, signal_id=201,
        )

        closure = ds.insert_closure.call_args[0][0]
        assert closure.reason == "CLOSE_PARTIAL"
        assert closure.trigger_reason is None
