#!/usr/bin/env python3
"""历史 K 线数据拉取脚本。

将 Binance 历史 K 线数据拉取到本地 PostgreSQL 数据库，供回测使用。
支持自动分页（ccxt 单次最多 1000 根）和断点续传。

用法:
    python scripts/fetch_history.py --symbol BTC/USDT --timeframe 5m --days 30
    python scripts/fetch_history.py --all --days 60
    python scripts/fetch_history.py --all --days 7   # 快速测试
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.data_store import DataStore, KlineRecord
from quant_trader.exchange.binance_adapter import BinanceAdapter

logger = logging.getLogger(__name__)


async def fetch_symbol_timeframe(adapter: BinanceAdapter, data_store: DataStore,
                                  symbol: str, timeframe: str, days: int):
    """拉取单个币种单个周期的历史数据，分页处理。"""
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    logger.info("开始拉取 %s %s, 从 %s 到 %s",
                symbol, timeframe, start_time.isoformat(), end_time.isoformat())

    all_klines: list[dict] = []
    since = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    page = 0

    while True:
        page += 1
        batch = await adapter.fetch_ohlcv(symbol, timeframe, limit=1000, since=since)
        if not batch:
            logger.info("%s %s 第 %d 页无数据，结束", symbol, timeframe, page)
            break

        all_klines.extend(batch)
        last_ts = batch[-1]["timestamp"]

        logger.info("%s %s 第 %d 页: %d 根, 时间范围 %s ~ %s",
                    symbol, timeframe, page, len(batch),
                    datetime.fromtimestamp(batch[0]["timestamp"] / 1000, tz=timezone.utc).isoformat(),
                    datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).isoformat())

        # 超出目标结束时间或已拉完
        if last_ts >= end_ms or len(batch) < 1000:
            break

        since = last_ts + 1  # 避免重复

    # 过滤超出时间范围的数据
    all_klines = [k for k in all_klines if start_time.timestamp() * 1000 <= k["timestamp"] <= end_ms]

    if not all_klines:
        logger.warning("%s %s 未拉取到任何数据", symbol, timeframe)
        return 0

    # 写入数据库（ON CONFLICT DO NOTHING 自动去重）
    records = [
        KlineRecord(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.fromtimestamp(k["timestamp"] / 1000, tz=timezone.utc),
            open=k["open"],
            high=k["high"],
            low=k["low"],
            close=k["close"],
            volume=k["volume"],
        )
        for k in all_klines
    ]

    inserted = data_store.insert_klines(records)
    logger.info("%s %s 拉取完成: 共 %d 根, 写入 %d 根(新数据)",
                symbol, timeframe, len(all_klines), inserted)
    return len(all_klines)


async def main():
    parser = argparse.ArgumentParser(description="拉取历史 K 线数据到本地数据库")
    parser.add_argument("--symbol", help="交易对, 如 BTC/USDT")
    parser.add_argument("--timeframe", help="K 线周期, 如 5m, 1h, 4h")
    parser.add_argument("--days", type=int, default=30, help="拉取天数(默认30)")
    parser.add_argument("--all", action="store_true", help="拉取 strategies.yaml 中配置的所有币种和周期")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

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

    adapter = BinanceAdapter(
        api_key=exchange_cfg.api_key,
        api_secret=exchange_cfg.api_secret,
        sandbox=exchange_cfg.sandbox,
        default_type=exchange_cfg.default_type,
        proxy=exchange_cfg.proxy or None,
    )
    adapter.connect()

    tasks = []

    if args.all:
        for strategy in config.strategies.strategies:
            if not strategy.enabled:
                continue
            for symbol in strategy.symbols:
                for tf in strategy.timeframes:
                    tasks.append(fetch_symbol_timeframe(adapter, data_store, symbol, tf, args.days))
    elif args.symbol and args.timeframe:
        tasks.append(fetch_symbol_timeframe(adapter, data_store, args.symbol, args.timeframe, args.days))
    else:
        print("用法: python fetch_history.py --all --days 30")
        print("      python fetch_history.py --symbol BTC/USDT --timeframe 5m --days 30")
        return

    results = await asyncio.gather(*tasks, return_exceptions=True)

    success = sum(1 for r in results if isinstance(r, int) and r > 0)
    failed = [r for r in results if isinstance(r, Exception)]

    logger.info("全部完成: %d/%d 成功", success, len(tasks))
    for e in failed:
        logger.error("拉取失败: %s", e)

    await adapter.close()
    data_store.close()


if __name__ == "__main__":
    asyncio.run(main())
