"""支撑位和压力位自动识别。

算法组合：
1. 波段高低点检测 (Swing High/Low)
2. 价格水平聚类
3. 成交量分布 (Volume Profile)
"""

import logging
from collections import Counter

import numpy as np

logger = logging.getLogger(__name__)


def find_swing_highs_lows(klines: list[dict], left: int = 2, right: int = 2) -> tuple[list, list]:
    """识别波段高点和波段低点。

    波段低点：某根 K 线的低点低于左右各 N 根 K 线的低点
    波段高点：某根 K 线的高点高于左右各 N 根 K 线的高点

    参数:
        klines: OHLCV 数据（旧→新）
        left: 左侧比较根数
        right: 右侧比较根数

    返回:
        (波段高点列表, 波段低点列表)，每项为 (索引, 价格)
    """
    n = len(klines)
    if n < left + right + 1:
        return [], []

    swing_highs = []
    swing_lows = []

    for i in range(left, n - right):
        high = klines[i]["high"]
        low = klines[i]["low"]

        # 检查波段高点
        if all(klines[j]["high"] <= high for j in range(max(0, i - left), i)) and \
           all(klines[j]["high"] <= high for j in range(i + 1, min(n, i + right + 1))):
            swing_highs.append((i, high))

        # 检查波段低点
        if all(klines[j]["low"] >= low for j in range(max(0, i - left), i)) and \
           all(klines[j]["low"] >= low for j in range(i + 1, min(n, i + right + 1))):
            swing_lows.append((i, low))

    return swing_highs, swing_lows


def cluster_levels(prices: list[float], tolerance_pct: float = 0.005) -> list[dict]:
    """将相近的价格聚类为支撑/压力区域。

    参数:
        prices: 波段高低点价格列表
        tolerance_pct: 聚类容差（0.5% 以内视为同一价位）

    返回:
        [{price, strength}] 列表，按强度降序排列
    """
    if not prices:
        return []

    clusters: dict[float, int] = {}
    for price in sorted(prices):
        found_cluster = False
        for existing_price in clusters:
            if abs(price - existing_price) / existing_price < tolerance_pct:
                clusters[existing_price] += 1
                found_cluster = True
                break
        if not found_cluster:
            clusters[price] = 1

    return sorted(
        [{"price": p, "strength": count} for p, count in clusters.items()],
        key=lambda x: x["strength"],
        reverse=True,
    )


def compute_volume_profile(klines: list[dict], num_bins: int = 50) -> list[dict]:
    """计算成交量分布 — 将成交量聚合到价格区间。

    高成交量区域通常构成强支撑/压力。

    参数:
        klines: OHLCV 数据
        num_bins: 价格区间数量

    返回:
        [{price, volume}] 列表，按成交量降序排列
    """
    if not klines:
        return []

    all_prices = [p for k in klines for p in (k["high"], k["low"])]
    min_price, max_price = min(all_prices), max(all_prices)
    if min_price == max_price:
        return []

    bin_width = (max_price - min_price) / num_bins
    volume_by_bin: Counter = Counter()

    for k in klines:
        # 将一根 K 线的成交量均匀分配到其价格区间内的 bin
        low_bin = int((k["low"] - min_price) / bin_width)
        high_bin = int((k["high"] - min_price) / bin_width)
        volume_per_bin = k["volume"] / max(high_bin - low_bin + 1, 1)
        for b in range(low_bin, min(high_bin + 1, num_bins)):
            volume_by_bin[b] += volume_per_bin

    result = []
    for bin_idx, vol in volume_by_bin.items():
        price = min_price + bin_width * (bin_idx + 0.5)
        result.append({"price": round(price, 2), "volume": round(vol, 2)})

    return sorted(result, key=lambda x: x["volume"], reverse=True)


def identify_support_resistance(klines: list[dict], min_touches: int = 2,
                                lookback: int = 100) -> dict:
    """完整的支撑/压力位分析。

    结合波段高低点聚类和成交量分布，得到更可靠的价位。

    参数:
        klines: OHLCV 数据（旧→新）
        min_touches: 最少触碰次数
        lookback: 分析 K 线根数

    返回:
        {"support": [...], "resistance": [...]}，每项含 price 和 strength
    """
    data = klines[-lookback:] if len(klines) > lookback else klines

    swing_highs, swing_lows = find_swing_highs_lows(data)

    # 波段高低点聚类
    resistance_clusters = cluster_levels([p for _, p in swing_highs])
    support_clusters = cluster_levels([p for _, p in swing_lows])

    # 成交量分布确认：高成交量节点增强相应价位的强度
    volume_profile = compute_volume_profile(data)
    high_volume_prices = set(
        vp["price"] for vp in volume_profile[:10]  # 前 10 个高量节点
    )

    for level in support_clusters + resistance_clusters:
        for hvp in high_volume_prices:
            if abs(level["price"] - hvp) / hvp < 0.01:
                level["strength"] += 1

    support = [s for s in support_clusters if s["strength"] >= min_touches]
    resistance = [r for r in resistance_clusters if r["strength"] >= min_touches]

    return {
        "support": sorted(support, key=lambda x: x["price"]),
        "resistance": sorted(resistance, key=lambda x: x["price"]),
    }
