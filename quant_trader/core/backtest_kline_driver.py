"""回测 K 线驱动器。

从回测 DB 读取历史 K 线，逐根产出 K 线事件（含 parent_window、funding_rate 上下文），
替代实时 K 线事件源。回测主循环消费此迭代器，将事件喂给 PaperTradingEngine。

复用自旧回测脚本（已废弃）迁移的逻辑：
- align_parent_klines: parent 周期对齐
- load_funding_rates: funding rate JSON 缓存加载
- match_funding_rate: 按时间戳匹配 funding rate

K 线数据完整性检查在回测入口（scripts/backtest_runner.py）启动前完成，
驱动器只负责消费已落库的数据。
"""

import bisect
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class KlineEvent:
    """单根 K 线的事件上下文。

    属性：
        kline: 当前 K 线（最新收盘的那根）
        window: 当前 K 线及其之前 window_size-1 根组成的滑动窗口（新→旧）
        parent_window: parent 周期窗口（新→旧），无 parent 时为 None
        funding_rate: 当前 K 线时间戳匹配到的 funding rate（百分比），无匹配时为 None
    """
    kline: dict
    window: list[dict]
    parent_window: list[dict] | None
    funding_rate: float | None


# ============================================================
# 工具函数：parent 周期对齐 / funding rate 匹配
# ============================================================

def align_parent_klines(
    klines_chrono: list[dict],
    parent_klines_chrono: list[dict],
    parent_lookback: int | None = None,
) -> list[list[dict] | None]:
    """将 parent K 线与当前周期对齐。

    参数:
        klines_chrono: 当前周期 K 线（旧→新）
        parent_klines_chrono: parent 周期 K 线（旧→新）
        parent_lookback: parent 窗口大小限制（None 表示不限制）

    返回:
        与 klines_chrono 等长的列表，每个元素是 parent 滑动窗口（新→旧）或 None
    """
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
            # 转为新→旧（与实时引擎一致）
            parent_windows.append(list(reversed(window)))
    return parent_windows


def match_funding_rate(timestamp_ms: int, records: list[dict]) -> float | None:
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


def load_funding_rates(symbol: str, cache_dir: Path | str) -> list[dict]:
    """从本地 JSON 缓存读取 funding rate 历史。

    返回按 timestamp_ms 升序排列的记录列表，每条记录含 rate_pct 和 timestamp_ms。
    缓存不存在时返回空列表。
    """
    cache_dir = Path(cache_dir)
    filename = symbol.replace("/", "_").replace(":", "_") + ".json"
    path = cache_dir / filename
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


# ============================================================
# BacktestKlineDriver
# ============================================================

class BacktestKlineDriver:
    """回测 K 线驱动器：从 DB 读取历史 K 线逐根推进。

    用法：
        driver = BacktestKlineDriver(data_store=ds, adapter=adapter, window_size=150)
        for event in driver.iter_klines(symbol="BTC/USDT", timeframe="5m",
                                         start=..., end=...,
                                         parent_timeframe="1h",
                                         funding_records=fr_records):
            paper_engine.update_prices(symbol, event.kline["close"], ds)
            signals = strategy.on_bar(symbol, timeframe, event.window, ...)
            ...

    注意：
        - 完整性检查与 API 补全由调用方在调用 iter_klines 之前完成
        - adapter 仅在调用方需要 API 补全时传入，本驱动不直接使用
    """

    def __init__(
        self,
        data_store,
        adapter=None,
        window_size: int = 150,
    ):
        self.data_store = data_store
        self.adapter = adapter
        self.window_size = window_size

    def iter_klines(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        parent_timeframe: str | None = None,
        parent_lookback: int | None = None,
        funding_records: list[dict] | None = None,
    ) -> Iterator[KlineEvent]:
        """按时间正序逐根产出 K 线事件。

        参数：
            symbol: 交易对，如 "BTC/USDT"
            timeframe: 当前周期，如 "5m"
            start, end: 时间范围（含两端）
            parent_timeframe: parent 周期（如 "1h"），None 表示不使用 parent
            parent_lookback: parent 窗口大小限制
            funding_records: funding rate 历史记录列表（按 timestamp_ms 升序）

        产出：
            KlineEvent 实例。前 window_size-1 根作为预热，从第 window_size 根开始产出事件。
        """
        # 1. 读取当前周期数据（旧→新）
        klines_chrono = self.data_store.get_klines_range(symbol, timeframe, start, end)
        if not klines_chrono:
            logger.warning("无 K 线数据 %s %s", symbol, timeframe)
            return

        # 2. 读取 parent 周期数据并预对齐
        parent_windows_aligned: list[list[dict] | None] = [None] * len(klines_chrono)
        if parent_timeframe:
            # parent 周期需要预热扩展（取 window_size 根作 warmup）
            parent_warmup = self._compute_parent_warmup(parent_timeframe, parent_lookback)
            parent_start = start - parent_warmup
            parent_klines_chrono = self.data_store.get_klines_range(
                symbol, parent_timeframe, parent_start, end,
            )
            if parent_klines_chrono:
                parent_windows_aligned = align_parent_klines(
                    klines_chrono, parent_klines_chrono, parent_lookback=parent_lookback,
                )
                logger.info(
                    "parent %s 对齐完成: %d 根当前 K 线, %d 根 parent K 线",
                    parent_timeframe, len(klines_chrono), len(parent_klines_chrono),
                )

        logger.info(
            "开始迭代 K 线 %s %s: 共 %d 根, 预热 %d 根, parent=%s",
            symbol, timeframe, len(klines_chrono), self.window_size - 1,
            parent_timeframe or "无",
        )

        # 3. 逐根产出事件（从 window_size-1 索引开始，保证 window 已满）
        for i in range(self.window_size - 1, len(klines_chrono)):
            kline = klines_chrono[i]
            # 构造滑动窗口（新→旧）：[klines[i], klines[i-1], ..., klines[i-window_size+1]]
            window = list(reversed(klines_chrono[i - self.window_size + 1: i + 1]))
            # parent 窗口（新→旧）
            parent_window = parent_windows_aligned[i] if i < len(parent_windows_aligned) else None
            # 匹配 funding rate
            funding_rate = None
            if funding_records:
                ts_ms = int(kline["timestamp"].timestamp() * 1000)
                funding_rate = match_funding_rate(ts_ms, funding_records)

            yield KlineEvent(
                kline=kline,
                window=window,
                parent_window=parent_window,
                funding_rate=funding_rate,
            )

    def _compute_parent_warmup(self, parent_timeframe: str, parent_lookback: int | None) -> timedelta:
        """根据 parent 周期和 lookback 计算预热时长。

        保守策略：取 parent_lookback 或 100 根中的较大者，按 parent 周期换算为时长。
        """
        from ..main import TIMEFRAME_SECONDS
        parent_seconds = TIMEFRAME_SECONDS.get(parent_timeframe, 3600)
        lookback = max(parent_lookback or 0, 100)
        return timedelta(seconds=parent_seconds * lookback)
