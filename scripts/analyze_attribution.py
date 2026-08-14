#!/usr/bin/env python3
"""归因分析脚本。

直连 db_backtest，按 run_id 查询归因指标，支持三种模式：

用法：
    # 查询指定 run_id 的详细归因
    python scripts/analyze_attribution.py --run-id bt_20260814_xxx

    # 查询最近一次回测
    python scripts/analyze_attribution.py --latest

    # 多 run_id 横向对比
    python scripts/analyze_attribution.py --compare bt_a bt_b bt_c

不依赖主进程，SQL 全部通过 BacktestDataStore 调用，单一来源。
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.backtest_data_store import BacktestDataStore


logger = logging.getLogger(__name__)


# ============================================================
# CLI 参数解析
# ============================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="回测归因分析工具")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--run-id", help="指定回测 run_id 查询详细归因")
    mode.add_argument("--latest", action="store_true",
                      help="查询最近一次回测的归因")
    mode.add_argument("--compare", nargs="+", metavar="RUN_ID",
                      help="对比多个 run_id 的归因指标")
    return parser.parse_args(argv)


# ============================================================
# --latest: 查询最近一次 run_id
# ============================================================

def find_latest_run_id(store: BacktestDataStore) -> str | None:
    """从 position_entry_closures 中查询最近的 run_id。

    优先按 close_time 降序取第一条；表为空时返回 None。
    """
    with store._get_conn().cursor() as cur:
        cur.execute(
            """SELECT run_id FROM position_entry_closures
               ORDER BY close_time DESC LIMIT 1"""
        )
        row = cur.fetchone()
    return row[0] if row else None


# ============================================================
# 报告格式化
# ============================================================

def print_attribution_detail(store: BacktestDataStore, run_id: str):
    """打印单个 run_id 的详细归因报告。"""
    print("\n" + "=" * 70)
    print(f"归因详细报告  run_id: {run_id}")
    print("=" * 70)

    # 按事件类型 + 触发原因
    print("\n── 按事件类型 × 触发原因 ──────────────────────────")
    by_reason = store.get_attribution_by_reason()
    if not by_reason:
        print("  (无归因数据)")
    else:
        print(f"  {'reason':<16} {'触发原因':<18} {'笔数':<6} {'总PNL':>12} {'胜率%':>8}")
        print("  " + "-" * 68)
        for row in by_reason:
            trigger = row.get("trigger_reason") or "-"
            print(f"  {row['reason']:<16} {trigger:<18} {row['count']:<6} "
                  f"{float(row['total_pnl']):>+12.2f} {float(row['win_rate']):>7.1f}%")

    # 按入场序号
    print("\n── 按入场序号（seq） ──────────────────────────")
    by_seq = store.get_attribution_by_entry_seq()
    if not by_seq:
        print("  (无归因数据)")
    else:
        print(f"  {'seq':<10} {'笔数':<6} {'总PNL':>12} {'平均PNL':>12} {'胜率%':>8}")
        print("  " + "-" * 56)
        for row in by_seq:
            seq = int(row["seq"])
            label = f"{seq}{' (开仓)' if seq == 1 else ' (加仓)'}"
            print(f"  {label:<10} {row['count']:<6} "
                  f"{float(row['total_pnl']):>+12.2f} "
                  f"{float(row['avg_pnl']):>+12.2f} "
                  f"{float(row['win_rate']):>7.1f}%")

    # 按信号方向（聚合）
    print("\n── 按信号方向（side） ──────────────────────────")
    by_pos = store.get_attribution_by_position()
    if by_pos:
        side_stats: dict[str, dict] = {}
        for row in by_pos:
            side = row.get("side", "?")
            if side not in side_stats:
                side_stats[side] = {"count": 0, "pnl_sum": 0.0,
                                    "wins": 0, "losses": 0}
            side_stats[side]["count"] += 1
            pnl = float(row.get("total_attributed_pnl") or 0)
            side_stats[side]["pnl_sum"] += pnl
            if pnl > 0:
                side_stats[side]["wins"] += 1
            else:
                side_stats[side]["losses"] += 1
        print(f"  {'方向':<6} {'持仓数':<8} {'总PNL':>12} {'胜率%':>8}")
        print("  " + "-" * 40)
        for side, stats in side_stats.items():
            wr = 100.0 * stats["wins"] / stats["count"] if stats["count"] else 0.0
            print(f"  {side:<6} {stats['count']:<8} "
                  f"{stats['pnl_sum']:>+12.2f} {wr:>7.1f}%")
    else:
        print("  (无归因数据)")

    # 按信号明细
    print("\n── 按信号明细（最近 10 条） ──────────────────────────")
    by_signal = store.get_attribution_by_signal()
    if not by_signal:
        print("  (无信号归因)")
    else:
        recent = by_signal[-10:]  # 最近 10 条
        print(f"  {'signal_id':<10} {'方向':<5} {'入场价':>10} {'归因PNL':>12}")
        print("  " + "-" * 44)
        for row in recent:
            sig_id = row.get("signal_id", "?")
            sig_type = row.get("signal_type", "?")
            entry_price = float(row.get("entry_price") or 0)
            pnl = float(row.get("total_attributed_pnl") or 0)
            print(f"  {sig_id:<10} {sig_type:<5} {entry_price:>10.2f} {pnl:>+12.2f}")

    print()


def print_compare_table(rows: list[dict]):
    """打印多 run_id 横向对比表。"""
    print("\n" + "=" * 110)
    print("多 run_id 归因对比")
    print("=" * 110)

    if not rows:
        print("\n  (无对比数据 — 检查 run_id 是否存在)")
        return

    headers = ["run_id", "总PNL", "笔数", "胜率%", "止损PNL", "部分平仓PNL", "全平PNL", "强平PNL"]
    print(f"\n  {headers[0]:<40} {headers[1]:>11} {headers[2]:>6} {headers[3]:>7} "
          f"{headers[4]:>11} {headers[5]:>12} {headers[6]:>11} {headers[7]:>11}")
    print("  " + "-" * 118)
    for row in rows:
        run_id = row.get("run_id", "?")
        rid_short = run_id if len(run_id) <= 40 else run_id[:37] + "..."
        total_pnl = float(row.get("total_pnl") or 0)
        trades = int(row.get("total_trades") or 0)
        win_rate = float(row.get("win_rate") or 0)
        sl_pnl = float(row.get("stop_loss_pnl") or 0)
        pc_pnl = float(row.get("partial_close_pnl") or 0)
        cf_pnl = float(row.get("close_full_pnl") or 0)
        ed_pnl = float(row.get("end_of_data_pnl") or 0)
        print(f"  {rid_short:<40} {total_pnl:>+11.2f} {trades:>6} "
              f"{win_rate:>6.1f}% {sl_pnl:>+11.2f} {pc_pnl:>+12.2f} "
              f"{cf_pnl:>+11.2f} {ed_pnl:>+11.2f}")
    print()


# ============================================================
# 主函数
# ============================================================

def main_logic(args, store: BacktestDataStore) -> int:
    """主逻辑（与 IO 解耦，便于测试）。"""
    if args.latest:
        run_id = find_latest_run_id(store)
        if run_id is None:
            print("未找到任何回测数据，请先运行 scripts/backtest_runner.py")
            return 1
        print(f"最近一次回测 run_id: {run_id}")
        store.run_id = run_id  # 切换到目标 run_id
        print_attribution_detail(store, run_id)
        return 0

    if args.compare:
        rows = store.compare_runs(run_ids=args.compare)
        print_compare_table(rows)
        return 0

    # --run-id 模式
    store.run_id = args.run_id
    print_attribution_detail(store, args.run_id)
    return 0


def main():
    args = parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    config = ConfigLoader()
    bt_cfg = config.load_backtest()

    store = BacktestDataStore(
        host=bt_cfg.db_host, port=bt_cfg.db_port,
        dbname=bt_cfg.db_name, user=bt_cfg.db_user,
        password=bt_cfg.db_password,
        run_id="dummy",  # compare/latest 模式不依赖 run_id
    )
    store.connect()

    try:
        rc = main_logic(args, store)
    finally:
        store.close()

    sys.exit(rc)


if __name__ == "__main__":
    main()
