#!/usr/bin/env python3
"""诊断加仓机制：记录所有加仓机会及其被过滤原因。"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.backtest_range import run_backtest


def analyze_additions(engine, label=""):
    """分析加仓情况。"""
    print(f"\n{'='*60}")
    print(f"加仓诊断: {label}")
    print(f"{'='*60}")

    # 按持仓分组
    positions = {}  # side -> list of trades
    current_side = None
    current_group = []

    for t in engine.trades:
        if current_side != t["side"]:
            if current_group:
                if current_side not in positions:
                    positions[current_side] = []
                positions[current_side].append(current_group)
            current_side = t["side"]
            current_group = [t]
        else:
            current_group.append(t)

    if current_group:
        if current_side not in positions:
            positions[current_side] = []
        positions[current_side].append(current_group)

    for side in ["sell", "buy"]:
        groups = positions.get(side, [])
        print(f"\n--- {side.upper()} 方向持仓 ---")
        print(f"总持仓次数: {len(groups)}")

        single_trade = 0
        multi_trade = 0
        total_additions = 0

        for grp in groups:
            if len(grp) == 1:
                single_trade += 1
            else:
                multi_trade += 1
                total_additions += len(grp) - 1

        print(f"单次平仓即结束: {single_trade}")
        print(f"多次交易(含加仓/部分止盈): {multi_trade}")
        print(f"总加仓/部分止盈次数: {total_additions}")

        if groups:
            print(f"\n逐笔持仓明细:")
            for i, grp in enumerate(groups, 1):
                entry = grp[0]["entry_price"]
                total_pnl = sum(t["net_pnl"] for t in grp)
                first_time = grp[0].get("entry_time", "")
                if first_time:
                    first_time = first_time.strftime("%m-%d %H:%M")
                print(f"  持仓{i}: 入场={entry:.2f}, 交易数={len(grp)}, 总盈亏={total_pnl:+.2f}, 开始={first_time}")
                for t in grp:
                    reason = t["reason"]
                    pnl = t["net_pnl"]
                    print(f"    -> {t['exit_price']:.2f} | {reason:<15} | {pnl:+.2f}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SOL/USDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--start", default="2026-05-11")
    parser.add_argument("--end", default="2026-06-08")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=timezone.utc)

    config = __import__("quant_trader.core.config_loader", fromlist=["ConfigLoader"]).ConfigLoader()
    config.load_strategies()
    params = None
    for s in config.strategies.strategies:
        if args.symbol in s.symbols and args.timeframe in s.timeframes:
            params = s.params
            break

    if not params:
        print(f"未找到 {args.symbol} {args.timeframe} 的策略配置")
        return

    engine = run_backtest(args.symbol, args.timeframe, start, end, params)
    if engine:
        summary = engine.get_summary()
        print(f"\n回测概要: {args.symbol} {args.timeframe} {args.start}~{args.end}")
        print(f"收益率: {summary['total_return_pct']:+.2f}%, 交易数: {summary['total_trades']}")
        analyze_additions(engine)


if __name__ == "__main__":
    asyncio.run(main())
