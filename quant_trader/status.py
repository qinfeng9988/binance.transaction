"""快速查看账户状态：余额、持仓、历史交易。"""

from core.config_loader import ConfigLoader
from core.data_store import DataStore
from exchange.paper_trading import PaperTradingEngine


def main():
    cfg = ConfigLoader()
    exchange_cfg = cfg.load_static()

    ds = DataStore(
        host=exchange_cfg.db_host,
        port=exchange_cfg.db_port,
        dbname=exchange_cfg.db_name,
        user=exchange_cfg.db_user,
        password=exchange_cfg.db_password,
    )
    ds.connect()

    # 从数据库恢复模拟账户状态
    engine = PaperTradingEngine(
        initial_balance=100000.0,
        default_type=exchange_cfg.default_type,
    )
    engine.restore_from_db(ds)

    # ====== 账户概览 ======
    summary = engine.get_summary()
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

    # ====== 当前持仓 ======
    if engine.positions:
        print(f"\n  当前持仓 ({len(engine.positions)} 个)")
        print("-" * 90)
        print(f"  {'交易对':<12} {'方向':<5} {'入场价':>10} {'现价':>10} "
              f"{'数量':>10} {'杠杆':>4} {'未实现盈亏':>12} {'止损':>10}")
        print("-" * 90)
        for sym, pos in engine.positions.items():
            pnl_str = f"{pos.unrealized_pnl:>+10.2f}"
            print(f"  {sym:<12} {pos.side:<5} {pos.entry_price:>10,.2f} "
                  f"{pos.current_price:>10,.2f} {pos.quantity:>10.4f} "
                  f"{pos.leverage:>4}x {pnl_str} {pos.stop_loss:>10,.2f}")
        print("-" * 90)
    else:
        print("\n  当前无持仓")

    # ====== 历史交易 ======
    if engine.trades_closed:
        print(f"\n  历史交易 ({len(engine.trades_closed)} 笔，最近 20 笔)")
        print("-" * 100)
        print(f"  {'#':<4} {'交易对':<12} {'方向':<5} {'入场价':>9} {'出场价':>9} "
              f"{'数量':>8} {'杠杆':>4} {'盈亏':>10} {'入场时间':<20}")
        print("-" * 100)
        for i, t in enumerate(engine.trades_closed[-20:], 1):
            pnl_str = f"{t['pnl']:>+9.2f}"
            entry_time = str(t.get("entry_time", ""))[:19]
            print(f"  {i:<4} {t['symbol']:<12} {t['side']:<5} "
                  f"{t['entry_price']:>9,.2f} {t['exit_price']:>9,.2f} "
                  f"{t['quantity']:>8.4f} {t['leverage']:>4}x {pnl_str} {entry_time:<20}")
        print("-" * 100)
    else:
        print("\n  暂无历史交易记录")

    print()
    ds.close()


if __name__ == "__main__":
    main()
