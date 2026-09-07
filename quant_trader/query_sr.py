"""手动交易 S/R 查询工具。

通过 HTTP 调用引擎查询服务，获取策略视角下的 S/R 分析结果，
格式化为自然语言纯文本输出。

当引擎未配置请求的币种/周期时，自动从 Binance 拉取数据并本地计算。

用法:
    # 开盘前查询（默认）
    python -m quant_trader.query_sr --symbol SOL --timeframe 15m

    # 持仓监控
    python -m quant_trader.query_sr --symbol SOL --timeframe 15m \
        --position-mode --entry-price 67.0 --side long --qty 10
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

import aiohttp
import yaml

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.time_utils import DEFAULT_QUERY_TIMEFRAMES, validate_timeframes
from quant_trader.exchange.binance_adapter import BinanceAdapter
from quant_trader.technical.support_resistance import analyze_sr_levels, compute_position_analysis


API_PATH = "/api/v1/query"

# 本地缓存: (symbol, timeframe) -> (timestamp, data)
_local_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_CACHE_TTL = 60  # 缓存有效期（秒）


def _load_manual_config() -> dict:
    """加载手动交易辅助配置文件。"""
    config_path = Path(__file__).parent / "config" / "manual_config.yaml"
    if not config_path.exists():
        return {}
    try:
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _query_timeframes() -> list[str]:
    """返回手动查询允许的周期清单（from manual_config.query_timeframes，未配置时回退默认）。"""
    tfs = _load_manual_config().get("query_timeframes")
    if not tfs:
        return list(DEFAULT_QUERY_TIMEFRAMES)
    validate_timeframes(tfs, where="manual_config.query_timeframes")
    return list(tfs)


def _normalize_symbol(symbol: str) -> str:
    """标准化交易对符号，自动补全 /USDT 后缀。"""
    symbol = symbol.strip().upper()
    if not symbol.endswith("/USDT"):
        symbol = f"{symbol}/USDT"
    return symbol


def _stars(quality: float) -> str:
    """根据质量分返回星级字符串。"""
    stars = int(quality // 2)
    return "★" * min(stars, 5) + "☆" * max(0, 5 - stars)


_VP_DETAIL_ZH = {
    "POC_near": "POC近区",
    "POC_far": "POC远区",
    "HVN_near": "HVN近区",
    "HVN_far": "HVN远区",
    "VAH_near": "VAH近区",
    "VAL_near": "VAL近区",
    "VA_inner": "Value Area内",
    "LVN": "LVN",
    "none": "",
}


def _translate_vp_detail(detail: str) -> str:
    """翻译 vp_detail 标签为中文。"""
    return _VP_DETAIL_ZH.get(detail, detail)


def _format_pre_trade(data: dict) -> str:
    """格式化开盘前查询结果。"""
    symbol = data["symbol"]
    timeframe = data["timeframe"]
    current_price = data["current_price"]
    timestamp = data.get("timestamp", "")
    trend = data["trend"]
    parent_trend = data.get("parent_trend")
    bias = data["bias"]
    adx_val = data["adx"]
    rsi_val = data["rsi"]
    atr_val = data["atr"]
    volume_ratio = data["volume_ratio"]
    is_ranging = data["is_ranging"]
    levels = data["levels"]
    suggested = data["suggested_stops"]

    lines = []
    lines.append(f"\n=== {symbol} {timeframe} 最新 S/R 查询 ===")

    # 时间价格
    if timestamp:
        try:
            # 尝试解析为Unix时间戳
            timestamp_float = float(timestamp)
            # 自动检测时间戳单位（秒或毫秒）
            if timestamp_float > 1e12:  # 毫秒级时间戳
                timestamp_float /= 1000
            # 检查时间戳有效性（2020-2030年）
            if 1577811600 < timestamp_float < 1893456000:  # 2020-2030年范围
                time_struct = time.localtime(timestamp_float)
                ts_str = time.strftime("%Y年%m月%d日%H时%M分", time_struct)
            else:
                # 时间戳超出合理范围，直接使用字符串
                ts_str = timestamp
        except (ValueError, TypeError):
            # 如果不是数字时间戳，直接使用字符串
            ts_str = timestamp
    else:
        ts_str = "当前"
    lines.append(f"时间: {ts_str} | 当前价格: {current_price:.2f} USDT")

    # 趋势信息
    trend_str = trend["trend"]
    if parent_trend:
        trend_str += f" ({parent_trend['trend']}上级确认)"
    trend_str += f" | ADX: {adx_val:.1f}"
    if is_ranging:
        trend_str += " (震荡)"
    else:
        trend_str += " (趋势)"

    lines.append(f"趋势偏向: {trend_str} | RSI: {rsi_val:.1f} | ATR(14): {atr_val:.2f} USDT")
    lines.append(f"成交量比: {volume_ratio:.2f}x | 偏向: {bias}")

    # 支撑位
    supports = sorted(levels["support"], key=lambda x: x["quality_score"], reverse=True)
    lines.append("\n支撑位 (由强到弱):")
    if supports:
        for s in supports:
            dist = (current_price - s["price"]) / current_price * 100
            lines.append(f"  {s['price']:.2f} (质量 {s['quality_score']:.1f} {_stars(s['quality_score'])})  距离 {dist:+.2f}%")
    else:
        lines.append("  (无)")

    # 压力位
    resistances = sorted(levels["resistance"], key=lambda x: x["quality_score"], reverse=True)
    lines.append("\n压力位 (由强到弱):")
    if resistances:
        for r in resistances:
            dist = (r["price"] - current_price) / current_price * 100
            lines.append(f"  {r['price']:.2f} (质量 {r['quality_score']:.1f} {_stars(r['quality_score'])})  距离 {dist:+.2f}%")
    else:
        lines.append("  (无)")

    # 建议止损位
    atr_mult = suggested["atr_multiplier"]
    long_stop = suggested.get("long_stop")
    short_stop = suggested.get("short_stop")

    lines.append(f"\n建议止损位 (手动缓冲版 ATR×{atr_mult}):")
    if long_stop:
        lines.append(f"  做多止损建议: {long_stop:.2f} (下方 {current_price - long_stop:.2f} USDT / {(current_price - long_stop) / current_price * 100:.2f}%)")
    if short_stop:
        lines.append(f"  做空止损建议: {short_stop:.2f} (上方 {short_stop - current_price:.2f} USDT / {(short_stop - current_price) / current_price * 100:.2f}%)")

    lines.append("\n操作建议: 结合K线形态、成交量和新闻综合判断。")
    lines.append("")

    return "\n".join(lines)


def _format_position(data: dict) -> str:
    """格式化持仓监控结果。"""
    symbol = data["symbol"]
    timeframe = data["timeframe"]
    current_price = data["current_price"]
    trend = data["trend"]
    adx_val = data["adx"]
    rsi_val = data["rsi"]
    levels = data.get("levels", {})
    pos = data.get("position_analysis")

    lines = []
    lines.append(f"\n=== {symbol} {timeframe} 持仓监控模式 ===")
    lines.append(f"当前价格: {current_price:.2f}")

    if pos:
        entry = pos["entry_price"]
        side = pos["side"]
        qty = pos["quantity"]
        pnl = pos["unrealized_pnl"]
        profit_pct = pos["profit_pct"]

        lines.append(f"入场价格: {entry:.2f} ({side})")
        lines.append(f"浮动盈亏: {profit_pct:+.2f}% ({pnl:+.2f} USDT)")

    # 趋势
    trend_str = trend["trend"] if trend else "N/A"
    lines.append(f"\n趋势偏向: {trend_str} | ADX: {adx_val:.1f} | RSI: {rsi_val:.1f}")

    if pos:
        entry = pos["entry_price"]
        side = pos["side"].lower()
        suggested_stop = pos.get("suggested_stop")
        atr_stop = pos.get("atr_stop_long") if side == "long" else pos.get("atr_stop_short")

        lines.append(f"\n=== 结构化建议 ===")

        # 止损
        if suggested_stop is not None:
            entry_dist_pct = (suggested_stop - entry) / entry * 100 if entry != 0 else 0
            current_dist_pct = (suggested_stop - current_price) / current_price * 100 if current_price != 0 else 0
            lines.append(f"推荐止损 (Structured SL): {suggested_stop:.2f} ({entry_dist_pct:+.2f}% from entry, {current_dist_pct:+.2f}% from current)")
        if atr_stop is not None:
            entry_dist_pct = (atr_stop - entry) / entry * 100 if entry != 0 else 0
            lines.append(f"ATR 止损: {atr_stop:.2f} ({entry_dist_pct:+.2f}% from entry)")

        # 止盈
        for tp_key, label in [("tp1", "推荐止盈 (Structured TP1)"), ("tp2", "推荐止盈 (Structured TP2)")]:
            tp_price = pos.get(tp_key)
            if tp_price is not None:
                tp_source = pos.get(f"{tp_key}_source", "")
                entry_dist_pct = (tp_price - entry) / entry * 100 if entry != 0 else 0
                source_tag = f" [{tp_source.upper()}]" if tp_source == "atr" else ""
                lines.append(f"{label}: {tp_price:.2f} ({entry_dist_pct:+.2f}% from entry){source_tag}")

    # 最近 S/R 位
    supports = sorted(levels.get("support", []), key=lambda x: x["quality_score"], reverse=True)
    resistances = sorted(levels.get("resistance", []), key=lambda x: x["quality_score"], reverse=True)

    lines.append("\n最近高质量支撑位:")
    if supports:
        for s in supports[:5]:
            vp_tag = _translate_vp_detail(s.get("vp_detail", ""))
            tag_str = f", {vp_tag}" if vp_tag else ""
            lines.append(f"- {s['price']:.2f} (quality {s['quality_score']:.1f}{tag_str})")
    else:
        lines.append("  (无)")

    lines.append("\n最近高质量阻力位:")
    if resistances:
        for r in resistances[:5]:
            vp_tag = _translate_vp_detail(r.get("vp_detail", ""))
            tag_str = f", {vp_tag}" if vp_tag else ""
            lines.append(f"- {r['price']:.2f} (quality {r['quality_score']:.1f}{tag_str})")
    else:
        lines.append("  (无)")

    lines.append("")
    return "\n".join(lines)


def format_output(data: dict, mode: str) -> str:
    """根据模式格式化输出。"""
    if mode == "position":
        return _format_position(data)
    return _format_pre_trade(data)


async def _fetch_local_analysis(
    symbol: str,
    timeframe: str,
    exchange_cfg,
    manual_config: dict,
    mode: str = "pre_trade",
    entry_price: float | None = None,
    side: str | None = None,
    qty: float | None = None,
):
    """当引擎无数据时，直接从 Binance 拉取 K 线并本地计算 S/R 分析。

    mode="position" 时同时计算结构化止损/止盈建议（与引擎端共用
    compute_position_analysis），保证持仓模式在兜底路径下输出一致。
    """
    # 检查本地缓存
    cache_key = (symbol, timeframe)
    if cache_key in _local_cache:
        cached_ts, cached_data = _local_cache[cache_key]
        if time.time() - cached_ts < _CACHE_TTL:
            return cached_data

    adapter = BinanceAdapter(
        api_key=exchange_cfg.api_key,
        api_secret=exchange_cfg.api_secret,
        sandbox=exchange_cfg.sandbox,
        default_type=exchange_cfg.default_type,
        proxy=exchange_cfg.proxy,
    )
    adapter.connect()

    try:
        # 拉取当前周期 K 线
        klines = await adapter.fetch_ohlcv(symbol, timeframe, limit=151)
        if not klines:
            return None
        # ccxt 返回旧→新，分析需要新→旧
        klines.reverse()

        # 尝试拉取上级周期 K 线
        from quant_trader.main import PARENT_TIMEFRAME
        parent_tf = PARENT_TIMEFRAME.get(timeframe)
        parent_klines = None
        if parent_tf:
            try:
                parent_klines_raw = await adapter.fetch_ohlcv(symbol, parent_tf, limit=151)
                if parent_klines_raw:
                    parent_klines_raw.reverse()
                    parent_klines = parent_klines_raw
            except Exception:
                pass

        # 本地计算 S/R 分析
        analysis = analyze_sr_levels(klines, parent_klines=parent_klines)
        if "error" in analysis:
            return None

        # 构建与引擎返回格式一致的数据
        current_price = analysis["current_price"]
        atr_val = analysis["atr"]
        atr_multiplier = manual_config.get("atr_multiplier", 5.5)
        show_levels = manual_config.get("show_levels", 5)
        long_stop = current_price - atr_val * atr_multiplier if atr_val > 0 else None
        short_stop = current_price + atr_val * atr_multiplier if atr_val > 0 else None

        # 按当前价过滤支撑位和压力位
        supports = [s for s in analysis["levels"]["support"] if s["price"] < current_price]
        resistances = [r for r in analysis["levels"]["resistance"] if r["price"] > current_price]

        data = {
            "symbol": symbol,
            "timeframe": timeframe,
            "mode": mode,
            "current_price": current_price,
            "timestamp": analysis.get("timestamp", ""),
            "trend": analysis["trend"],
            "parent_trend": analysis.get("parent_trend"),
            "bias": analysis["bias"],
            "adx": analysis["adx"],
            "rsi": analysis["rsi"],
            "atr": atr_val,
            "volume_ratio": analysis["volume_ratio"],
            "is_ranging": analysis["is_ranging"],
            "levels": {
                "support": supports[:show_levels],
                "resistance": resistances[:show_levels],
            },
            "suggested_stops": {
                "atr_multiplier": atr_multiplier,
                "long_stop": round(long_stop, 2) if long_stop else None,
                "short_stop": round(short_stop, 2) if short_stop else None,
            },
        }

        # 持仓模式：补齐结构化止损/止盈（与引擎端共用 compute_position_analysis）
        if mode == "position" and entry_price and side:
            try:
                entry = float(entry_price)
                qty_val = float(qty) if qty else 0.0
                data["position_analysis"] = compute_position_analysis(
                    analysis, current_price, entry, side, qty_val, manual_config,
                )
            except (ValueError, TypeError):
                pass

        # 仅 pre_trade 结果入缓存（position 模式因入场价不同每次都算）
        if mode == "pre_trade":
            _local_cache[cache_key] = (time.time(), data)
        return data
    finally:
        await adapter.close()


async def _fetch_query(host: str, port: int, params: dict):
    """发送查询请求。"""
    url = f"http://{host}:{port}{API_PATH}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                try:
                    error_data = await resp.json()
                except Exception:
                    error_data = {"error": error_text}
                exc = aiohttp.ClientResponseError(
                    resp.request_info,
                    resp.history,
                    status=resp.status,
                    message=error_data.get("error", error_text),
                    headers=resp.headers,
                )
                exc._error_data = error_data
                raise exc
            return await resp.json()


async def _main_async():
    parser = argparse.ArgumentParser(description="手动交易 S/R 查询工具")
    parser.add_argument("--symbol", required=True, help="交易对符号，如 SOL 或 SOL/USDT")
    parser.add_argument("--timeframe", default="15m", choices=_query_timeframes(), help="K线周期")
    parser.add_argument("--position-mode", action="store_true", help="持仓监控模式")
    parser.add_argument("--entry-price", type=float, help="入场价格（持仓模式必需）")
    parser.add_argument("--side", choices=["long", "short"], help="持仓方向（持仓模式必需）")
    parser.add_argument("--qty", type=float, help="持仓数量（持仓模式必需）")
    parser.add_argument("--host", help="查询服务主机地址（默认从配置读取）")
    parser.add_argument("--port", type=int, help="查询服务端口（默认从配置读取）")

    args = parser.parse_args()

    # 标准化 symbol
    symbol = _normalize_symbol(args.symbol)

    # 持仓模式参数校验
    if args.position_mode:
        missing = []
        if args.entry_price is None:
            missing.append("--entry-price")
        if args.side is None:
            missing.append("--side")
        if args.qty is None:
            missing.append("--qty")
        if missing:
            print(f"错误：持仓监控模式缺少必需参数: {', '.join(missing)}")
            print("用法: python -m quant_trader.query_sr --symbol SOL --timeframe 15m \\")
            print("      --position-mode --entry-price 67.0 --side long --qty 10")
            sys.exit(1)

    # 加载配置
    cfg = ConfigLoader()
    exchange_cfg = cfg.load_static()
    manual_config = _load_manual_config()
    host = args.host or exchange_cfg.status_host
    port = args.port or exchange_cfg.status_port

    # 构建查询参数
    params = {
        "symbol": symbol,
        "timeframe": args.timeframe,
    }

    if args.position_mode:
        params["mode"] = "position"
        params["entry_price"] = str(args.entry_price)
        params["side"] = args.side
        params["qty"] = str(args.qty)
    else:
        params["mode"] = "pre_trade"

    try:
        data = await _fetch_query(host, port, params)
    except aiohttp.ClientConnectorError:
        print(f"\n错误：查询服务未运行（{host}:{port}）")
        print("      请确认交易引擎已启动后再执行查询命令。")
        sys.exit(1)
    except aiohttp.ClientResponseError as exc:
        if exc.status == 404:
            # 引擎无该币种/周期的 K 线数据，尝试直接从 Binance 拉取
            print(f"\n提示：引擎未配置 {symbol} {args.timeframe}，正在直接从 Binance 拉取...")
            data = await _fetch_local_analysis(
                symbol, args.timeframe, exchange_cfg, manual_config,
                mode=("position" if args.position_mode else "pre_trade"),
                entry_price=args.entry_price,
                side=args.side,
                qty=args.qty,
            )
            if data is None:
                print(f"\n错误：{exc.message}")
                if hasattr(exc, '_error_data') and 'hint' in exc._error_data:
                    print(f"      {exc._error_data['hint']}")
                sys.exit(1)
        elif exc.status == 400:
            print(f"\n错误：{exc.message}")
            if hasattr(exc, '_error_data') and 'hint' in exc._error_data:
                print(f"      {exc._error_data['hint']}")
            sys.exit(1)
        else:
            print(f"\n错误：查询服务返回异常（HTTP {exc.status}）")
            sys.exit(1)
    except Exception as exc:
        print(f"\n错误：请求查询服务失败：{exc}")
        sys.exit(1)

    # 输出结果
    mode = "position" if args.position_mode else "pre_trade"
    output = format_output(data, mode)
    print(output)


def main():
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
