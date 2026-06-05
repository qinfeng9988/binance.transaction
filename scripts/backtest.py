#!/usr/bin/env python3
"""策略回测脚本。

从本地数据库读取历史 K 线，按时间正序逐根推进，模拟交易执行，输出回测报告。

用法:
    python scripts/backtest.py --symbol BTC/USDT --timeframe 5m --days 30
    python scripts/backtest.py --symbol ETH/USDT --timeframe 1h --days 60
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

# 合约手续费率（Binance U 本位合约 Maker/Taker 约 0.04%）
FEE_RATE = 0.0004


class BacktestEngine:
    """简单回测引擎 — 逐 K 线推进，模拟成交与止损。"""

    def __init__(self, initial_balance: float = 100000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions: dict[str, dict] = {}   # symbol -> position
        self.trades: list[dict] = []           # 成交记录
        self.equity_curve: list[tuple[datetime, float]] = []  # 权益曲线

    def _position_value(self, pos: dict, price: float) -> float:
        """计算持仓当前市值（含杠杆盈亏）。"""
        entry = pos["entry_price"]
        qty = pos["quantity"]
        lev = pos["leverage"]
        if pos["side"] == "buy":
            return qty * price + (price - entry) * qty * lev
        else:
            return qty * price + (entry - price) * qty * lev

    def _record_equity(self, timestamp: datetime, price_map: dict[str, float]):
        """记录当前总权益。"""
        total = self.balance
        for sym, pos in self.positions.items():
            p = price_map.get(sym, pos["entry_price"])
            total += self._position_value(pos, p) - pos["quantity"] * pos["entry_price"]
        self.equity_curve.append((timestamp, total))

    def check_stop_loss(self, symbol: str, kline: dict) -> bool:
        """检查当前 K 线是否触及止损，返回是否触发。"""
        pos = self.positions.get(symbol)
        if not pos:
            return False

        stop = pos["stop_loss"]
        side = pos["side"]

        if side == "buy" and kline["low"] <= stop:
            # 做多止损：K 线低点触及或跌破止损价
            # 简化处理：以止损价成交（实际可能在 stop ~ kline["low"] 之间）
            self._close_position(symbol, stop, "stop_loss", kline)
            return True
        elif side == "sell" and kline["high"] >= stop:
            self._close_position(symbol, stop, "stop_loss", kline)
            return True

        return False

    def _close_position(self, symbol: str, exit_price: float, reason: str, kline: dict | None = None):
        """平仓并记录。"""
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
            "symbol": symbol,
            "side": side,
            "entry_price": entry,
            "exit_price": exit_price,
            "quantity": qty,
            "leverage": lev,
            "gross_pnl": gross_pnl,
            "fee": total_fee,
            "net_pnl": net_pnl,
            "reason": reason,
            "entry_time": pos.get("entry_time"),
            "exit_time": kline["timestamp"] if kline else None,
        })

        logger.info(
            "平仓 %s %s 入场=%.2f 出场=%.2f 毛利=%.2f 手续费=%.2f 净利=%.2f 原因=%s",
            symbol, side, entry, exit_price, gross_pnl, total_fee, net_pnl, reason,
        )

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
        """执行信号：反向平仓 / 同向加仓 / 新开仓。"""
        existing = self.positions.get(symbol)

        # 反向持仓 → 先平仓
        if existing and existing["side"] != signal_type:
            self._close_position(symbol, price, "reverse_signal")
            existing = None

        position_value = self.balance * 0.1
        qty = position_value / price

        if existing:
            # 同向加仓 — 限制最多加仓 1 次（最多 2 层仓位）
            add_count = existing.get("add_count", 0)
            if add_count >= 1:
                logger.info(
                    "忽略 %s %s 同向信号: 已达最大加仓次数(%d)",
                    symbol, signal_type, add_count,
                )
                return

            old_qty = existing["quantity"]
            old_entry = existing["entry_price"]
            total_qty = old_qty + qty
            avg_price = (old_entry * old_qty + price * qty) / total_qty
            existing["quantity"] = total_qty
            existing["entry_price"] = avg_price
            existing["stop_loss"] = stop_loss
            existing["add_count"] = add_count + 1
            logger.info(
                "加仓 %s %s 新均价=%.2f 总数量=%.4f 止损=%.2f 加仓次数=%d",
                symbol, signal_type, avg_price, total_qty, stop_loss, add_count + 1,
            )
        else:
            # 新开仓
            fee = price * qty * FEE_RATE
            self.balance -= fee  # 先扣开仓手续费
            pos = {
                "side": signal_type,
                "symbol": symbol,
                "entry_price": price,
                "quantity": qty,
                "leverage": leverage,
                "stop_loss": stop_loss,
                "entry_time": timestamp,
                "add_count": 0,
                "partial_tp_triggered": [],
            }
            if metadata:
                pos["quality_score"] = metadata.get("quality_score", 0.0)
            self.positions[symbol] = pos
            logger.info(
                "开仓 %s %s 价格=%.2f 数量=%.4f 杠杆=%dx 止损=%.2f",
                symbol, signal_type, price, qty, leverage, stop_loss,
            )

    def partial_close_position(self, symbol: str, close_price: float,
                                close_ratio: float, reason: str,
                                kline: dict | None = None):
        """部分平仓：平仓指定比例的仓位。"""
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

        logger.info(
            "部分平仓 %s %s 出场=%.2f 平仓比例=%.0f%% 数量=%.4f 剩余=%.4f 净利=%.2f 原因=%s",
            symbol, side, close_price, close_ratio * 100, close_qty, remaining, net_pnl, reason,
        )

    def get_summary(self) -> dict:
        """计算回测指标。"""
        if not self.trades:
            return {
                "initial_balance": self.initial_balance,
                "final_balance": self.balance,
                "total_return_pct": 0.0,
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
            }

        winning = [t for t in self.trades if t["net_pnl"] > 0]
        losing = [t for t in self.trades if t["net_pnl"] <= 0]

        total_win = sum(t["net_pnl"] for t in winning)
        total_loss = abs(sum(t["net_pnl"] for t in losing))

        win_rate = len(winning) / len(self.trades) * 100
        avg_win = total_win / len(winning) if winning else 0
        avg_loss = total_loss / len(losing) if losing else 0
        profit_factor = total_win / total_loss if total_loss > 0 else float("inf")

        # 最大回撤（基于权益曲线）
        max_dd = 0.0
        peak = self.initial_balance
        for _, equity in self.equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd:
                max_dd = dd

        return {
            "initial_balance": self.initial_balance,
            "final_balance": self.balance,
            "total_return_pct": (self.balance - self.initial_balance) / self.initial_balance * 100,
            "total_trades": len(self.trades),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": -avg_loss,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_dd,
        }


def _align_parent_klines(klines_chrono: list[dict], parent_klines_chrono: list[dict]) -> list[list[dict] | None]:
    """将上级周期 K 线与当前周期对齐，返回每根当前 K 线对应的 parent 滑动窗口。

    参数:
        klines_chrono: 当前周期 K 线（旧→新）
        parent_klines_chrono: 上级周期 K 线（旧→新）

    返回:
        与 klines_chrono 等长的列表，每个元素是 parent 滑动窗口（新→旧）或 None
    """
    if not parent_klines_chrono:
        return [None] * len(klines_chrono)

    parent_window_size = 50  # EMA50 所需的最小数据量
    result: list[list[dict] | None] = []
    parent_idx = 0

    for k in klines_chrono:
        k_ts = k["timestamp"]
        # 将 timestamp 统一转为 epoch 秒（数据库返回 datetime，API 返回毫秒）
        k_epoch = k_ts.timestamp() if isinstance(k_ts, datetime) else k_ts / 1000

        # 找到包含当前 K 线的 parent K 线（parent 时间 <= 当前 K 线时间）
        while parent_idx < len(parent_klines_chrono) - 1:
            p_ts = parent_klines_chrono[parent_idx + 1]["timestamp"]
            p_epoch = p_ts.timestamp() if isinstance(p_ts, datetime) else p_ts / 1000
            if p_epoch <= k_epoch:
                parent_idx += 1
            else:
                break

        # 取 parent_idx 及之前的 parent_window_size-1 根，组成窗口
        start = max(0, parent_idx - parent_window_size + 1)
        window = parent_klines_chrono[start:parent_idx + 1]
        # 转为新→旧（与实时引擎一致）
        result.append(list(reversed(window)) if len(window) >= 50 else None)

    return result


# 策略名称自动映射: 根据币种推断默认策略名
_SYMBOL_TO_STRATEGY = {
    "BTC/USDT": "support_resistance_btc",
    "ETH/USDT": "support_resistance_eth",
    "SOL/USDT": "support_resistance_sol",
    "PAXG/USDT": "support_resistance_paxg",
}


async def run_backtest(symbol: str, timeframe: str, days: int, strategy_name: str | None = None):
    """执行单币种单周期回测（含上级周期数据）。

    参数:
        symbol: 交易对
        timeframe: K线周期
        days: 回测天数
        strategy_name: 策略名称（默认根据币种自动推断）
    """
    config = ConfigLoader()
    exchange_cfg = config.load_static()
    config.load_strategies()

    data_store = DataStore(
        host=exchange_cfg.db_host,
        port=exchange_cfg.db_port,
        dbname=exchange_cfg.db_name,
        user=exchange_cfg.db_user,
        password=exchange_cfg.db_password,
    )
    data_store.connect()

    # 确定策略名称
    if not strategy_name:
        strategy_name = _SYMBOL_TO_STRATEGY.get(symbol)
        if not strategy_name:
            # 未映射的币种回退到通用策略名
            base = symbol.split("/")[0].lower()
            strategy_name = f"support_resistance_{base}"

    # 查找策略配置
    strategy_cfg = None
    for s in config.strategies.strategies:
        if s.name == strategy_name and s.enabled:
            strategy_cfg = s
            break

    # 如果精确匹配失败，尝试模糊匹配（币种包含在策略symbols中）
    if not strategy_cfg:
        for s in config.strategies.strategies:
            if s.enabled and symbol in s.symbols:
                strategy_cfg = s
                strategy_name = s.name
                break

    if not strategy_cfg:
        logger.error("未找到启用的策略配置: %s (可用策略: %s)",
                     strategy_name,
                     [s.name for s in config.strategies.strategies if s.enabled])
        return

    logger.info("回测使用策略: %s, 币种: %s", strategy_name, symbol)
    strategy = SupportResistanceStrategy(strategy_cfg.name, strategy_cfg.params, config)

    # 计算需要的数据量
    warmup = 200
    candles_per_day = {"5m": 288, "1h": 24, "4h": 6}.get(timeframe, 24)
    needed = days * candles_per_day + warmup

    # ---- 读取当前周期数据 ----
    logger.info("从数据库读取 %s %s 数据, 请求 %d 根 K 线", symbol, timeframe, needed)
    klines = data_store.get_klines(symbol, timeframe, limit=needed)

    if len(klines) < warmup + 10:
        logger.error("数据不足: %s %s 数据库中只有 %d 根 K 线, 至少需要 %d 根",
                     symbol, timeframe, len(klines), warmup + 10)
        logger.error("请先运行: python scripts/fetch_history.py --symbol %s --timeframe %s --days %d",
                     symbol, timeframe, days)
        return

    # get_klines 返回 新→旧，转为 旧→新
    klines_chrono = list(reversed(klines))

    # ---- 读取上级周期数据 ----
    parent_tf = PARENT_TIMEFRAME.get(timeframe)
    parent_klines_chrono: list[dict] = []
    parent_aligned: list[list[dict] | None] = []

    if parent_tf:
        # 上级周期需要更少的数据（但覆盖相同时间范围）
        parent_candles_per_day = {"5m": 288, "1h": 24, "4h": 6, "1d": 1}.get(parent_tf, 24)
        parent_needed = days * parent_candles_per_day + 60  # 上级周期预热 60 根
        logger.info("从数据库读取上级周期 %s %s 数据, 请求 %d 根 K 线", symbol, parent_tf, parent_needed)
        parent_klines = data_store.get_klines(symbol, parent_tf, limit=parent_needed)
        if len(parent_klines) < 50:
            logger.warning("上级周期 %s %s 数据不足 (%d 根), 回测将不使用 parent 锚定",
                           symbol, parent_tf, len(parent_klines))
        else:
            parent_klines_chrono = list(reversed(parent_klines))
            parent_aligned = _align_parent_klines(klines_chrono, parent_klines_chrono)
            logger.info("上级周期 %s 对齐完成: %d 根当前 K 线, %d 根上级 K 线",
                        parent_tf, len(klines_chrono), len(parent_klines_chrono))
    else:
        parent_aligned = [None] * len(klines_chrono)

    # ---- 读取祖父周期数据（多周期共振）----
    grandparent_tf = PARENT_TIMEFRAME.get(parent_tf) if parent_tf else None
    grandparent_klines_chrono: list[dict] = []
    grandparent_aligned: list[list[dict] | None] = []

    if grandparent_tf:
        gp_candles_per_day = {"5m": 288, "1h": 24, "4h": 6, "1d": 1}.get(grandparent_tf, 24)
        gp_needed = days * gp_candles_per_day + 30  # 祖父周期预热 30 根
        logger.info("从数据库读取祖父周期 %s %s 数据, 请求 %d 根 K 线", symbol, grandparent_tf, gp_needed)
        gp_klines = data_store.get_klines(symbol, grandparent_tf, limit=gp_needed)
        if len(gp_klines) < 50:
            logger.warning("祖父周期 %s %s 数据不足 (%d 根), 回测将不使用 grandparent 锚定",
                           symbol, grandparent_tf, len(gp_klines))
        else:
            grandparent_klines_chrono = list(reversed(gp_klines))
            grandparent_aligned = _align_parent_klines(klines_chrono, grandparent_klines_chrono)
            logger.info("祖父周期 %s 对齐完成: %d 根当前 K 线, %d 根祖父 K 线",
                        grandparent_tf, len(klines_chrono), len(grandparent_klines_chrono))
    else:
        grandparent_aligned = [None] * len(klines_chrono)

    # ---- 回测循环 ----
    engine = BacktestEngine()
    window_size = 150
    cooldown_seconds = 220
    last_signal_time: dict[str, float] = {}

    logger.info("开始回测 %s %s, 共 %d 根 K 线, 预热 %d 根, parent=%s, grandparent=%s",
                symbol, timeframe, len(klines_chrono), window_size,
                parent_tf or "无", grandparent_tf or "无")

    for i in range(window_size, len(klines_chrono)):
        current = klines_chrono[i]
        ts = current["timestamp"]
        ts_epoch = ts.timestamp() if isinstance(ts, datetime) else ts / 1000

        # 1. 止损检查
        engine.check_stop_loss(symbol, current)

        # 2. 记录权益
        price_map = {symbol: current["close"]}
        engine._record_equity(
            ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
            price_map,
        )

        # 3. 准备滑动窗口（新→旧）
        window = list(reversed(klines_chrono[i - window_size + 1:i + 1]))

        # 4. 获取对应的 parent 窗口
        parent_window = parent_aligned[i] if i < len(parent_aligned) else None

        # 5. 获取对应的 grandparent 窗口
        grandparent_window = grandparent_aligned[i] if i < len(grandparent_aligned) else None

        # 6. 调用策略生成信号（传入 parent_klines 和 grandparent_klines）
        signals = strategy.on_bar(symbol, timeframe, window, engine.positions.values(),
                                  parent_klines=parent_window, grandparent_klines=grandparent_window)

        # 7. 执行信号（冷却期去重）
        for sig in signals:
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
                            symbol=sig.symbol,
                            close_price=sig.price,
                            close_ratio=sig.close_ratio,
                            reason=f"partial_tp_{level_id}",
                            kline=current,
                        )
                continue
            last = last_signal_time.get(sig.symbol, 0)
            if ts_epoch - last < cooldown_seconds:
                continue

            engine.execute_signal(
                symbol=sig.symbol,
                signal_type=sig.signal_type,
                price=sig.price,
                stop_loss=sig.stop_loss,
                leverage=sig.leverage,
                timestamp=ts if isinstance(ts, datetime) else datetime.fromtimestamp(ts_epoch, tz=timezone.utc),
                metadata=sig.metadata,
            )
            last_signal_time[sig.symbol] = ts_epoch
            break  # 每根 K 线只执行一个信号

    # ---- 输出报告 ----
    summary = engine.get_summary()

    print("\n" + "=" * 50)
    print(f"回测报告  {symbol}  {timeframe}  近 {days} 天  parent={parent_tf or '无'}")
    print("=" * 50)
    print(f"初始资金:        {summary['initial_balance']:>12.2f} USDT")
    print(f"最终资金:        {summary['final_balance']:>12.2f} USDT")
    print(f"总收益率:        {summary['total_return_pct']:>11.2f}%")
    print(f"最大回撤:        {summary['max_drawdown_pct']:>11.2f}%")
    print(f"-" * 50)
    print(f"总交易次数:      {summary['total_trades']:>12}")
    print(f"胜率:            {summary['win_rate']:>11.1f}%")
    print(f"平均盈利:        {summary['avg_win']:>12.2f} USDT")
    print(f"平均亏损:        {summary['avg_loss']:>12.2f} USDT")
    print(f"盈亏比:          {abs(summary['avg_win'] / summary['avg_loss']) if summary['avg_loss'] != 0 else 0:>12.2f}")
    print(f" profit factor:  {summary['profit_factor']:>12.2f}")
    print("=" * 50 + "\n")

    # ---- 过滤统计汇总 ----
    filter_summary = strategy.get_filter_summary()
    print("=== FILTER SUMMARY ===")
    for direction, stats in filter_summary.items():
        checked = stats["checked"]
        passed = stats["passed"]
        rejected = checked - passed
        if checked == 0:
            continue
        print(f"\n{direction.upper()} direction:")
        print(f"  Total checked S/R levels:     {checked:>8}")
        print(f"  Total signals passed:         {passed:>8} ({passed/checked*100:.1f}%)")
        print(f"  Distance filter rejected:     {stats['distance']:>8} ({stats['distance']/checked*100:.1f}%)")
        print(f"  ADX filter rejected:          {stats['adx']:>8} ({stats['adx']/checked*100:.1f}%)")
        print(f"  DI direction rejected:        {stats['di']:>8} ({stats['di']/checked*100:.1f}%)")
        print(f"  Quality filter rejected:      {stats['quality']:>8} ({stats['quality']/checked*100:.1f}%)")
        print(f"  EMA filter rejected:          {stats['ema']:>8} ({stats['ema']/checked*100:.1f}%)")
        print(f"  RSI filter rejected:          {stats['rsi']:>8} ({stats['rsi']/checked*100:.1f}%)")
        print(f"  Volume filter rejected:       {stats['volume']:>8} ({stats['volume']/checked*100:.1f}%)")
        print(f"  Rejection candle rejected:    {stats['rejection']:>8} ({stats['rejection']/checked*100:.1f}%)")
        print(f"  Pullback filter rejected:     {stats['pullback']:>8} ({stats['pullback']/checked*100:.1f}%)")
        print(f"  Add position rejected:        {stats['add']:>8} ({stats['add']/checked*100:.1f}%)")
        # 注意：各层拦截有重叠（同一 S/R 位可能先后被多个条件过滤），所以各层之和可能 > rejected
        print(f"  Overall rejection rate:       {rejected:>8} ({rejected/checked*100:.1f}%)")
    print()

    if engine.trades:
        print("逐笔交易明细:")
        print(f"{'序号':>4} {'方向':>6} {'入场价':>10} {'出场价':>10} {'杠杆':>4} {'毛利':>10} {'手续费':>8} {'净利':>10} {'原因':>10}")
        for idx, t in enumerate(engine.trades, 1):
            print(f"{idx:>4} {t['side']:>6} {t['entry_price']:>10.2f} {t['exit_price']:>10.2f} "
                  f"{t['leverage']:>4}x {t['gross_pnl']:>10.2f} {t['fee']:>8.2f} "
                  f"{t['net_pnl']:>10.2f} {t['reason']:>10}")
        print()

    data_store.close()


def main():
    parser = argparse.ArgumentParser(description="策略回测")
    parser.add_argument("--symbol", default="BTC/USDT", help="交易对 (默认 BTC/USDT)")
    parser.add_argument("--timeframe", default="5m", help="K 线周期 (默认 5m)")
    parser.add_argument("--days", type=int, default=30, help="回测天数 (默认30)")
    parser.add_argument("--strategy", help="策略名称 (默认根据币种自动推断, 如 support_resistance_btc)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    asyncio.run(run_backtest(args.symbol, args.timeframe, args.days, args.strategy))


if __name__ == "__main__":
    main()
