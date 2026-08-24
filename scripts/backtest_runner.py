#!/usr/bin/env python3
"""统一回测入口（基于 PaperTradingEngine）。

废弃旧的 scripts/backtest.py 和 scripts/backtest_range.py。
本脚本复用生产 PaperTradingEngine + signal_router，确保回测与实盘行为一致。

用法:
    # 基础用法
    python scripts/backtest_runner.py \\
        --symbol BTC/USDT --timeframe 5m \\
        --start 2026-01-01 --end 2026-06-30 \\
        --strategy support_resistance_btc

    # 指定回测时间范围与初始资金
    python scripts/backtest_runner.py \\
        --symbol ETH/USDT --timeframe 1h \\
        --start 2026-01-01 --end 2026-06-30 \\
        --strategy support_resistance_eth \\
        --initial-balance 50000

输出:
    - 基础指标：收益率、最大回撤、胜率、profit factor
    - 归因指标：按 reason / entry_seq / 方向 聚合的 PNL 贡献
    - 本次回测的 run_id（用于后续 analyze_attribution.py 查询）
"""

import argparse
import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.data_store import KlineRecord
from quant_trader.core.backtest_data_store import BacktestDataStore
from quant_trader.core.backtest_kline_driver import BacktestKlineDriver, load_funding_rates
from quant_trader.core.signal_router import handle_signal
from quant_trader.exchange.binance_adapter import BinanceAdapter
from quant_trader.exchange.paper_trading import PaperTradingEngine
from quant_trader.core.time_utils import bar_close_time
from quant_trader.main import PARENT_TIMEFRAME, TIMEFRAME_SECONDS
from quant_trader.risk.manager import RiskManager
from quant_trader.strategies.s_r_levels import SupportResistanceStrategy
from quant_trader.strategies.bollinger_bands import BollingerBandsStrategy


logger = logging.getLogger(__name__)


STRATEGY_REGISTRY = {
    "support_resistance": SupportResistanceStrategy,
    "support_resistance_btc_5m": SupportResistanceStrategy,
    "support_resistance_btc_1h": SupportResistanceStrategy,
    "support_resistance_eth": SupportResistanceStrategy,
    "support_resistance_eth_5m": SupportResistanceStrategy,
    "support_resistance_sol": SupportResistanceStrategy,
    "support_resistance_paxg": SupportResistanceStrategy,
    "bollinger_bands_paxg": BollingerBandsStrategy,
}


# ============================================================
# run_id 生成
# ============================================================

def generate_run_id(symbol: str, timeframe: str, params: dict) -> str:
    """生成 run_id：bt_{timestamp}_{symbol}_{timeframe}_{param_hash8}。

    例: bt_20260812_143000_BTC-USDT_5m_a3f2e1d8
    """
    ts_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    symbol_safe = symbol.replace("/", "-").replace(":", "-")
    # param_hash: 对参数 JSON 稳定序列化后取 md5 前 8 位
    params_json = json.dumps(params, sort_keys=True, default=str)
    param_hash = hashlib.md5(params_json.encode("utf-8")).hexdigest()[:8]
    return f"bt_{ts_str}_{symbol_safe}_{timeframe}_{param_hash}"


# ============================================================
# K 线完整性检查（复用旧脚本逻辑）
# ============================================================

async def _fetch_klines_from_api(adapter: BinanceAdapter, ds, symbol: str,
                                  timeframe: str, start: datetime, end: datetime):
    """从交易所 API 分页拉取 K 线写入 DB（幂等）。"""
    since = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    total_fetched = 0

    while True:
        batch = await adapter.fetch_ohlcv(symbol, timeframe, limit=1000, since=since)
        if not batch:
            break

        records = [
            KlineRecord(
                symbol=symbol, timeframe=timeframe,
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


async def ensure_klines_complete(symbol: str, timeframe: str,
                                  start: datetime, end: datetime,
                                  ds, adapter: BinanceAdapter,
                                  tolerance: float = 0.001,
                                  max_retries: int = 5):
    """验证 K 线数据完整性，不完整时从 API 补全。

    注：Binance 不会返回当前未收盘的 K 线，所以期望值按 min(end, now) 计算，
    避免因未来时间导致永远缺失。
    """
    # 当前时间截断：end 不能超过 now（Binance 无未收盘 K 线）
    now = datetime.now(tz=timezone.utc)
    effective_end = min(end, now)

    interval = TIMEFRAME_SECONDS.get(timeframe, 300)
    expected = int((effective_end - start).total_seconds() / interval) + 1
    actual = ds.count_klines(symbol, timeframe, start, end)
    missing_rate = (expected - actual) / expected if expected > 0 else 0

    if missing_rate <= tolerance:
        logger.info("K线数据完整 %s %s: %d/%d (缺失率 %.2f%%, end 截断到 now)",
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
                    f"K线数据补全失败 {symbol} {timeframe}: 缺失率={missing_rate*100:.2f}%"
                ) from e
            continue

        new_actual = ds.count_klines(symbol, timeframe, start, end)
        new_missing = (expected - new_actual) / expected if expected > 0 else 0
        if new_missing <= tolerance:
            logger.info("补全成功 %s %s: %d/%d (缺失率 %.2f%%)",
                        symbol, timeframe, new_actual, expected, new_missing * 100)
            return

    raise RuntimeError(f"K线数据补全失败 {symbol} {timeframe}: 重试 {max_retries} 次仍不完整")


# ============================================================
# 回测主循环
# ============================================================

def run_backtest_loop(
    symbol: str,
    timeframe: str,
    strategy,
    paper_engine: PaperTradingEngine,
    risk_manager: RiskManager,
    backtest_ds: BacktestDataStore,
    kline_driver: BacktestKlineDriver,
    start: datetime,
    end: datetime,
    funding_records: list[dict] | None = None,
    parent_lookback: int | None = None,
    signal_cooldown_seconds: int = 220,
    window_size: int = 150,
) -> dict:
    """驱动 K 线迭代器 + signal_router 的核心循环。

    返回:
        含 'equity_curve' 和 'trades' 的统计字典
    """
    parent_tf = PARENT_TIMEFRAME.get(timeframe)

    equity_curve: list[tuple[datetime, float]] = []
    last_signal_time: float = 0.0

    for event in kline_driver.iter_klines(
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=end,
        parent_timeframe=parent_tf,
        parent_lookback=parent_lookback,
        funding_records=funding_records,
    ):
        # 0. 设置当前业务时间为 bar 收盘时间
        bar_close = bar_close_time(event.kline["timestamp"], timeframe)
        paper_engine.set_current_time(bar_close)

        # 1. 价格更新 → 触发止损检查
        paper_engine.update_prices(symbol, event.kline["close"], backtest_ds)

        # 2. 记录权益曲线
        equity = paper_engine.get_total_equity()
        equity_curve.append((event.kline["timestamp"], equity))

        # 3. 策略生成信号
        signals = strategy.on_bar(
            symbol, timeframe, event.window,
            paper_engine.positions.values(),
            parent_klines=event.parent_window,
            grandparent_klines=None,
            funding_rate=event.funding_rate,
        )

        # 4. 路由信号
        # 4a. 退出管理信号（update_stop / partial_close）优先执行，无冷却期
        #     退出管理与开仓过滤解耦（v1.6.0），需每根 bar 无条件执行，
        #     否则已有仓位的止损上移/部分止盈可能被延迟或跳过
        ts_epoch = event.kline["timestamp"].timestamp()
        for sig in signals:
            if sig.symbol != symbol:
                continue
            if sig.action in ("update_stop", "partial_close"):
                sig.timestamp = bar_close
                handle_signal(
                    signal=sig,
                    paper_engine=paper_engine,
                    data_store=backtest_ds,
                    risk_manager=risk_manager,
                    mode="paper",
                )
        # 4b. 开仓/全平信号，保持冷却期 + 每根 K 线只执行一个
        for sig in signals:
            if sig.symbol != symbol:
                continue
            if sig.action in ("update_stop", "partial_close"):
                continue  # 退出信号已在 4a 处理
            if ts_epoch - last_signal_time < signal_cooldown_seconds:
                continue
            sig.timestamp = bar_close
            handle_signal(
                signal=sig,
                paper_engine=paper_engine,
                data_store=backtest_ds,
                risk_manager=risk_manager,
                mode="paper",
            )
            last_signal_time = ts_epoch
            break  # 每根 K 线只执行一个开仓信号

    # 5. 回测结束：强制平仓所有 open positions
    if paper_engine.positions:
        last_kline = event.kline  # noqa: F821 -- event 在循环里赋值
        for sym in list(paper_engine.positions.keys()):
            paper_engine.close_position(
                sym, last_kline["close"], backtest_ds,
                order_type="end_of_data", signal_id=None,
                trigger_reason="end_of_backtest",
            )

    return {"equity_curve": equity_curve}


# ============================================================
# 报告生成
# ============================================================

def print_basic_report(paper_engine: PaperTradingEngine, equity_curve: list, run_id: str):
    """打印基础绩效指标。"""
    summary = paper_engine.get_summary()

    # 计算最大回撤
    max_dd = 0.0
    peak = paper_engine.initial_balance
    for _, equity in equity_curve:
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    print("\n" + "=" * 60)
    print(f"回测报告  run_id: {run_id}")
    print("=" * 60)
    print(f"初始资金:           {summary['initial_balance']:,.2f} USDT")
    print(f"期末资金:           {summary['balance']:,.2f} USDT")
    print(f"总权益:             {summary['total_equity']:,.2f} USDT")
    print(f"收益率:             {summary['return_pct']:+.2f}%")
    print(f"最大回撤:           {max_dd:.2f}%")
    print(f"总交易次数:         {summary['total_trades']}")
    print(f"胜率:               {summary['win_rate']:.1f}%")
    print(f"总盈亏:             {summary['total_pnl']:+.2f} USDT")
    print(f"总手续费:           {summary['total_fees']:.2f} USDT")
    print("=" * 60)


def print_attribution_report(backtest_ds: BacktestDataStore):
    """打印归因指标（按事件类型×触发原因、entry_seq、方向聚合）。"""
    print("\n── 归因指标（按事件类型 × 触发原因） ──────────────────")
    by_reason = backtest_ds.get_attribution_by_reason()
    if not by_reason:
        print("  (无归因数据)")
    else:
        print(f"  {'reason':<16} {'触发原因':<18} {'笔数':<6} {'总PNL':>12} {'胜率%':>8}")
        print("  " + "-" * 68)
        for row in by_reason:
            trigger = row.get("trigger_reason") or "-"
            print(f"  {row['reason']:<16} {trigger:<18} {row['count']:<6} "
                  f"{float(row['total_pnl']):>+12.2f} {float(row['win_rate']):>7.1f}%")

    print("\n── 归因指标（按入场序号） ──────────────────────────")
    by_seq = backtest_ds.get_attribution_by_entry_seq()
    if not by_seq:
        print("  (无归因数据)")
    else:
        print(f"  {'seq':<6} {'笔数':<6} {'总PNL':>12} {'平均PNL':>12} {'胜率%':>8}")
        print("  " + "-" * 56)
        for row in by_seq:
            seq_label = f"{row['seq']}{' (开仓)' if row['seq'] == 1 else ' (加仓)'}"
            print(f"  {seq_label:<10} {row['count']:<6} {row['total_pnl']:>+12.2f} "
                  f"{row['avg_pnl']:>+12.2f} {row['win_rate']:>7.1f}%")

    print("\n── 归因指标（按信号方向） ──────────────────────────")
    by_pos = backtest_ds.get_attribution_by_position()
    if by_pos:
        # 聚合 by side
        side_stats: dict[str, dict] = {}
        for row in by_pos:
            side = row.get("side", "?")
            if side not in side_stats:
                side_stats[side] = {"count": 0, "pnl_sum": 0.0}
            side_stats[side]["count"] += 1
            pnl = row.get("total_attributed_pnl") or 0
            side_stats[side]["pnl_sum"] += float(pnl)
        print(f"  {'方向':<6} {'持仓数':<8} {'归因PNL':>12}")
        print("  " + "-" * 36)
        for side, stats in side_stats.items():
            print(f"  {side:<6} {stats['count']:<8} {stats['pnl_sum']:>+12.2f}")
    else:
        print("  (无归因数据)")
    print()


# ============================================================
# 主函数
# ============================================================

async def run(args):
    """执行回测。"""
    config = ConfigLoader()
    config.load_strategies()
    config.load_risk()
    backtest_cfg = config.load_backtest()
    exchange_cfg = config.load_static()

    # 解析时间范围
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, tzinfo=timezone.utc,
    )

    # 查找策略配置
    strategy_cfg = None
    for s in config.strategies.strategies:
        if args.strategy:
            if s.name == args.strategy and args.symbol in s.symbols and args.timeframe in s.timeframes:
                strategy_cfg = s
                break
        else:
            if args.symbol in s.symbols and args.timeframe in s.timeframes and s.enabled:
                strategy_cfg = s
                break

    if not strategy_cfg:
        logger.error(
            "未找到匹配的策略配置: symbol=%s timeframe=%s strategy=%s",
            args.symbol, args.timeframe, args.strategy or "(auto)",
        )
        return 1

    strategy_cls = STRATEGY_REGISTRY.get(strategy_cfg.name)
    if not strategy_cls:
        logger.error("未知策略: %s", strategy_cfg.name)
        return 1

    # 生成 run_id
    run_id = generate_run_id(args.symbol, args.timeframe, strategy_cfg.params)
    logger.info("本次回测 run_id: %s", run_id)

    # 初始化回测 DB
    backtest_ds = BacktestDataStore(
        host=backtest_cfg.db_host, port=backtest_cfg.db_port,
        dbname=backtest_cfg.db_name, user=backtest_cfg.db_user,
        password=backtest_cfg.db_password,
        run_id=run_id,
    )
    backtest_ds.connect()

    # 初始化交易所适配器（用于 K 线数据补全）
    adapter = BinanceAdapter(
        api_key=exchange_cfg.api_key, api_secret=exchange_cfg.api_secret,
        sandbox=exchange_cfg.sandbox, default_type=exchange_cfg.default_type,
        proxy=exchange_cfg.proxy or None,
    )
    adapter.connect()

    # 确保当前周期数据完整
    await ensure_klines_complete(
        args.symbol, args.timeframe, start, end, backtest_ds, adapter,
        tolerance=backtest_cfg.current_tolerance,
        max_retries=backtest_cfg.max_retries,
    )

    # 确保 parent 周期数据完整
    parent_tf = PARENT_TIMEFRAME.get(args.timeframe)
    if parent_tf:
        parent_start = start - timedelta(days=5)  # parent 预热
        await ensure_klines_complete(
            args.symbol, parent_tf, parent_start, end, backtest_ds, adapter,
            tolerance=backtest_cfg.parent_tolerance,
            max_retries=backtest_cfg.max_retries,
        )
    adapter.close()

    # 初始化引擎、风控、策略
    paper_engine = PaperTradingEngine(
        initial_balance=args.initial_balance,
        default_type=backtest_cfg.default_type,
    )
    risk_manager = RiskManager(config.risk, backtest_ds)
    strategy = strategy_cls(strategy_cfg.name, strategy_cfg.params, config)

    # 加载 funding rate 缓存
    funding_records = load_funding_rates(args.symbol, backtest_cfg.funding_rate_cache_dir)

    # 初始化 K 线驱动器
    parent_lookback = strategy_cfg.params.get("parent_lookback")
    window_size = strategy_cfg.params.get("lookback", 150)
    kline_driver = BacktestKlineDriver(
        data_store=backtest_ds, adapter=None, window_size=window_size,
    )

    # 运行回测
    logger.info("开始回测 %s %s %s ~ %s", args.symbol, args.timeframe, args.start, args.end)
    result = run_backtest_loop(
        symbol=args.symbol, timeframe=args.timeframe,
        strategy=strategy, paper_engine=paper_engine,
        risk_manager=risk_manager, backtest_ds=backtest_ds,
        kline_driver=kline_driver, start=start, end=end,
        funding_records=funding_records,
        parent_lookback=parent_lookback,
        window_size=window_size,
    )

    # 输出报告
    print_basic_report(paper_engine, result["equity_curve"], run_id)
    print_attribution_report(backtest_ds)

    print(f"\n归因分析命令：")
    print(f"  python scripts/analyze_attribution.py --run-id {run_id}")

    backtest_ds.close()
    return 0


def main():
    parser = argparse.ArgumentParser(description="统一回测入口（基于 PaperTradingEngine）")
    parser.add_argument("--symbol", required=True, help="交易对，如 BTC/USDT")
    parser.add_argument("--timeframe", required=True, help="K 线周期，如 5m、1h、4h")
    parser.add_argument("--start", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--strategy", default=None,
                        help="策略名称（与 strategies.yaml 中一致）")
    parser.add_argument("--initial-balance", type=float, default=100000.0,
                        help="初始资金（USDT），默认 100000")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    rc = asyncio.run(run(args))
    sys.exit(rc)


if __name__ == "__main__":
    main()
