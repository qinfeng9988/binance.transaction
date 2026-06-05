#!/usr/bin/env python3
"""SOL 分时段收益分析 —— 对比前30天和后30天表现。"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.data_store import DataStore
from quant_trader.main import PARENT_TIMEFRAME
from quant_trader.strategies.s_r_levels import SupportResistanceStrategy
import psycopg2

FEE_RATE = 0.0004


def get_klines_range(symbol: str, timeframe: str, start: datetime, end: datetime) -> list[dict]:
    config = ConfigLoader()
    exchange_cfg = config.load_static()
    conn = psycopg2.connect(
        host=exchange_cfg.db_host, port=exchange_cfg.db_port,
        dbname=exchange_cfg.db_name, user=exchange_cfg.db_user,
        password=exchange_cfg.db_password,
    )
    cur = conn.cursor()
    cur.execute(
        """SELECT timestamp, open, high, low, close, volume
           FROM klines
           WHERE symbol = %s AND timeframe = %s
             AND timestamp >= %s AND timestamp <= %s
           ORDER BY timestamp ASC""",
        (symbol, timeframe, start, end),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
        for r in rows
    ]


class SimpleEngine:
    def __init__(self, initial=100000.0):
        self.initial = initial
        self.balance = initial
        self.positions = {}
        self.trades = []
        self.equity = []

    def check_sl(self, symbol, kline):
        pos = self.positions.get(symbol)
        if not pos:
            return False
        if pos["side"] == "buy" and kline["low"] <= pos["stop"]:
            self.close(symbol, pos["stop"], "stop_loss", kline)
            return True
        elif pos["side"] == "sell" and kline["high"] >= pos["stop"]:
            self.close(symbol, pos["stop"], "stop_loss", kline)
            return True
        return False

    def close(self, symbol, exit_price, reason, kline):
        pos = self.positions.pop(symbol)
        entry = pos["entry"]
        qty = pos["qty"]
        lev = pos["lev"]
        if pos["side"] == "buy":
            gross = (exit_price - entry) * qty * lev
        else:
            gross = (entry - exit_price) * qty * lev
        fee = (entry + exit_price) * qty * FEE_RATE
        net = gross - fee
        self.balance += net
        self.trades.append({
            "side": pos["side"], "entry": entry, "exit": exit_price,
            "lev": lev, "net": net, "reason": reason,
            "time": kline["timestamp"],
        })

    def open_pos(self, symbol, side, price, lev, stop, ts):
        existing = self.positions.get(symbol)
        if existing and existing["side"] != side:
            self.close(symbol, price, "reverse", {"timestamp": ts})
            existing = None
        if existing:
            return  # 忽略同向加仓简化处理
        qty = (self.balance * 0.1) / price
        fee = price * qty * FEE_RATE
        self.balance -= fee
        self.positions[symbol] = {"side": side, "entry": price, "qty": qty, "lev": lev, "stop": stop}

    def record_equity(self, ts, price_map):
        total = self.balance
        for sym, pos in self.positions.items():
            p = price_map.get(sym, pos["entry"])
            if pos["side"] == "buy":
                val = pos["qty"] * p + (p - pos["entry"]) * pos["qty"] * pos["lev"]
            else:
                val = pos["qty"] * p + (pos["entry"] - p) * pos["qty"] * pos["lev"]
            total += val - pos["qty"] * pos["entry"]
        self.equity.append((ts, total))


def run_period(symbol, timeframe, start, end, params, config_loader):
    klines = get_klines_range(symbol, timeframe, start, end)
    parent_tf = PARENT_TIMEFRAME.get(timeframe)
    parent_klines = []
    if parent_tf:
        parent_klines = get_klines_range(symbol, parent_tf, start, end)

    if not klines:
        return None

    strategy = SupportResistanceStrategy("test", params, config_loader)
    engine = SimpleEngine()

    for i, k in enumerate(klines):
        engine.check_sl(symbol, k)
        hist = klines[max(0, i - 200):i + 1]
        parent_hist = []
        if parent_klines:
            parent_hist = [p for p in parent_klines if p["timestamp"] <= k["timestamp"]]
            parent_hist = parent_hist[-200:]
        signals = strategy.on_bar(symbol, timeframe, hist, engine.positions.values(), parent_klines=parent_hist)
        engine.record_equity(k["timestamp"], {symbol: k["close"]})
        for sig in signals:
            if sig.symbol == symbol:
                engine.open_pos(symbol, sig.signal_type, sig.price, sig.leverage, sig.stop_loss, k["timestamp"])

    if engine.positions:
        for sym in list(engine.positions.keys()):
            engine.close(sym, klines[-1]["close"], "end", klines[-1])

    ret = (engine.balance - engine.initial) / engine.initial * 100
    wins = [t for t in engine.trades if t["net"] > 0]
    losses = [t for t in engine.trades if t["net"] <= 0]
    pf = abs(sum(t["net"] for t in wins) / sum(t["net"] for t in losses)) if losses else float("inf")

    peak = engine.initial
    max_dd = 0
    for _, eq in engine.equity:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "return": ret, "trades": len(engine.trades), "win_rate": len(wins) / len(engine.trades) * 100 if engine.trades else 0,
        "pf": pf, "max_dd": max_dd, "equity": engine.equity,
    }


def main():
    config = ConfigLoader()
    config.load_strategies()
    params = None
    for s in config.strategies.strategies:
        if s.name == "support_resistance_sol":
            params = s.params
            break

    now = datetime.now(timezone.utc)

    # 后30天（最近）
    late_start = now - timedelta(days=30)
    late = run_period("SOL/USDT", "5m", late_start, now, params, config)

    # 前30天（30-60天前）
    early_start = now - timedelta(days=60)
    early_end = now - timedelta(days=30)
    early = run_period("SOL/USDT", "5m", early_start, early_end, params, config)

    # 60天整体
    full = run_period("SOL/USDT", "5m", early_start, now, params, config)

    print("=" * 70)
    print("SOL/USDT 5m 分时段收益分析")
    print("=" * 70)
    print(f"\n{'时段':<20} {'收益率':<10} {'交易次数':<8} {'胜率%':<8} {'PF':<6} {'最大回撤%':<10}")
    print("-" * 70)
    print(f"{'前30天 (30-60天前)':<20} {early['return']:<10.2f} {early['trades']:<8} {early['win_rate']:<8.1f} {early['pf']:<6.2f} {early['max_dd']:<10.2f}")
    print(f"{'后30天 (最近)':<20} {late['return']:<10.2f} {late['trades']:<8} {late['win_rate']:<8.1f} {late['pf']:<6.2f} {late['max_dd']:<10.2f}")
    print(f"{'60天整体':<20} {full['return']:<10.2f} {full['trades']:<8} {full['win_rate']:<8.1f} {full['pf']:<6.2f} {full['max_dd']:<10.2f}")

    # 权益曲线对比
    print("\n" + "=" * 70)
    print("权益曲线关键点")
    print("=" * 70)
    for name, data in [("前30天", early), ("后30天", late)]:
        if data["equity"]:
            start_eq = data["equity"][0][1]
            end_eq = data["equity"][-1][1]
            min_eq = min(e[1] for e in data["equity"])
            print(f"{name}: 起始={start_eq:.2f}, 最低={min_eq:.2f}, 结束={end_eq:.2f}")


if __name__ == "__main__":
    main()
