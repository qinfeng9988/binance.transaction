"""BacktestDataStore 单元测试。

验证：
- 写入方法自动注入 run_id
- 查询方法自动按 run_id 过滤
- 多 run_id 共存时数据互不污染
- positions 隐式唯一性正确处理（update_position 只更新当前 run_id 的仓位）
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, call

from quant_trader.core.data_store import (
    PositionRecord, OrderRecord, SignalRecord,
    EntryRecord, ClosureRecord,
)


def create_backtest_data_store(run_id: str):
    """创建 BacktestDataStore 实例，模拟 DB 连接（不实际连库）。

    返回 (store, mock_cursor) 以便断言 SQL 语句。
    """
    from quant_trader.core.backtest_data_store import BacktestDataStore

    store = BacktestDataStore(
        host="localhost", port=5432, dbname="db_backtest",
        user="postgres", password="",
        run_id=run_id,
    )

    # 替换 _get_conn 返回 mock
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    store._get_conn = MagicMock(return_value=mock_conn)

    return store, mock_cursor


# ============================================================
# open_position: 写入时注入 run_id
# ============================================================

def test_open_position_injects_run_id():
    """open_position INSERT 语句必须包含 run_id 字段。"""
    store, cur = create_backtest_data_store("bt_test_001")
    cur.fetchone.return_value = [42]

    pos = PositionRecord(
        symbol="BTC/USDT", side="buy", entry_price=50000.0, quantity=0.1,
        leverage=5, entry_time=datetime.now(tz=timezone.utc),
        stop_loss=49000.0, status="open",
    )

    pid = store.open_position(pos, total_fee=2.0)

    assert pid == 42
    # 验证 SQL 包含 run_id 列
    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql
    # 验证参数中包含 run_id 值
    params = cur.execute.call_args[0][1]
    assert "bt_test_001" in params


def test_open_position_default_run_id_in_total_fee_path():
    """open_position 即使不传 total_fee，run_id 仍被注入。"""
    store, cur = create_backtest_data_store("bt_test_002")
    cur.fetchone.return_value = [7]

    pos = PositionRecord(
        symbol="ETH/USDT", side="sell", entry_price=3000.0, quantity=1.0,
        leverage=3, entry_time=datetime.now(tz=timezone.utc),
    )

    store.open_position(pos)

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql


# ============================================================
# update_position: WHERE 加 run_id 过滤
# ============================================================

def test_update_position_filters_by_run_id():
    """update_position WHERE 子句必须含 run_id，避免多 run_id 污染。"""
    store, cur = create_backtest_data_store("bt_test_001")

    store.update_position(
        symbol="BTC/USDT", quantity=0.2, entry_price=51000.0,
        stop_loss=49500.0,
    )

    sql = cur.execute.call_args[0][0]
    assert "WHERE symbol = %s AND status = 'open'" not in sql or "run_id" in sql
    # 必须包含 run_id 过滤
    assert "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_test_001" in params


def test_update_position_all_optional_fields_keep_run_id():
    """update_position 含可选字段时仍带 run_id。"""
    store, cur = create_backtest_data_store("bt_x")

    store.update_position(
        symbol="BTC/USDT", quantity=0.2, entry_price=51000.0,
        stop_loss=49500.0, closed_quantity=0.05, close_count=1,
        pnl=10.0, total_fee=0.5, partial_tp_triggered="2.0R",
    )

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql


# ============================================================
# get_open_positions / get_position_count: 按 run_id 过滤
# ============================================================

def test_get_open_positions_filters_by_run_id():
    """get_open_positions 只返回当前 run_id 的持仓。"""
    store, cur = create_backtest_data_store("bt_a")
    cur.fetchall.return_value = []  # 简化：无数据

    store.get_open_positions()

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_a" in params


def test_get_position_count_filters_by_run_id():
    """get_position_count 只统计当前 run_id。"""
    store, cur = create_backtest_data_store("bt_count")
    cur.fetchone.return_value = [3]

    count = store.get_position_count()

    assert count == 3
    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql


# ============================================================
# insert_order / insert_signal: 注入 run_id
# ============================================================

def test_insert_order_injects_run_id():
    store, cur = create_backtest_data_store("bt_order")
    cur.fetchone.return_value = [1]

    order = OrderRecord(
        symbol="BTC/USDT", price=50000.0, quantity=0.1, leverage=5,
        timestamp=datetime.now(tz=timezone.utc), mode="paper",
        fee=2.0, status="filled", order_id="PAPER-1",
    )

    store.insert_order(order)

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_order" in params


def test_insert_signal_injects_run_id():
    store, cur = create_backtest_data_store("bt_sig")
    cur.fetchone.return_value = [10]

    sig = SignalRecord(
        strategy_name="sr_btc", symbol="BTC/USDT", timeframe="5m",
        signal_type="buy", price=50000.0, stop_loss=49000.0,
        timestamp=datetime.now(tz=timezone.utc), leverage=5,
    )

    store.insert_signal(sig)

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_sig" in params


# ============================================================
# insert_entry / insert_closure: 注入 run_id
# ============================================================

def test_insert_entry_injects_run_id():
    store, cur = create_backtest_data_store("bt_entry")
    cur.fetchone.return_value = [100]

    entry = EntryRecord(
        position_id=1, seq=1, orig_qty=0.1, remaining_qty=0.1,
        entry_price=50000.0, entry_time=datetime.now(tz=timezone.utc),
        entry_fee=2.0, order_id="PAPER-1",
        signal_id=5, status="open",
    )

    store.insert_entry(entry)

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_entry" in params


def test_insert_closure_injects_run_id():
    store, cur = create_backtest_data_store("bt_closure")
    cur.fetchone.return_value = [200]

    closure = ClosureRecord(
        entry_id=100, close_qty=0.05, entry_price=50000.0,
        exit_price=51000.0, pnl=50.0, fee=1.0,
        reason="partial_close", close_time=datetime.now(tz=timezone.utc),
        order_id="PAPER-2", signal_id=5,
    )

    store.insert_closure(closure)

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_closure" in params


# ============================================================
# filter_stats: WHERE 含 run_id
# ============================================================

def test_upsert_filter_stats_contains_run_id():
    store, cur = create_backtest_data_store("bt_fs")

    store.upsert_filter_stats(
        strategy_name="sr_btc", symbol="BTC/USDT", timeframe="5m",
        direction="long", stats={"distance": 5, "adx": 2},
    )

    # 至少一次 UPDATE 或 INSERT 包含 run_id
    all_sql = [c.args[0] for c in cur.execute.call_args_list]
    assert any("run_id" in sql for sql in all_sql)


def test_get_filter_stats_filters_by_run_id():
    store, cur = create_backtest_data_store("bt_gfs")
    cur.fetchall.return_value = []
    cur.description = []

    store.get_filter_stats()

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql


# ============================================================
# run_id 属性暴露
# ============================================================

def test_backtest_data_store_exposes_run_id():
    """BacktestDataStore 通过 run_id 属性暴露当前值。"""
    from quant_trader.core.backtest_data_store import BacktestDataStore

    store = BacktestDataStore(
        host="localhost", port=5432, dbname="db_backtest",
        user="postgres", password="",
        run_id="bt_attr_check",
    )

    assert store.run_id == "bt_attr_check"
