#!/usr/bin/env python3
"""诊断 strict_bias_mode 对交易的影响。

运行基线回测（strict_bias_mode=false），然后分析哪些交易会被 strict_bias_mode 过滤，
以及这些被过滤的交易的盈亏情况。

用法:
    python3 scripts/diagnose_strict_bias.py --symbol SOL/USDT --timeframe 5m --days 120
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.backtest import run_backtest, BacktestEngine


def analyze_strict_bias_impact(engine: BacktestEngine, adx_min: float = 19.0):
    """分析 strict_bias_mode 如果启用，会过滤掉哪些交易，以及它们的盈亏。"""

    # strict_bias_mode 过滤条件：
    # - parent_trend in ("uptrend", "downtrend")
    # - current_adx >= adx_min
    # - 信号方向与趋势相反
    #   - buy 被过滤: parent_trend="downtrend", bias in ("short_only", "short_primary")
    #   - sell 被过滤: parent_trend="uptrend", bias in ("long_only", "long_primary")

    filtered_trades = []
    kept_trades = []

    for trade in engine.trades:
        side = trade["side"]
        parent_trend = trade.get("parent_trend")
        adx = trade.get("adx")
        bias = trade.get("bias")

        if parent_trend is None or adx is None or bias is None:
            kept_trades.append(trade)
            continue

        would_filter = False
        if parent_trend in ("uptrend", "downtrend") and adx >= adx_min:
            if side == "buy" and bias in ("short_only", "short_primary"):
                would_filter = True
            elif side == "sell" and bias in ("long_only", "long_primary"):
                would_filter = True

        if would_filter:
            filtered_trades.append(trade)
        else:
            kept_trades.append(trade)

    def summarize(trades, label):
        if not trades:
            return f"{label}: 0 笔"
        winning = [t for t in trades if t["net_pnl"] > 0]
        losing = [t for t in trades if t["net_pnl"] <= 0]
        total_pnl = sum(t["net_pnl"] for t in trades)
        win_rate = len(winning) / len(trades) * 100
        avg_pnl = total_pnl / len(trades)
        return (
            f"{label}: {len(trades)} 笔, 总盈亏={total_pnl:+.2f}, 胜率={win_rate:.1f}%, 单笔均={avg_pnl:+.2f}\n"
            f"  盈利 {len(winning)} 笔, 总盈利={sum(t['net_pnl'] for t in winning):+.2f}\n"
            f"  亏损 {len(losing)} 笔, 总亏损={sum(t['net_pnl'] for t in losing):+.2f}"
        )

    print("\n" + "=" * 60)
    print("strict_bias_mode 诊断分析")
    print("=" * 60)
    print(f"ADX 阈值: {adx_min}")
    print(f"总交易数: {len(engine.trades)}")
    print()
    print(summarize(filtered_trades, "会被过滤的交易"))
    print()
    print(summarize(kept_trades, "保留的交易"))
    print()

    # 模拟 strict_bias_mode=true 的收益
    simulated_balance = engine.initial_balance + sum(t["net_pnl"] for t in kept_trades)
    simulated_return = (simulated_balance - engine.initial_balance) / engine.initial_balance * 100
    actual_return = (engine.balance - engine.initial_balance) / engine.initial_balance * 100

    print("-" * 60)
    print(f"实际收益（strict=false）: {actual_return:+.2f}%")
    print(f"模拟收益（strict=true）:  {simulated_return:+.2f}%")
    print(f"差异: {simulated_return - actual_return:+.2f}%")
    print("=" * 60)

    # 按 parent_trend 细分
    print("\n--- 按 parent_trend 细分被过滤的交易 ---")
    for pt in ["uptrend", "downtrend", "ranging"]:
        pt_trades = [t for t in filtered_trades if t.get("parent_trend") == pt]
        if pt_trades:
            total = sum(t["net_pnl"] for t in pt_trades)
            wins = len([t for t in pt_trades if t["net_pnl"] > 0])
            print(f"  {pt}: {len(pt_trades)} 笔, 盈亏={total:+.2f}, 胜率={wins/len(pt_trades)*100:.1f}%")

    # 按 buy/sell 细分
    print("\n--- 按方向细分被过滤的交易 ---")
    for side in ["buy", "sell"]:
        side_trades = [t for t in filtered_trades if t["side"] == side]
        if side_trades:
            total = sum(t["net_pnl"] for t in side_trades)
            wins = len([t for t in side_trades if t["net_pnl"] > 0])
            print(f"  {side}: {len(side_trades)} 笔, 盈亏={total:+.2f}, 胜率={wins/len(side_trades)*100:.1f}%")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="SOL/USDT")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--adx-min", type=float, default=19.0)
    args = parser.parse_args()

    # 运行回测
    engine = await run_backtest(args.symbol, args.timeframe, args.days)
    if engine:
        analyze_strict_bias_impact(engine, adx_min=args.adx_min)


if __name__ == "__main__":
    asyncio.run(main())
