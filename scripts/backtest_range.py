#!/usr/bin/env python3
"""指定日期范围的回测脚本。

用法:
    python scripts/backtest_range.py --symbol SOL/USDT --timeframe 5m --start 2026-02-01 --end 2026-04-30
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.data_store import DataStore
from quant_trader.main import PARENT_TIMEFRAME
from quant_trader.strategies.s_r_levels import SupportResistanceStrategy

logger = logging.getLogger(__name__)
FEE_RATE = 0.0004


class BacktestEngine:
    def __init__(self, initial_balance: float = 100000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.equity_curve: list[tuple[datetime, float]] = []

    def _position_value(self, pos: dict, price: float) -> float:
        entry = pos["entry_price"]
        qty = pos["quantity"]
        lev = pos["leverage"]
        if pos["side"] == "buy":
            return qty * price + (price - entry) * qty * lev
        else:
            return qty * price + (entry - price) * qty * lev

    def _record_equity(self, timestamp: datetime, price_map: dict[str, float]):
        total = self.balance
        for sym, pos in self.positions.items():
            p = price_map.get(sym, pos["entry_price"])
            total += self._position_value(pos, p) - pos["quantity"] * pos["entry_price"]
        self.equity_curve.append((timestamp, total))

    def check_stop_loss(self, symbol: str, kline: dict) -> bool:
        pos = self.positions.get(symbol)
        if not pos:
            return False
        stop = pos["stop_loss"]
        side = pos["side"]
        if side == "buy" and kline["low"] <= stop:
            self._close_position(symbol, stop, "stop_loss", kline)
            return True
        elif side == "sell" and kline["high"] >= stop:
            self._close_position(symbol, stop, "stop_loss", kline)
            return True
        return False

    def _close_position(self, symbol: str, exit_price: float, reason: str, kline: dict | None = None):
        pos = self.positions.pop(symbol, None)
        if not pos:
            return
        entry = pos["entry_price"]
        qty = pos["quantity"]
        lev = pos["leverage"]
        side = pos["side"]
        if side == "buy":
            gross_pnl = (exit_price - entry) * qty * lev
        else:
            gross_pnl = (entry - exit_price) * qty * lev
        fee_open = entry * qty * FEE_RATE
        fee_close = exit_price * qty * FEE_RATE
        total_fee = fee_open + fee_close
        net_pnl = gross_pnl - total_fee
        self.balance += net_pnl
        self.trades.append({
            "symbol": symbol, "side": side, "entry_price": entry, "exit_price": exit_price,
            "quantity": qty, "leverage": lev, "gross_pnl": gross_pnl, "fee": total_fee,
            "net_pnl": net_pnl, "reason": reason,
            "entry_time": pos.get("entry_time"), "exit_time": kline["timestamp"] if kline else None,
        })

    def update_stop_loss(self, symbol: str, new_stop: float) -> bool:
        """更新持仓止损位（只能向有利方向移动）。"""
        pos = self.positions.get(symbol)
        if not pos:
            return False
        side = pos["side"]
        current_stop = pos["stop_loss"]
        if side == "buy" and new_stop > current_stop:
            pos["stop_loss"] = new_stop
            return True
        if side == "sell" and new_stop < current_stop:
            pos["stop_loss"] = new_stop
            return True
        return False

    def execute_signal(self, symbol: str, signal_type: str, price: float,
                       stop_loss: float, leverage: int, timestamp: datetime,
                       metadata: dict | None = None):
        existing = self.positions.get(symbol)
        if existing and existing["side"] != signal_type:
            self._close_position(symbol, price, "reverse_signal")
            existing = None
        position_value = self.balance * 0.1
        qty = position_value / price
        if existing:
            add_count = existing.get("add_count", 0)
            if add_count >= 1:
                return
            old_qty = existing["quantity"]
            old_entry = existing["entry_price"]
            total_qty = old_qty + qty
            avg_price = (old_entry * old_qty + price * qty) / total_qty
            existing["quantity"] = total_qty
            existing["entry_price"] = avg_price
            existing["stop_loss"] = stop_loss
            existing["add_count"] = add_count + 1
        else:
            fee = price * qty * FEE_RATE
            self.balance -= fee
            pos = {
                "side": signal_type, "symbol": symbol, "entry_price": price, "quantity": qty,
                "leverage": leverage, "stop_loss": stop_loss,
                "entry_time": timestamp, "add_count": 0,
                "partial_tp_triggered": [],
            }
            if metadata:
                pos["quality_score"] = metadata.get("quality_score", 0.0)
            self.positions[symbol] = pos

    def partial_close_position(self, symbol: str, close_price: float,
                                close_ratio: float, reason: str,
                                kline: dict | None = None):
        pos = self.positions.get(symbol)
        if not pos:
            return
        qty = pos["quantity"]
        close_qty = qty * close_ratio
        if close_qty <= 0:
            return
        entry = pos["entry_price"]
        lev = pos["leverage"]
        side = pos["side"]
        if side == "buy":
            gross_pnl = (close_price - entry) * close_qty * lev
        else:
            gross_pnl = (entry - close_price) * close_qty * lev
        fee_open = entry * close_qty * FEE_RATE
        fee_close = close_price * close_qty * FEE_RATE
        total_fee = fee_open + fee_close
        net_pnl = gross_pnl - total_fee
        self.balance += net_pnl
        self.trades.append({
            "symbol": symbol, "side": side, "entry_price": entry,
            "exit_price": close_price, "quantity": close_qty,
            "leverage": lev, "gross_pnl": gross_pnl, "fee": total_fee,
            "net_pnl": net_pnl, "reason": reason,
            "entry_time": pos.get("entry_time"),
            "exit_time": kline["timestamp"] if kline else None,
        })
        remaining = qty - close_qty
        if remaining <= 1e-12:
            self.positions.pop(symbol, None)
        else:
            pos["quantity"] = remaining

    def get_summary(self) -> dict:
        if not self.trades:
            return {"initial_balance": self.initial_balance, "final_balance": self.balance,
                    "total_return_pct": 0.0, "total_trades": 0, "win_rate": 0.0,
                    "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0, "max_drawdown_pct": 0.0}
        winning = [t for t in self.trades if t["net_pnl"] > 0]
        losing = [t for t in self.trades if t["net_pnl"] <= 0]
        total_win = sum(t["net_pnl"] for t in winning)
        total_loss = abs(sum(t["net_pnl"] for t in losing))
        win_rate = len(winning) / len(self.trades) * 100
        avg_win = total_win / len(winning) if winning else 0
        avg_loss = total_loss / len(losing) if losing else 0
        profit_factor = total_win / total_loss if total_loss > 0 else float("inf")
        max_dd = 0.0
        peak = self.initial_balance
        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return {
            "initial_balance": self.initial_balance, "final_balance": self.balance,
            "total_return_pct": (self.balance - self.initial_balance) / self.initial_balance * 100,
            "total_trades": len(self.trades), "win_rate": win_rate,
            "avg_win": avg_win, "avg_loss": -avg_loss,
            "profit_factor": profit_factor, "max_drawdown_pct": max_dd,
        }


def _align_parent_klines(klines_chrono: list[dict], parent_klines_chrono: list[dict]) -> list[list[dict] | None]:
    parent_idx = 0
    parent_windows: list[list[dict] | None] = []
    n_parent = len(parent_klines_chrono)
    for k in klines_chrono:
        ts = k["timestamp"]
        while parent_idx < n_parent and parent_klines_chrono[parent_idx]["timestamp"] <= ts:
            parent_idx += 1
        if parent_idx == 0:
            parent_windows.append(None)
        else:
            window = parent_klines_chrono[max(0, parent_idx - 200):parent_idx]
            # 转为 新→旧（与实时引擎及 backtest.py 一致）
            parent_windows.append(list(reversed(window)))
    return parent_windows


def run_backtest(symbol: str, timeframe: str, start: datetime, end: datetime, params: dict):
    config = ConfigLoader()
    exchange_cfg = config.load_static()
    ds = DataStore(host=exchange_cfg.db_host, port=exchange_cfg.db_port,
                   dbname=exchange_cfg.db_name, user=exchange_cfg.db_user,
                   password=exchange_cfg.db_password)
    ds.connect()

    # 获取 K 线数据（需要扩展 get_klines 支持时间范围，这里直接查询）
    import psycopg2
    conn = psycopg2.connect(host=exchange_cfg.db_host, port=exchange_cfg.db_port,
                            dbname=exchange_cfg.db_name, user=exchange_cfg.db_user,
                            password=exchange_cfg.db_password)
    cur = conn.cursor()
    cur.execute(
        """SELECT timestamp, open, high, low, close, volume
           FROM klines WHERE symbol = %s AND timeframe = %s
             AND timestamp >= %s AND timestamp <= %s
           ORDER BY timestamp ASC""",
        (symbol, timeframe, start, end),
    )
    klines = [{"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
               "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
              for r in cur.fetchall()]

    parent_tf = PARENT_TIMEFRAME.get(timeframe)
    parent_klines = []
    if parent_tf:
        cur.execute(
            """SELECT timestamp, open, high, low, close, volume
               FROM klines WHERE symbol = %s AND timeframe = %s
                 AND timestamp >= %s AND timestamp <= %s
               ORDER BY timestamp ASC""",
            (symbol, parent_tf, start, end),
        )
        parent_klines = [{"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
                          "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                         for r in cur.fetchall()]

    grandparent_tf = PARENT_TIMEFRAME.get(parent_tf) if parent_tf else None
    grandparent_klines = []
    if grandparent_tf:
        cur.execute(
            """SELECT timestamp, open, high, low, close, volume
               FROM klines WHERE symbol = %s AND timeframe = %s
                 AND timestamp >= %s AND timestamp <= %s
               ORDER BY timestamp ASC""",
            (symbol, grandparent_tf, start, end),
        )
        grandparent_klines = [{"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
                               "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                              for r in cur.fetchall()]
    cur.close()
    conn.close()
    ds.close()

    if not klines:
        logger.warning("无数据")
        return None

    strategy = SupportResistanceStrategy("sol_test", params, config)
    engine = BacktestEngine()
    parent_windows = _align_parent_klines(klines, parent_klines)
    grandparent_windows = _align_parent_klines(klines, grandparent_klines)

    cooldown_seconds = 220
    last_signal_time: dict[str, float] = {}
    window_size = 150

    # 从 window_size 开始，前 window_size 根作为预热（与 backtest.py 一致）
    for i in range(window_size, len(klines)):
        kline = klines[i]
        engine.check_stop_loss(symbol, kline)
        # 窗口转为 新→旧（与策略期望一致）
        window = list(reversed(klines[i - window_size + 1:i + 1]))
        parent_window = parent_windows[i] if i < len(parent_windows) else None
        grandparent_window = grandparent_windows[i] if i < len(grandparent_windows) else None
        signals = strategy.on_bar(symbol, timeframe, window, engine.positions.values(), parent_klines=parent_window, grandparent_klines=grandparent_window)
        price_map = {symbol: kline["close"]}
        engine._record_equity(kline["timestamp"], price_map)
        ts_epoch = kline["timestamp"].timestamp()
        for sig in signals:
            if sig.symbol != symbol:
                continue
            if sig.action == "update_stop":
                engine.update_stop_loss(symbol, sig.stop_loss)
                continue
            if sig.action == "partial_close":
                pos = engine.positions.get(sig.symbol)
                if pos:
                    level_id = sig.metadata.get("partial_tp_level", "unknown")
                    triggered = set(pos.get("partial_tp_triggered", []))
                    if level_id not in triggered:
                        triggered.add(level_id)
                        pos["partial_tp_triggered"] = list(triggered)
                        engine.partial_close_position(
                            symbol=sig.symbol, close_price=sig.price,
                            close_ratio=sig.close_ratio, reason=f"partial_tp_{level_id}", kline=kline,
                        )
                continue
            last = last_signal_time.get(sig.symbol, 0)
            if ts_epoch - last < cooldown_seconds:
                continue
            engine.execute_signal(symbol, sig.signal_type, sig.price, sig.stop_loss, sig.leverage, kline["timestamp"], metadata=sig.metadata)
            last_signal_time[sig.symbol] = ts_epoch
            break  # 每根 K 线只执行一个信号

    if engine.positions:
        for sym in list(engine.positions.keys()):
            engine._close_position(sym, klines[-1]["close"], "end_of_data", klines[-1])

    return engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=timezone.utc)

    config = ConfigLoader()
    config.load_strategies()
    params = None
    for s in config.strategies.strategies:
        if args.symbol in s.symbols and args.timeframe in s.timeframes:
            params = s.params
            break
    if not params:
        logger.error("未找到 %s %s 的策略配置", args.symbol, args.timeframe)
        return

    engine = run_backtest(args.symbol, args.timeframe, start, end, params)
    if not engine:
        return

    summary = engine.get_summary()
    print("\n" + "=" * 60)
    print(f"回测报告  {args.symbol}  {args.timeframe}  {args.start} ~ {args.end}")
    print("=" * 60)
    print(f"初始资金:           {summary['initial_balance']:.2f} USDT")
    print(f"最终资金:           {summary['final_balance']:.2f} USDT")
    print(f"总收益率:           {summary['total_return_pct']:+.2f}%")
    print(f"最大回撤:           {summary['max_drawdown_pct']:.2f}%")
    print(f"总交易次数:         {summary['total_trades']}")
    print(f"胜率:               {summary['win_rate']:.1f}%")
    print(f"平均盈利:           {summary['avg_win']:.2f} USDT")
    print(f"平均亏损:           {summary['avg_loss']:.2f} USDT")
    print(f"profit factor:      {summary['profit_factor']:.2f}")
    print("=" * 60)

    # 按月分析
    print("\n按月盈亏分布:")
    monthly = {}
    for t in engine.trades:
        if t["exit_time"] is None:
            continue
        month = t["exit_time"].strftime("%Y-%m")
        if month not in monthly:
            monthly[month] = {"win": 0, "loss": 0, "count": 0, "pnl": 0}
        monthly[month]["pnl"] += t["net_pnl"]
        monthly[month]["count"] += 1
        if t["net_pnl"] > 0:
            monthly[month]["win"] += 1
        else:
            monthly[month]["loss"] += 1

    print(f"{'月份':<10} {'交易数':<8} {'胜率%':<8} {'盈亏(USDT)':<12} {'累计盈亏':<12}")
    print("-" * 60)
    cumulative = 0
    for month in sorted(monthly.keys()):
        m = monthly[month]
        cumulative += m["pnl"]
        wr = m["win"] / m["count"] * 100 if m["count"] > 0 else 0
        print(f"{month:<10} {m['count']:<8} {wr:<8.1f} {m['pnl']:<+12.2f} {cumulative:<+12.2f}")

    # 逐笔明细
    print("\n逐笔交易明细:")
    print(f"{'序号':<4} {'方向':<5} {'入场价':<10} {'出场价':<10} {'杠杆':<5} {'毛利':<12} {'手续费':<10} {'净利':<12} {'原因':<15} {'时间':<20}")
    print("-" * 130)
    for i, t in enumerate(engine.trades, 1):
        ts = t["exit_time"].strftime("%m-%d %H:%M") if t["exit_time"] else ""
        print(f"{i:<4} {t['side']:<5} {t['entry_price']:<10.2f} {t['exit_price']:<10.2f} {t['leverage']:<5}x {t['gross_pnl']:<12.2f} {t['fee']:<10.2f} {t['net_pnl']:<+12.2f} {t['reason']:<15} {ts:<20}")


if __name__ == "__main__":
    main()
