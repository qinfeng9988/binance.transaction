#!/usr/bin/env python3
"""指定日期范围的回测脚本。

用法:
    python scripts/backtest_range.py --symbol SOL/USDT --timeframe 5m --start 2026-02-01 --end 2026-04-30
"""

import argparse
import asyncio
import bisect
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.data_store import DataStore, KlineRecord
from quant_trader.exchange.binance_adapter import BinanceAdapter
from quant_trader.main import PARENT_TIMEFRAME, TIMEFRAME_SECONDS
from quant_trader.strategies.s_r_levels import SupportResistanceStrategy
from quant_trader.strategies.bollinger_bands import BollingerBandsStrategy
from quant_trader.risk.manager import RiskManager

FUNDING_DATA_DIR = Path(__file__).parent.parent / "quant_trader" / "data" / "funding_rates"


def _load_funding_rates(symbol: str) -> list[dict]:
    """从本地 JSON 缓存读取 funding rate 历史。

    返回按 timestamp_ms 升序排列的记录列表，每条记录含 rate_pct 和 timestamp_ms。
    缓存不存在时返回空列表。
    """
    filename = symbol.replace("/", "_").replace(":", "_") + ".json"
    path = FUNDING_DATA_DIR / filename
    if not path.exists():
        logger.warning("Funding rate 缓存不存在: %s, 回测将不启用 FR 过滤", path)
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        records = data.get("records", [])
        records.sort(key=lambda r: r["timestamp_ms"])
        logger.info("已加载 %s funding rate 缓存: %d 条", symbol, len(records))
        return records
    except Exception as e:
        logger.warning("加载 funding rate 缓存失败 %s: %s", path, e)
        return []


def _match_funding_rate(timestamp_ms: int, records: list[dict]) -> float | None:
    """按时间戳匹配最近的 funding rate。

    使用 bisect 查找不超过 timestamp_ms 的最新记录。
    无匹配记录时返回 None。
    """
    if not records:
        return None
    times = [r["timestamp_ms"] for r in records]
    idx = bisect.bisect_right(times, timestamp_ms) - 1
    if idx < 0:
        return None
    return records[idx]["rate_pct"]


logger = logging.getLogger(__name__)
FEE_RATE = 0.0004


class BacktestEngine:
    def __init__(self, initial_balance: float = 100000.0,
                 max_consecutive_losses: int = 3,
                 cooldown_duration_seconds: int = 3600):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions: dict[str, dict] = {}
        self.trades: list[dict] = []
        self.equity_curve: list[tuple[datetime, float]] = []
        self.consecutive_losses = 0
        self.cooldown_end: datetime | None = None
        self.max_consecutive_losses = max_consecutive_losses
        self.cooldown_duration_seconds = cooldown_duration_seconds

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

        # 连续亏损追踪
        exit_ts = kline["timestamp"] if kline else None
        if net_pnl <= 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_losses and exit_ts:
                self.cooldown_end = exit_ts + timedelta(seconds=self.cooldown_duration_seconds)
                logger.info(
                    "连续亏损 %d 次，进入冷静期 %d 秒，直到 %s",
                    self.consecutive_losses, self.cooldown_duration_seconds, self.cooldown_end,
                )
        else:
            if self.consecutive_losses > 0:
                logger.info("盈利 %.2f，重置连续亏损计数 %d → 0", net_pnl, self.consecutive_losses)
            self.consecutive_losses = 0

        self.trades.append({
            "symbol": symbol, "side": side, "entry_price": entry, "exit_price": exit_price,
            "quantity": qty, "leverage": lev, "gross_pnl": gross_pnl, "fee": total_fee,
            "net_pnl": net_pnl, "reason": reason,
            "entry_time": pos.get("entry_time"), "exit_time": exit_ts,
            "parent_trend": pos.get("parent_trend"),
            "adx": pos.get("adx"),
            "bias": pos.get("bias"),
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
        # 冷静期检查（仅对新开仓，反向平仓和加仓不受限）
        if not self.positions.get(symbol) and self.cooldown_end and timestamp < self.cooldown_end:
            logger.info(
                "拒绝 %s %s 新信号: 处于冷静期 %s",
                symbol, signal_type, self.cooldown_end,
            )
            return

        existing = self.positions.get(symbol)
        if existing and existing["side"] != signal_type:
            self._close_position(symbol, price, "reverse_signal", {"timestamp": timestamp})
            existing = None
        position_value = self.balance * 0.1
        qty = position_value / price
        if existing:
            add_count = existing.get("add_count", 0)
            max_additions = metadata.get("max_additions", 1) if metadata else 1
            if add_count >= max_additions:
                logger.info(
                    "忽略 %s %s 同向加仓: 已达最大次数 %d/%d",
                    symbol, signal_type, add_count, max_additions,
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
                "加仓 %s %s: 新均价=%.2f 总数量=%.4f 加仓次数=%d/%d",
                symbol, signal_type, avg_price, total_qty, add_count + 1, max_additions,
            )
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
                pos["parent_trend"] = metadata.get("parent_trend")
                pos["adx"] = metadata.get("adx")
                pos["bias"] = metadata.get("bias")
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
            "parent_trend": pos.get("parent_trend"),
            "adx": pos.get("adx"),
            "bias": pos.get("bias"),
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


async def _fetch_klines_from_api(adapter: BinanceAdapter, ds: DataStore,
                                  symbol: str, timeframe: str,
                                  start: datetime, end: datetime):
    """从交易所 API 分页拉取 K 线数据并写入数据库（幂等）。"""
    since = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    total_fetched = 0

    while True:
        batch = await adapter.fetch_ohlcv(symbol, timeframe, limit=1000, since=since)
        if not batch:
            break

        records = [
            KlineRecord(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.fromtimestamp(k["timestamp"] / 1000, tz=timezone.utc),
                open=k["open"], high=k["high"], low=k["low"],
                close=k["close"], volume=k["volume"],
            )
            for k in batch
        ]
        ds.insert_klines(records)
        total_fetched += len(batch)

        last_ts = batch[-1]["timestamp"]
        if last_ts >= end_ms or len(batch) < 1000:
            break
        since = last_ts + 1

    logger.info("API 拉取 %s %s: 共 %d 根", symbol, timeframe, total_fetched)


async def _ensure_klines_complete(symbol: str, timeframe: str,
                                   start: datetime, end: datetime,
                                   ds: DataStore, adapter: BinanceAdapter,
                                   tolerance: float = 0.001,
                                   max_retries: int = 5):
    """验证 K 线数据完整性，不完整时从 API 补全。

    tolerance: 允许的缺失率上限（当前周期 0.001=0.1%，parent 周期 0.005=0.5%）
    max_retries: API 补全最大重试次数
    """
    interval = TIMEFRAME_SECONDS.get(timeframe, 300)
    expected = int((end - start).total_seconds() / interval) + 1
    actual = ds.count_klines(symbol, timeframe, start, end)
    missing_rate = (expected - actual) / expected if expected > 0 else 0

    if missing_rate <= tolerance:
        logger.info("K线数据完整 %s %s: %d/%d (缺失率 %.2f%%)",
                    symbol, timeframe, actual, expected, missing_rate * 100)
        return

    logger.warning("K线数据不完整 %s %s: expected=%d, actual=%d, 缺失率 %.2f%%",
                   symbol, timeframe, expected, actual, missing_rate * 100)

    for attempt in range(1, max_retries + 1):
        try:
            await _fetch_klines_from_api(adapter, ds, symbol, timeframe, start, end)
        except Exception as e:
            logger.warning("API 补全失败 (第 %d/%d 次) %s %s: %s",
                           attempt, max_retries, symbol, timeframe, e)
            if attempt == max_retries:
                raise RuntimeError(
                    f"K线数据补全失败 {symbol} {timeframe}: "
                    f"expected={expected}, actual={actual}, 缺失率={missing_rate*100:.2f}%"
                ) from e
            continue

        new_actual = ds.count_klines(symbol, timeframe, start, end)
        new_missing = (expected - new_actual) / expected if expected > 0 else 0
        if new_missing <= tolerance:
            logger.info("补全成功 %s %s: %d/%d (缺失率 %.2f%%)",
                        symbol, timeframe, new_actual, expected, new_missing * 100)
            return

        logger.warning("补全后仍不完整 %s %s: %d/%d (缺失率 %.2f%%), 重试 %d/%d",
                       symbol, timeframe, new_actual, expected, new_missing * 100,
                       attempt + 1, max_retries)

    raise RuntimeError(
        f"K线数据补全失败 {symbol} {timeframe}: "
        f"expected={expected}, 重试 {max_retries} 次后仍不完整"
    )


def _align_parent_klines(klines_chrono: list[dict], parent_klines_chrono: list[dict],
                         parent_lookback: int | None = None) -> list[list[dict] | None]:
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
            start_idx = 0 if parent_lookback is None else max(0, parent_idx - parent_lookback)
            window = parent_klines_chrono[start_idx:parent_idx]
            # 转为 新→旧（与实时引擎及 backtest.py 一致）
            parent_windows.append(list(reversed(window)))
    return parent_windows


async def run_backtest(symbol: str, timeframe: str, start: datetime, end: datetime,
                       params: dict, strategy_name: str = "default",
                       adapter: BinanceAdapter | None = None):
    config = ConfigLoader()
    exchange_cfg = config.load_static()
    ds = DataStore(host=exchange_cfg.db_host, port=exchange_cfg.db_port,
                   dbname=exchange_cfg.db_name, user=exchange_cfg.db_user,
                   password=exchange_cfg.db_password)
    ds.connect()

    # 验证 + 补全当前周期 K 线
    if adapter:
        await _ensure_klines_complete(symbol, timeframe, start, end, ds, adapter, tolerance=0.001)
    klines = ds.get_klines_range(symbol, timeframe, start, end)

    # parent 周期：需要 warmup 扩展
    _CANDLES_PER_DAY = {"5m": 288, "1h": 24, "4h": 6, "1d": 1}
    _WARMUP_CANDLES = 60

    parent_tf = PARENT_TIMEFRAME.get(timeframe)
    parent_klines = []
    if parent_tf:
        parent_cpd = _CANDLES_PER_DAY.get(parent_tf, 24)
        parent_warmup_days = int(_WARMUP_CANDLES / parent_cpd) + 5
        parent_start = start - timedelta(days=parent_warmup_days)
        if adapter:
            await _ensure_klines_complete(symbol, parent_tf, parent_start, end, ds, adapter, tolerance=0.005)
        parent_klines = ds.get_klines_range(symbol, parent_tf, parent_start, end)

    if not klines:
        ds.close()
        logger.warning("无数据")
        return None

    # 根据策略名称选择策略类
    strategy_registry = {
        "bollinger_bands_paxg": BollingerBandsStrategy,
    }
    strategy_cls = strategy_registry.get(strategy_name, SupportResistanceStrategy)
    strategy = strategy_cls(strategy_name, params, config)
    engine = BacktestEngine()

    # 加载风控配置（与实时代码保持一致）
    config.load_risk()
    risk_manager = RiskManager(config.risk, ds)

    parent_lookback = params.get("parent_lookback", None)
    parent_windows = _align_parent_klines(klines, parent_klines, parent_lookback=parent_lookback)

    # 加载 funding rate 缓存
    funding_records = _load_funding_rates(symbol)

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
        ts_epoch = kline["timestamp"].timestamp()
        # 匹配当前 K 线时间对应的 funding rate
        funding_rate = None
        if funding_records:
            funding_rate = _match_funding_rate(int(ts_epoch * 1000), funding_records)
        signals = strategy.on_bar(symbol, timeframe, window, engine.positions.values(), parent_klines=parent_window, grandparent_klines=None, funding_rate=funding_rate)
        price_map = {symbol: kline["close"]}
        engine._record_equity(kline["timestamp"], price_map)
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
            if sig.action == "close":
                existing = engine.positions.get(symbol)
                if existing:
                    engine._close_position(symbol, sig.price, sig.metadata.get("reason", "close_signal"), kline)
                continue
            last = last_signal_time.get(sig.symbol, 0)
            if ts_epoch - last < cooldown_seconds:
                continue
            # 风控检查（与实时代码保持一致）
            risk_result = risk_manager.check_and_adjust(sig)
            if not risk_result["approved"]:
                continue
            sig.leverage = risk_result["leverage"]
            engine.execute_signal(symbol, sig.signal_type, sig.price, sig.stop_loss, sig.leverage, kline["timestamp"], metadata=sig.metadata)
            last_signal_time[sig.symbol] = ts_epoch
            break  # 每根 K 线只执行一个信号

    if engine.positions:
        for sym in list(engine.positions.keys()):
            engine._close_position(sym, klines[-1]["close"], "end_of_data", klines[-1])

    engine.strategy = strategy

    # 同步 filter summary 到数据库（仅用于回测期间日志打印，结束后自清）
    if hasattr(strategy, "get_filter_summary"):
        filter_summary = strategy.get_filter_summary()
        for direction, stats in filter_summary.items():
            if stats.get("checked", 0) > 0:
                ds.upsert_filter_stats(strategy.name, symbol, timeframe, direction, stats)
        # 回测数据用完即弃，避免污染 status 命令的实盘统计
        with ds._get_conn().cursor() as cur:
            cur.execute(
                "DELETE FROM filter_stats WHERE strategy_name = %s",
                (strategy.name,),
            )

    ds.close()
    return engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--strategy", default=None, help="指定策略名称（可选）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=timezone.utc)

    config = ConfigLoader()
    config.load_strategies()
    params = None
    strategy_name = "default"

    # 如果指定了策略名称，优先使用
    if args.strategy:
        for s in config.strategies.strategies:
            if s.name == args.strategy and args.symbol in s.symbols and args.timeframe in s.timeframes:
                params = s.params
                strategy_name = s.name
                break
    else:
        # 只选择启用的策略
        for s in config.strategies.strategies:
            if args.symbol in s.symbols and args.timeframe in s.timeframes and s.enabled:
                params = s.params
                strategy_name = s.name
                break
    if not params:
        logger.error("未找到 %s %s 的启用策略，请使用 --strategy 指定或在配置中启用策略",
                     args.symbol, args.timeframe)
        return

    # 创建交易所适配器（用于 K 线数据验证补全）
    exchange_cfg = config.load_static()
    adapter = BinanceAdapter(
        api_key=exchange_cfg.api_key,
        api_secret=exchange_cfg.api_secret,
        sandbox=exchange_cfg.sandbox,
        default_type=exchange_cfg.default_type,
        proxy=exchange_cfg.proxy or None,
    )
    adapter.connect()

    engine = asyncio.run(run_backtest(args.symbol, args.timeframe, start, end, params,
                                       strategy_name=strategy_name, adapter=adapter))
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

    # ---- 过滤统计汇总 ----
    if hasattr(engine, "strategy"):
        filter_summary = engine.strategy.get_filter_summary()
        print("\n=== FILTER SUMMARY ===")
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
            print(f"  FR hard block rejected:       {stats.get('fr_hard_block', 0):>8} ({stats.get('fr_hard_block', 0)/checked*100:.1f}%)")
            print(f"  FR penalty applied:           {stats.get('fr_penalty', 0):>8} ({stats.get('fr_penalty', 0)/checked*100:.1f}%)")
            print(f"  Add position rejected:        {stats['add']:>8} ({stats['add']/checked*100:.1f}%)")
            print(f"  Add limit rejected:           {stats.get('add_limit', 0):>8} ({stats.get('add_limit', 0)/checked*100:.1f}%)")
            print(f"  Add quality rejected:         {stats.get('add_quality', 0):>8} ({stats.get('add_quality', 0)/checked*100:.1f}%)")
            print(f"  Overall rejection rate:       {rejected:>8} ({rejected/checked*100:.1f}%)")
        print()

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
    print(f"{'序号':<4} {'方向':<5} {'入场价':<10} {'出场价':<10} {'杠杆':<5} {'净利':<10} {'parent':<10} {'bias':<14} {'原因':<15} {'入场时间':<12} {'出场时间':<12}")
    print("-" * 128)
    for i, t in enumerate(engine.trades, 1):
        entry_ts = t["entry_time"].strftime("%m-%d %H:%M") if t.get("entry_time") else ""
        exit_ts = t["exit_time"].strftime("%m-%d %H:%M") if t["exit_time"] else ""
        pt = t.get("parent_trend") or "None"
        bias = t.get("bias") or "None"
        print(f"{i:<4} {t['side']:<5} {t['entry_price']:<10.2f} {t['exit_price']:<10.2f} {t['leverage']:<5}x {t['net_pnl']:<+10.2f} {pt:<10} {bias:<14} {t['reason']:<15} {entry_ts:<12} {exit_ts:<12}")


if __name__ == "__main__":
    main()
