#!/usr/bin/env python3
"""历史 Funding Rate 拉取脚本。

直接调用 Binance U 本位合约公开 API (/fapi/v1/fundingRate)，无需身份验证。
Funding rate 每 8 小时结算一次（00:00/08:00/16:00 UTC），数据量极小。

用法:
    python scripts/fetch_funding_history.py --symbol BTC/USDT --days 60
    python scripts/fetch_funding_history.py --all --days 60
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "quant_trader" / "data" / "funding_rates"
BASE_URL = "https://fapi.binance.com"


def _normalize_symbol(symbol: str) -> str:
    """将交易对名称转为文件名安全的格式。"""
    return symbol.replace("/", "_").replace(":", "_")


def _to_binance_symbol(symbol: str) -> str:
    """将 ccxt 格式 BTC/USDT 转为 Binance 格式 BTCUSDT。"""
    return symbol.replace("/", "").replace(":", "")


def _load_cache(symbol: str) -> dict | None:
    """读取本地缓存的 funding rate 数据。"""
    path = DATA_DIR / f"{_normalize_symbol(symbol)}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("读取缓存失败 %s: %s", path, e)
        return None


def _save_cache(symbol: str, records: list[dict]):
    """保存 funding rate 数据到本地 JSON 缓存。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{_normalize_symbol(symbol)}.json"

    data = {
        "symbol": symbol,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("已保存 %s funding rate 缓存: %d 条 -> %s", symbol, len(records), path)


def fetch_symbol_funding(symbol: str, days: int, proxy: str | None = None) -> int:
    """拉取单个币种的历史 funding rate。

    返回拉取到的记录数。
    """
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=days)

    logger.info("开始拉取 %s funding rate, 从 %s 到 %s",
                symbol, start_time.isoformat(), end_time.isoformat())

    since_ms = int(start_time.timestamp() * 1000)
    end_ms = int(end_time.timestamp() * 1000)
    bsymbol = _to_binance_symbol(symbol)

    proxies = {"http": proxy, "https": proxy} if proxy else None
    records = []
    current_start = since_ms

    # 分页拉取（每页最多 1000 条）
    while current_start < end_ms:
        params = {
            "symbol": bsymbol,
            "startTime": current_start,
            "endTime": end_ms,
            "limit": 1000,
        }

        try:
            resp = requests.get(
                f"{BASE_URL}/fapi/v1/fundingRate",
                params=params,
                proxies=proxies,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("拉取失败 %s: %s", symbol, e)
            break

        if not data:
            break

        for r in data:
            ts_ms = r.get("fundingTime", 0)
            rate = float(r.get("fundingRate", 0))
            records.append({
                "rate_pct": round(rate * 100, 4),
                "timestamp_ms": ts_ms,
            })

        # 下一页从最后一条的 fundingTime + 1 开始
        last_ts = data[-1]["fundingTime"]
        if last_ts <= current_start:
            break
        current_start = last_ts + 1

        logger.debug("%s 第 %d 页: %d 条, 时间至 %s",
                     symbol, len(records) // 1000 + 1, len(data),
                     datetime.fromtimestamp(last_ts / 1000, tz=timezone.utc).isoformat())

    if not records:
        logger.warning("%s 未拉取到 funding rate 数据", symbol)
        return 0

    # 去重并按时间排序（旧→新）
    unique = {r["timestamp_ms"]: r for r in records}
    records = sorted(unique.values(), key=lambda x: x["timestamp_ms"])

    # 过滤时间范围
    records = [r for r in records if since_ms <= r["timestamp_ms"] <= end_ms]

    # 合并已有缓存（增量更新）
    cached = _load_cache(symbol)
    if cached:
        existing = {r["timestamp_ms"]: r for r in cached.get("records", [])}
        for r in records:
            existing[r["timestamp_ms"]] = r
        merged = sorted(existing.values(), key=lambda x: x["timestamp_ms"])
        logger.info("%s 合并缓存: 新增 %d 条, 总计 %d 条",
                    symbol, len(records), len(merged))
        records = merged

    _save_cache(symbol, records)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description="拉取历史 Funding Rate 到本地 JSON 缓存")
    parser.add_argument("--symbol", help="交易对, 如 BTC/USDT")
    parser.add_argument("--days", type=int, default=60, help="拉取天数(默认60)")
    parser.add_argument("--all", action="store_true", help="拉取 strategies.yaml 中配置的所有币种")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    config = ConfigLoader()
    exchange_cfg = config.load_static()
    config.load_strategies()

    proxy = exchange_cfg.proxy or None

    symbols = set()
    if args.all:
        for strategy in config.strategies.strategies:
            if not strategy.enabled:
                continue
            for symbol in strategy.symbols:
                symbols.add(symbol)
    elif args.symbol:
        symbols.add(args.symbol)
    else:
        print("用法: python fetch_funding_history.py --all --days 60")
        print("      python fetch_funding_history.py --symbol BTC/USDT --days 60")
        return

    success = 0
    for symbol in sorted(symbols):
        try:
            count = fetch_symbol_funding(symbol, args.days, proxy)
            if count > 0:
                success += 1
        except Exception as e:
            logger.error("拉取失败 %s: %s", symbol, e)

    logger.info("全部完成: %d/%d 币种成功", success, len(symbols))


if __name__ == "__main__":
    main()
