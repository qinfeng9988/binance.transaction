"""analyze_attribution 脚本单元测试。

验证：
- 命令行参数解析
- --latest 模式：查询最近一次 run_id
- --compare 模式：调用 compare_runs
- --run-id 模式：调用归因方法
- 报告格式化函数输出合理
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from analyze_attribution import (
    parse_args,
    find_latest_run_id,
    print_attribution_detail,
    print_compare_table,
)


# ============================================================
# CLI 参数解析
# ============================================================

def test_parse_args_run_id_mode():
    args = parse_args(["--run-id", "bt_xxx"])
    assert args.run_id == "bt_xxx"
    assert args.latest is False
    assert args.compare is None


def test_parse_args_latest_mode():
    args = parse_args(["--latest"])
    assert args.latest is True
    assert args.run_id is None


def test_parse_args_compare_mode():
    args = parse_args(["--compare", "bt_a", "bt_b", "bt_c"])
    assert args.compare == ["bt_a", "bt_b", "bt_c"]


def test_parse_args_requires_one_mode():
    """三种模式必须选一个。"""
    with pytest.raises(SystemExit):
        parse_args([])  # 无任何参数


# ============================================================
# --latest: 查询最近一次 run_id
# ============================================================

def test_find_latest_run_id_returns_most_recent():
    """从 position_entry_closures 中查找最近的 run_id。"""
    from quant_trader.core.backtest_data_store import BacktestDataStore

    store = MagicMock(spec=BacktestDataStore)
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    store._get_conn.return_value = mock_conn
    mock_cursor.fetchone.return_value = ["bt_20260814_xxx"]

    result = find_latest_run_id(store)

    assert result == "bt_20260814_xxx"
    sql = mock_cursor.execute.call_args[0][0]
    assert "ORDER BY" in sql.upper()
    assert "DESC" in sql.upper()
    assert "LIMIT 1" in sql.upper()


def test_find_latest_run_id_returns_none_when_no_data():
    """无回测数据时返回 None。"""
    store = MagicMock()
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    store._get_conn.return_value = mock_conn
    mock_cursor.fetchone.return_value = [None]

    assert find_latest_run_id(store) is None


# ============================================================
# 报告格式化函数
# ============================================================

def test_print_attribution_detail_handles_empty(capsys):
    """无归因数据时不报错。"""
    store = MagicMock()
    store.get_attribution_by_reason.return_value = []
    store.get_attribution_by_entry_seq.return_value = []
    store.get_attribution_by_position.return_value = []
    store.get_attribution_by_month.return_value = []
    store.get_attribution_by_signal.return_value = []

    print_attribution_detail(store, "bt_empty")

    captured = capsys.readouterr()
    assert "无归因数据" in captured.out or "归因" in captured.out


def test_print_attribution_detail_outputs_reason_seq_direction(capsys):
    """有数据时输出 reason×trigger/seq/direction/月份 四个维度。"""
    store = MagicMock()
    store.get_attribution_by_reason.return_value = [
        {"reason": "CLOSE_FULL", "trigger_reason": "signal_close", "count": 1,
         "total_pnl": 99.49, "win_count": 1, "loss_count": 0, "win_rate": 100.0},
    ]
    store.get_attribution_by_entry_seq.return_value = [
        {"seq": 1, "count": 1, "total_pnl": 99.49, "avg_pnl": 99.49, "win_rate": 100.0},
    ]
    store.get_attribution_by_position.return_value = [
        {"side": "buy", "total_attributed_pnl": 99.49},
    ]
    store.get_attribution_by_month.return_value = [
        {"month": "2026-01", "count": 1, "total_pnl": 99.49,
         "cumulative_pnl": 99.49, "win_rate": 100.0},
    ]
    store.get_attribution_by_signal.return_value = []

    print_attribution_detail(store, "bt_xxx")

    captured = capsys.readouterr()
    assert "CLOSE_FULL" in captured.out
    assert "signal_close" in captured.out
    assert "buy" in captured.out
    assert "2026-01" in captured.out


def test_print_compare_table_outputs_run_id_rows(capsys):
    """compare 表输出每个 run_id 一行。"""
    rows = [
        {"run_id": "bt_a", "total_pnl": 523.40, "total_trades": 47,
         "win_rate": 61.7, "stop_loss_pnl": -312.40,
         "partial_close_pnl": 390.70, "close_full_pnl": 89.10,
         "end_of_data_pnl": -44.00},
        {"run_id": "bt_b", "total_pnl": 312.10, "total_trades": 38,
         "win_rate": 57.9, "stop_loss_pnl": -290.10,
         "partial_close_pnl": 280.50, "close_full_pnl": 50.50,
         "end_of_data_pnl": 0.0},
    ]

    print_compare_table(rows)

    captured = capsys.readouterr()
    assert "bt_a" in captured.out
    assert "bt_b" in captured.out
    assert "523.40" in captured.out or "523" in captured.out
    assert "47" in captured.out


def test_print_compare_table_handles_empty(capsys):
    print_compare_table([])
    captured = capsys.readouterr()
    assert "无" in captured.out or "未找到" in captured.out
