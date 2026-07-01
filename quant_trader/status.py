"""快速查看账户状态：余额、持仓、历史交易。

本命令通过 HTTP 请求交易引擎暴露的状态服务获取数据，
因此只有在引擎运行时才能查到实时状态；引擎未启动时会明确提示。
"""

import asyncio
import sys
from datetime import datetime

import aiohttp

from quant_trader.core.config_loader import ConfigLoader


API_PATH = "/api/v1/status"


def _fmt_ts(iso_str: str | None) -> str:
    """把 ISO 时间字符串格式化为 %m-%d %H:%M。"""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso_str[:16] if len(iso_str) >= 16 else iso_str


def _format_summary(summary: dict):
    print("\n" + "=" * 60)
    print("  量化交易账户概览")
    print("=" * 60)
    print(f"  初始资金:    {summary['initial_balance']:>12,.2f} USDT")
    print(f"  可用余额:    {summary['balance']:>12,.2f} USDT")
    print(f"  总权益:      {summary['total_equity']:>12,.2f} USDT")
    print(f"  收益率:      {summary['return_pct']:>11.2f} %")
    print("-" * 60)
    print(f"  总交易次数:  {summary['total_trades']:>12}")
    print(f"  盈利次数:    {summary['wins']:>12}")
    print(f"  亏损次数:    {summary['losses']:>12}")
    print(f"  胜率:        {summary['win_rate']:>11.2f} %")
    print(f"  累计盈亏:    {summary['total_pnl']:>12,.2f} USDT")
    print(f"  累计手续费:  {summary['total_fees']:>12,.2f} USDT")
    print("=" * 60)


def _format_positions(positions: dict):
    if not positions:
        print("\n  当前无持仓")
        return

    print(f"\n  当前持仓 ({len(positions)} 个)")
    print("-" * 90)
    print(f"  {'交易对':<12} {'方向':<5} {'入场价':>10} {'实时价格':>10} "
          f"{'数量':>10} {'杠杆':>4} {'未实现盈亏':>12} {'止损':>10}")
    print("-" * 90)
    for sym, pos in positions.items():
        pnl_str = f"{pos['unrealized_pnl']:>+10.2f}"
        print(f"  {sym:<12} {pos['side']:<5} {pos['entry_price']:>10,.2f} "
              f"{pos['current_price']:>10,.2f} {pos['quantity']:>10.4f} "
              f"{pos['leverage']:>4}x {pnl_str} {pos['stop_loss']:>10,.2f}")
    print("-" * 90)


def _format_trades(trades: list):
    if not trades:
        print("\n  暂无历史交易记录")
        return

    print(f"\n  历史交易 ({len(trades)} 笔，最近 20 笔)")
    print("-" * 110)
    print(f"  {'#':<4} {'交易对':<12} {'方向':<5} {'入场价':>9} {'出场价':>9} "
          f"{'数量':>8} {'杠杆':>4} {'盈亏':>10} {'入场时间':<12} {'出场时间':<12}")
    print("-" * 110)
    for i, t in enumerate(trades, 1):
        pnl_str = f"{t['pnl']:>+9.2f}"
        et = t.get("entry_time")
        xt = t.get("exit_time")
        entry_ts = _fmt_ts(et)
        exit_ts = _fmt_ts(xt)
        print(f"  {i:<4} {t['symbol']:<12} {t['side']:<5} "
              f"{t['entry_price']:>9,.2f} {t['exit_price']:>9,.2f} "
              f"{t['quantity']:>8.4f} {t['leverage']:>4}x {pnl_str} {entry_ts:<12} {exit_ts:<12}")
    print("-" * 110)


def _format_signal_stats(signal_stats: dict):
    total_signals = signal_stats["total_signals"]
    total_orders = signal_stats["total_orders"]
    blocked = signal_stats["blocked"]

    if total_signals > 0:
        print(f"\n  信号拦截统计")
        print("-" * 40)
        print(f"  总信号数:       {total_signals:>8}")
        print(f"  实际成交:       {total_orders:>8}")
        print(f"  被拦截:         {blocked:>8} ({blocked/total_signals*100:.1f}%)")
        print("-" * 40)


def _format_filter_stats(filter_stats: list):
    if not filter_stats:
        return

    print("\n  === FILTER SUMMARY ===")
    # 按 direction 分组，再按 filter_type 索引
    by_direction: dict[str, dict[str, int]] = {}
    for row in filter_stats:
        direction = row["direction"]
        ft = row["filter_type"]
        if direction not in by_direction:
            by_direction[direction] = {}
        by_direction[direction][ft] = row["rejected_count"]

    for direction in sorted(by_direction.keys()):
        ft_map = by_direction[direction]
        checked = ft_map.get("checked", 0)
        passed = ft_map.get("passed", 0)
        rejected = checked - passed
        if checked == 0:
            continue
        print(f"\n  {direction.upper()} direction:")
        print(f"    Total checked S/R levels:     {checked:>8}")
        print(f"    Total signals passed:         {passed:>8} ({passed/checked*100:.1f}%)")
        _show = lambda label, ft: print(
            f"    {label:<30} {ft_map.get(ft, 0):>8} ({ft_map.get(ft, 0)/checked*100:.1f}%)"
        ) if ft_map.get(ft, 0) > 0 else None
        _show("Distance filter rejected:", "distance")
        _show("ADX filter rejected:", "adx")
        _show("DI direction rejected:", "di")
        _show("Quality filter rejected:", "quality")
        _show("EMA filter rejected:", "ema")
        _show("RSI filter rejected:", "rsi")
        _show("Volume filter rejected:", "volume")
        _show("Rejection candle rejected:", "rejection")
        _show("Pullback filter rejected:", "pullback")
        _show("FR hard block rejected:", "fr_hard_block")
        _show("FR penalty applied:", "fr_penalty")
        _show("Add position rejected:", "add")
        _show("Add limit rejected:", "add_limit")
        _show("Add quality rejected:", "add_quality")
        print(f"    {'Overall rejection rate:':<30} {rejected:>8} ({rejected/checked*100:.1f}%)")


async def _fetch_status(host: str, port: int):
    url = f"http://{host}:{port}{API_PATH}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _main_async():
    cfg = ConfigLoader()
    exchange_cfg = cfg.load_static()
    host = exchange_cfg.status_host
    port = exchange_cfg.status_port

    try:
        data = await _fetch_status(host, port)
    except aiohttp.ClientConnectorError as exc:
        print(f"\n错误：交易引擎状态服务未运行（{host}:{port}）")
        print(f"      请确认交易引擎已启动后再执行 status 命令。")
        sys.exit(1)
    except aiohttp.ClientResponseError as exc:
        print(f"\n错误：交易引擎状态服务返回异常（HTTP {exc.status}）")
        sys.exit(1)
    except Exception as exc:
        print(f"\n错误：请求交易引擎状态服务失败：{exc}")
        sys.exit(1)

    _format_summary(data["summary"])
    _format_positions(data["positions"])
    _format_trades(data["trades"])
    _format_signal_stats(data["signal_stats"])
    _format_filter_stats(data["filter_stats"])
    print()


def main():
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
