#!/usr/bin/env python3
"""SOL 参数网格搜索脚本。"""

import argparse
import asyncio
import logging
import sys
import yaml
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
        pos = self.positions[symbol]
        entry = pos["entry_price"]
        qty = pos["quantity"]
        lev = pos["leverage"]
        side = pos["side"]

        if side == "buy":
            gross_pnl = (exit_price - entry) * qty * lev
        else:
            gross_pnl = (entry - exit_price) * qty * lev

        fee = qty * exit_price * FEE_RATE
        net_pnl = gross_pnl - fee
        self.balance += net_pnl

        self.trades.append({
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "leverage": lev,
            "gross": gross_pnl,
            "fee": fee,
            "net": net_pnl,
            "reason": reason,
        })
        del self.positions[symbol]

    def open_position(self, symbol: str, side: str, price: float, quantity: float, leverage: int, stop_loss: float):
        fee = quantity * price * FEE_RATE
        self.balance -= fee
        self.positions[symbol] = {
            "side": side,
            "entry_price": price,
            "quantity": quantity,
            "leverage": leverage,
            "stop_loss": stop_loss,
        }

    def add_position(self, symbol: str, price: float, quantity: float, leverage: int, stop_loss: float):
        pos = self.positions[symbol]
        old_qty = pos["quantity"]
        old_entry = pos["entry_price"]
        new_qty = old_qty + quantity
        avg_entry = (old_entry * old_qty + price * quantity) / new_qty
        fee = quantity * price * FEE_RATE
        self.balance -= fee
        pos["entry_price"] = avg_entry
        pos["quantity"] = new_qty
        pos["stop_loss"] = stop_loss


def run_backtest(symbol: str, timeframe: str, days: int, params: dict) -> dict:
    config = ConfigLoader()
    exchange_cfg = config.load_static()
    data_store = DataStore(
        host=exchange_cfg.db_host, port=exchange_cfg.db_port,
        dbname=exchange_cfg.db_name, user=exchange_cfg.db_user,
        password=exchange_cfg.db_password,
    )
    data_store.connect()

    end_time = datetime.now(timezone.utc)
    start_time = end_time - __import__("datetime").timedelta(days=days)
    klines = data_store.get_klines(symbol, timeframe, start_time, end_time)

    parent_tf = PARENT_TIMEFRAME.get(timeframe)
    parent_klines = []
    if parent_tf:
        parent_klines = data_store.get_klines(symbol, parent_tf, start_time, end_time)

    data_store.close()

    if not klines:
        return {"error": "no data"}

    strategy = SupportResistanceStrategy("sol_test", params)
    engine = BacktestEngine()

    for i, kline in enumerate(klines):
        timestamp = kline["timestamp"]
        engine.check_stop_loss(symbol, kline)

        hist = klines[max(0, i - 200):i + 1]
        parent_hist = []
        if parent_klines:
            parent_hist = [p for p in parent_klines if p["timestamp"] <= timestamp]
            parent_hist = parent_hist[-200:]

        signals = strategy.generate_signals(hist, parent_hist)
        price_map = {symbol: kline["close"]}
        engine._record_equity(timestamp, price_map)

        for sig in signals:
            if sig["symbol"] != symbol:
                continue
            current_pos = engine.positions.get(symbol)

            if current_pos and current_pos["side"] == sig["side"]:
                continue

            if current_pos and current_pos["side"] != sig["side"]:
                engine._close_position(symbol, kline["close"], "reverse_signal", kline)

            qty = engine.balance * 0.1
            engine.open_position(symbol, sig["side"], kline["close"], qty, sig["leverage"], sig["stop_loss"])

    if engine.positions:
        for sym, pos in engine.positions.items():
            engine._close_position(sym, klines[-1]["close"], "end_of_data", klines[-1])

    final_balance = engine.balance
    total_return = (final_balance - engine.initial_balance) / engine.initial_balance * 100
    trades = engine.trades

    if not trades:
        return {"return": 0, "max_dd": 0, "trades": 0, "win_rate": 0, "pf": 0, "params": params}

    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t["net"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["net"] for t in losses) / len(losses) if losses else 0
    pf = abs(sum(t["net"] for t in wins) / sum(t["net"] for t in losses)) if losses and sum(t["net"] for t in losses) != 0 else float("inf")

    peak = engine.initial_balance
    max_dd = 0
    for _, eq in engine.equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "return": total_return,
        "max_dd": max_dd,
        "trades": len(trades),
        "win_rate": win_rate,
        "pf": pf,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "params": {k: v for k, v in params.items()},
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()

    base_params = {
        "lookback": 100,
        "min_touches": 4,
        "stop_loss_pct": 1.5,
        "leverage": 3,
        "entry_threshold_pct": 0.5,
        "secondary_min_touches": 4,
        "secondary_entry_threshold_pct": 0.3,
        "adaptive_swing_window": True,
        "swing_window_adx_threshold": 25.0,
        "swing_window_strong": 3,
        "swing_window_weak": 2,
        "use_order_blocks": True,
        "use_fvg": True,
        "trend_bonus_aligned": 1.3,
        "trend_bonus_counter": -1.0,
        "require_parent_ema_aligned": True,
        "ema_fast_period": 9,
        "ema_slow_period": 21,
        "ema_gap_threshold_pct": 0.5,
        "adx_period": 14,
        "adx_min": 19.0,
        "adx_min_high_quality": 17.0,
        "di_tolerance": 2.0,
        "quality_threshold_for_lower_adx": 5.5,
        "price_vs_ema_filter": False,
        "rsi_period": 14,
        "rsi_long_min": 35.0,
        "rsi_long_max": 75.0,
        "rsi_short_min": 25.0,
        "rsi_short_max": 58.0,
        "rsi_countertrend_long_max": 45.0,
        "rsi_countertrend_short_min": 55.0,
        "volume_burst_ratio": 1.5,
        "volume_burst_lookback": 20,
        "secondary_min_quality": 6.0,
        "secondary_volume_ratio": 1.6,
        "use_atr_stop": True,
        "atr_period": 14,
        "atr_multiplier": 4.0,
        "require_rejection_candle": False,
        "require_pullback_retest": False,
        "pullback_lookback": 10,
    }

    # 网格搜索参数
    grids = {
        "entry_threshold_pct": [0.5, 0.7, 0.9, 1.2],
        "min_touches": [3, 4, 5],
        "atr_multiplier": [3.0, 4.0, 5.0],
        "adx_min": [15.0, 17.0, 19.0, 22.0],
    }

    import itertools
    keys = list(grids.keys())
    values = list(grids.values())

    results = []
    for combo in itertools.product(*values):
        params = dict(base_params)
        for k, v in zip(keys, combo):
            params[k] = v
        print(f"\n测试: entry={params['entry_threshold_pct']}, touches={params['min_touches']}, atr={params['atr_multiplier']}, adx={params['adx_min']}")
        result = run_backtest("SOL/USDT", "5m", args.days, params)
        print(f"  收益={result['return']:.2f}%, 回撤={result['max_dd']:.2f}%, 交易={result['trades']}, 胜率={result['win_rate']:.1f}%, PF={result['pf']:.2f}")
        results.append(result)

    # 排序输出
    results = [r for r in results if r["trades"] >= 5]
    results.sort(key=lambda x: (x["return"] - x["max_dd"] * 0.5), reverse=True)

    print("\n" + "=" * 80)
    print("TOP 10 参数组合（按 收益 - 0.5*回撤 排序，至少5笔交易）")
    print("=" * 80)
    for i, r in enumerate(results[:10]):
        p = r["params"]
        print(f"#{i+1}: 收益={r['return']:.2f}%, 回撤={r['max_dd']:.2f}%, 交易={r['trades']}, 胜率={r['win_rate']:.1f}%, PF={r['pf']:.2f}")
        print(f"     entry={p['entry_threshold_pct']}, touches={p['min_touches']}, atr={p['atr_multiplier']}, adx={p['adx_min']}")


if __name__ == "__main__":
    main()
