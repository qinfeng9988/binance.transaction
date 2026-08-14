"""BacktestDataStore 归因查询方法单元测试。

验证：
- get_attribution_by_signal/position/time_range 自动加 WHERE run_id 过滤
- get_attribution_by_reason 按 reason 聚合（笔数/总PNL/占比/胜率）
- get_attribution_by_entry_seq 按 entry seq 聚合
- compare_runs 多 run_id 横向对比
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from quant_trader.core.backtest_data_store import BacktestDataStore


def make_store_and_cursor(run_id: str):
    """创建 BacktestDataStore 实例和 mock cursor。"""
    store = BacktestDataStore(
        host="localhost", port=5432, dbname="db_backtest",
        user="postgres", password="",
        run_id=run_id,
    )
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    store._get_conn = MagicMock(return_value=mock_conn)
    return store, mock_cursor


# ============================================================
# get_attribution_by_signal: WHERE 加 pe.run_id
# ============================================================

def test_get_attribution_by_signal_filters_by_run_id():
    store, cur = make_store_and_cursor("bt_sig_attr")
    cur.description = []
    cur.fetchall.return_value = []

    store.get_attribution_by_signal()

    sql = cur.execute.call_args[0][0]
    assert "pe.run_id" in sql or "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_sig_attr" in params


def test_get_attribution_by_signal_with_signal_id_still_filters_run_id():
    store, cur = make_store_and_cursor("bt_a")
    cur.description = []
    cur.fetchall.return_value = []

    store.get_attribution_by_signal(signal_id=42)

    sql = cur.execute.call_args[0][0]
    assert "run_id" in sql
    params = cur.execute.call_args[0][1]
    # 同时含 signal_id 和 run_id
    assert 42 in params
    assert "bt_a" in params


# ============================================================
# get_attribution_by_position: WHERE 加 p.run_id
# ============================================================

def test_get_attribution_by_position_filters_by_run_id():
    store, cur = make_store_and_cursor("bt_pos")
    cur.description = []
    cur.fetchall.return_value = []

    store.get_attribution_by_position()

    sql = cur.execute.call_args[0][0]
    assert "p.run_id" in sql or "run_id" in sql
    params = cur.execute.call_args[0][1]
    assert "bt_pos" in params


# ============================================================
# get_attribution_by_time_range: WHERE 加 ec.run_id
# ============================================================

def test_get_attribution_by_time_range_filters_by_run_id():
    store, cur = make_store_and_cursor("bt_tr")
    cur.description = []
    cur.fetchall.return_value = []

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 6, 30, tzinfo=timezone.utc)
    store.get_attribution_by_time_range(start, end)

    sql = cur.execute.call_args[0][0]
    assert "ec.run_id" in sql or "run_id" in sql


# ============================================================
# get_attribution_by_reason: 按 reason 聚合
# ============================================================

def test_get_attribution_by_reason_returns_aggregated_dict():
    """按 reason 聚合：返回包含 reason/count/total_pnl/win_rate 等字段的列表。"""
    store, cur = make_store_and_cursor("bt_reason")
    cur.description = [
        ("reason",), ("count",), ("total_pnl",),
        ("win_count",), ("loss_count",), ("win_rate",),
    ]
    cur.fetchall.return_value = [
        ("partial_tp_2.0R", 5, 234.50, 5, 0, 100.0),
        ("stop_loss_close", 10, -312.40, 0, 10, 0.0),
    ]

    result = store.get_attribution_by_reason()

    assert len(result) == 2
    assert result[0]["reason"] == "partial_tp_2.0R"
    assert result[0]["count"] == 5
    assert result[0]["total_pnl"] == 234.50
    assert result[0]["win_rate"] == 100.0


def test_get_attribution_by_reason_sql_contains_run_id_and_group_by_reason():
    store, cur = make_store_and_cursor("bt_r2")
    cur.description = []
    cur.fetchall.return_value = []

    store.get_attribution_by_reason()

    sql = cur.execute.call_args[0][0]
    assert "GROUP BY" in sql.upper()
    assert "reason" in sql.lower()
    assert "run_id" in sql


# ============================================================
# get_attribution_by_entry_seq: 按 entry seq 聚合
# ============================================================

def test_get_attribution_by_entry_seq_returns_aggregated_dict():
    """按 seq 聚合：区分首次开仓 vs 加仓的 PNL 贡献。"""
    store, cur = make_store_and_cursor("bt_seq")
    cur.description = [
        ("seq",), ("count",), ("total_pnl",),
        ("avg_pnl",), ("win_rate",),
    ]
    cur.fetchall.return_value = [
        (1, 47, 312.40, 6.64, 65.9),  # 首次开仓
        (2, 18, 211.00, 11.72, 72.2),  # 第一次加仓
    ]

    result = store.get_attribution_by_entry_seq()

    assert len(result) == 2
    assert result[0]["seq"] == 1
    assert result[0]["count"] == 47
    assert result[0]["avg_pnl"] == 6.64
    assert result[1]["seq"] == 2


def test_get_attribution_by_entry_seq_sql_groups_by_seq():
    store, cur = make_store_and_cursor("bt_s2")
    cur.description = []
    cur.fetchall.return_value = []

    store.get_attribution_by_entry_seq()

    sql = cur.execute.call_args[0][0]
    assert "GROUP BY" in sql.upper()
    assert "seq" in sql.lower()
    assert "run_id" in sql


# ============================================================
# compare_runs: 多 run_id 横向对比
# ============================================================

def test_compare_runs_queries_all_runs_aggregated():
    """compare_runs 返回每个 run_id 的归因汇总。"""
    store = BacktestDataStore(
        host="localhost", port=5432, dbname="db_backtest",
        user="postgres", password="",
        run_id="dummy",  # compare_runs 不依赖 self.run_id
    )
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    store._get_conn = MagicMock(return_value=mock_conn)

    mock_cursor.description = [
        ("run_id",), ("total_pnl",), ("total_trades",),
        ("win_rate",), ("stop_loss_pnl",), ("partial_tp_pnl",),
    ]
    mock_cursor.fetchall.return_value = [
        ("bt_a", 523.40, 47, 61.7, -312.40, 390.70),
        ("bt_b", 312.10, 38, 57.9, -290.10, 280.50),
    ]

    result = store.compare_runs(run_ids=["bt_a", "bt_b"])

    assert len(result) == 2
    assert result[0]["run_id"] == "bt_a"
    assert result[0]["total_pnl"] == 523.40
    sql = mock_cursor.execute.call_args[0][0]
    assert "run_id" in sql
    assert "IN" in sql.upper() or "ANY" in sql.upper()


def test_compare_runs_no_run_ids_returns_all():
    """未指定 run_ids 时返回所有 run_id 的汇总。"""
    store = BacktestDataStore(
        host="localhost", port=5432, dbname="db_backtest",
        user="postgres", password="", run_id="dummy",
    )
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    store._get_conn = MagicMock(return_value=mock_conn)
    mock_cursor.description = [("run_id",), ("total_pnl",)]
    mock_cursor.fetchall.return_value = [("bt_a", 100.0)]

    store.compare_runs()

    sql = mock_cursor.execute.call_args[0][0]
    # 不应包含 "IN (" 子句（查询所有）
    assert "IN (" not in sql
    assert "%s" not in sql  # 无参数
