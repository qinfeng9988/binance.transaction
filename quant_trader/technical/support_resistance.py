"""支撑位和压力位自动识别。

算法组合：
1. 波段高低点检测 (Swing High/Low)
2. 价格水平聚类
3. 成交量分布 (Volume Profile)
4. S/R 位质量加权评分（触碰次数 + 成交量爆发 + 聚类密度）
5. 拒绝蜡烛确认（Rejection Candle）
"""

import logging
from collections import Counter

import numpy as np

from .indicators import adx

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
        [{price, strength, density}] 列表，按强度降序排列
        density: 0.5% 容差内的价格点数量
    """
    if not prices:
        return []

    # 第一轮：建立聚类中心
    cluster_items: list[list[float]] = []
    for price in sorted(prices):
        found = False
        for items in cluster_items:
            center = sum(items) / len(items)
            if abs(price - center) / center < tolerance_pct:
                items.append(price)
                found = True
                break
        if not found:
            cluster_items.append([price])

    result = []
    for items in cluster_items:
        price = sum(items) / len(items)
        result.append({"price": price, "strength": len(items), "density": len(items)})

    return sorted(result, key=lambda x: x["strength"], reverse=True)


def compute_volume_profile(klines: list[dict], num_bins: int = 50,
                           value_area_pct: float = 0.70) -> dict:
    """计算成交量分布 — 将成交量聚合到价格区间，并提取 POC / HVN / VAH / VAL。

    高成交量区域通常构成强支撑/压力。POC（Point of Control）是成交量
    最大的价格点，HVN（High Volume Node）是成交量显著高于平均的节点。
    VAH/VAL（Value Area High/Low）是累计成交量达到指定百分比的价格区间
    上下沿，代表市场共识的价值区域边界。

    参数:
        klines: OHLCV 数据（旧→新）
        num_bins: 价格区间数量
        value_area_pct: Value Area 累计成交量百分比（默认 70%）

    返回:
        {
            "profile": [{"price": float, "volume": float}, ...],  # 按成交量降序
            "poc": float,       # Point of Control：成交量最大的价格点
            "hvn": [float, ...], # High Volume Nodes：volume > mean + 0.5*std
            "vah": float,       # Value Area High：价值区上沿
            "val": float,       # Value Area Low：价值区下沿
            "mean_volume": float,
            "std_volume": float,
        }
    """
    if not klines:
        return {
            "profile": [],
            "poc": 0.0,
            "hvn": [],
            "vah": 0.0,
            "val": 0.0,
            "mean_volume": 0.0,
            "std_volume": 0.0,
        }

    all_prices = [p for k in klines for p in (k["high"], k["low"])]
    min_price, max_price = min(all_prices), max(all_prices)
    if min_price == max_price:
        return {
            "profile": [],
            "poc": min_price,
            "hvn": [],
            "vah": min_price,
            "val": min_price,
            "mean_volume": 0.0,
            "std_volume": 0.0,
        }

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

    # 按成交量降序排列
    profile = sorted(result, key=lambda x: x["volume"], reverse=True)

    # 计算 POC（成交量最大的节点）
    poc = profile[0]["price"] if profile else 0.0

    # 计算 mean 和 std
    volumes = [p["volume"] for p in profile]
    mean_vol = sum(volumes) / len(volumes) if volumes else 0.0
    std_vol = (sum((v - mean_vol) ** 2 for v in volumes) / len(volumes)) ** 0.5 if volumes else 0.0

    # HVN：成交量显著高于平均的节点（mean + 0.5 * std）
    hvn_threshold = mean_vol + 0.5 * std_vol
    hvn = [p["price"] for p in profile if p["volume"] >= hvn_threshold]

    # --- VAH / VAL 计算（Value Area High / Low）---
    vah = 0.0
    val = 0.0
    if profile:
        # 按价格排序（低到高），用于从 POC 向两侧扩展
        profile_by_price = sorted(result, key=lambda x: x["price"])

        # 找到 POC 在价格排序列表中的索引
        poc_idx = max(
            range(len(profile_by_price)),
            key=lambda i: profile_by_price[i]["volume"],
        )

        total_volume = sum(volumes)
        target_volume = total_volume * value_area_pct

        included = {poc_idx}
        cum_vol = profile_by_price[poc_idx]["volume"]

        while cum_vol < target_volume and len(included) < len(profile_by_price):
            lower = min(included) - 1
            upper = max(included) + 1

            lower_vol = profile_by_price[lower]["volume"] if lower >= 0 else -1.0
            upper_vol = profile_by_price[upper]["volume"] if upper < len(profile_by_price) else -1.0

            if lower_vol >= upper_vol and lower >= 0:
                included.add(lower)
                cum_vol += lower_vol
            elif upper < len(profile_by_price):
                included.add(upper)
                cum_vol += upper_vol
            else:
                break

        prices_in_va = [profile_by_price[i]["price"] for i in included]
        val = min(prices_in_va)
        vah = max(prices_in_va)

    return {
        "profile": profile,
        "poc": poc,
        "hvn": hvn,
        "vah": round(vah, 2),
        "val": round(val, 2),
        "mean_volume": round(mean_vol, 2),
        "std_volume": round(std_vol, 2),
    }


def check_volume_surge(klines: list[dict], lookback: int = 20, surge_ratio: float = 1.5) -> tuple[bool, float]:
    """检查最新一根 K 线是否出现成交量爆发。

    注意：klines 由引擎反转后传入，index 0 为最新 K 线。

    参数:
        klines: OHLCV 数据（新→旧）
        lookback: 平均成交量计算周期
        surge_ratio: 爆发阈值倍数

    返回:
        (是否爆发, 当前成交量/平均成交量的比值)
    """
    if len(klines) < lookback + 1:
        return False, 1.0

    current_vol = klines[0]["volume"]  # 最新 K 线
    avg_vol = sum(k["volume"] for k in klines[1:lookback + 1]) / lookback
    if avg_vol == 0:
        return False, 1.0

    ratio = current_vol / avg_vol
    return ratio >= surge_ratio, ratio


def check_rejection_candle(kline: dict, level_type: str,
                            min_wick_ratio: float = 2.0,
                            max_body_ratio: float = 0.5) -> bool:
    """检查单根 K 线是否为拒绝蜡烛。

    多头：在支撑位附近出现长下影线 + 收盘价 > 开盘
    空头：在压力位附近出现长上影线 + 收盘价 < 开盘

    参数:
        kline: 单根 K 线数据
        level_type: "support" 或 "resistance"
        min_wick_ratio: 影线相对于实体的最小倍数
        max_body_ratio: 实体占波幅的最大比例（小实体要求）

    返回:
        是否为拒绝蜡烛
    """
    open_p = kline["open"]
    high = kline["high"]
    low = kline["low"]
    close = kline["close"]

    body = abs(close - open_p)
    range_size = high - low

    if range_size == 0:
        return False

    # 小实体检查
    if body / range_size > max_body_ratio:
        return False

    if body == 0:
        return False

    if level_type == "support":
        lower_shadow = min(open_p, close) - low
        if lower_shadow / body >= min_wick_ratio and close > open_p:
            return True
    elif level_type == "resistance":
        upper_shadow = high - max(open_p, close)
        if upper_shadow / body >= min_wick_ratio and close < open_p:
            return True

    return False


def find_order_blocks(klines: list[dict], min_swing_bars: int = 3) -> dict:
    """检测 Order Block（订单块）。

    Bullish OB: 在下跌趋势中，反转成上涨前最后一根 bearish K 线（close < open）的 low
    Bearish OB: 在上涨趋势中，反转成下跌前最后一根 bullish K 线（close > open）的 high

    参数:
        klines: OHLCV 数据（旧→新）
        min_swing_bars: 识别趋势反转所需的最小连续同向 K 线数

    返回:
        {"bullish": [float, ...], "bearish": [float, ...]}
    """
    n = len(klines)
    if n < min_swing_bars * 2 + 1:
        return {"bullish": [], "bearish": []}

    bullish_obs = []
    bearish_obs = []

    i = min_swing_bars
    while i < n - min_swing_bars:
        # 检查前面是否有连续 bearish K 线（下跌趋势）
        prior_bearish = all(klines[j]["close"] < klines[j]["open"] for j in range(i - min_swing_bars, i))
        # 检查后面是否有连续 bullish K 线（反转上涨）
        after_bullish = all(klines[j]["close"] > klines[j]["open"] for j in range(i, i + min_swing_bars))

        if prior_bearish and after_bullish:
            # Bullish OB: 取反转前最后一根 bearish K 线的 low 作为支撑
            bullish_obs.append(klines[i - 1]["low"])
            i += min_swing_bars  # 跳过已识别的区域
            continue

        # 检查前面是否有连续 bullish K 线（上涨趋势）
        prior_bullish = all(klines[j]["close"] > klines[j]["open"] for j in range(i - min_swing_bars, i))
        # 检查后面是否有连续 bearish K 线（反转下跌）
        after_bearish = all(klines[j]["close"] < klines[j]["open"] for j in range(i, i + min_swing_bars))

        if prior_bullish and after_bearish:
            # Bearish OB: 取反转前最后一根 bullish K 线的 high 作为压力
            bearish_obs.append(klines[i - 1]["high"])
            i += min_swing_bars
            continue

        i += 1

    return {"bullish": bullish_obs, "bearish": bearish_obs}


def find_fvg(klines: list[dict]) -> dict:
    """检测 Fair Value Gaps（公允价值缺口）。

    Bullish FVG: klines[i].high < klines[i+2].low（价格跳空上涨，缺口构成支撑）
    Bearish FVG: klines[i].low > klines[i+2].high（价格跳空下跌，缺口构成压力）

    参数:
        klines: OHLCV 数据（旧→新）

    返回:
        {"bullish": [(low, high), ...], "bearish": [(low, high), ...]}
        bullish FVG 的 low/high 分别取 klines[i+1].low / klines[i+2].low
        bearish FVG 的 low/high 分别取 klines[i+2].high / klines[i+1].high
    """
    n = len(klines)
    if n < 3:
        return {"bullish": [], "bearish": []}

    bullish_fvgs = []
    bearish_fvgs = []

    for i in range(n - 2):
        k0 = klines[i]
        k1 = klines[i + 1]
        k2 = klines[i + 2]

        # Bullish FVG: k0.high < k2.low（中间 K 线留下一个向上缺口）
        if k0["high"] < k2["low"]:
            # 缺口下沿 = k1.low（中间 K 线的低点，最接近缺口的价位）
            # 缺口上沿 = k2.low
            bullish_fvgs.append((k1["low"], k2["low"]))

        # Bearish FVG: k0.low > k2.high（中间 K 线留下一个向下缺口）
        if k0["low"] > k2["high"]:
            # 缺口下沿 = k2.high
            # 缺口上沿 = k1.high
            bearish_fvgs.append((k2["high"], k1["high"]))

    return {"bullish": bullish_fvgs, "bearish": bearish_fvgs}


def is_fvg_filled(klines: list[dict], gap_low: float, gap_high: float,
                   gap_idx: int = 0, is_bullish: bool = True) -> bool:
    """检查 FVG 是否已被回补。

    参数:
        klines: OHLCV 数据（旧→新）
        gap_low: 缺口下沿
        gap_high: 缺口上沿
        gap_idx: 缺口形成时的 K 线索引（在 klines 中的位置）
        is_bullish: True=向上缺口（bullish），False=向下缺口（bearish）

    返回:
        True=已被回补，False=未回补
    """
    # 从缺口形成后的 K 线开始检查
    for k in klines[gap_idx + 1:]:
        # 如果价格回到缺口区间内，视为已回补
        if k["low"] <= gap_high and k["high"] >= gap_low:
            return True
    return False


def identify_support_resistance(klines: list[dict], min_touches: float = 2.0,
                                lookback: int = 100,
                                volume_surge_ratio: float = 1.5,
                                require_rejection: bool = False,
                                bias: str = "neutral",
                                recency_lookback: int = 20,
                                trend_bonus_aligned: float = 1.3,
                                trend_bonus_counter: float = -0.5,
                                adaptive_swing_window: bool = False,
                                swing_window_adx_threshold: float = 25.0,
                                swing_window_strong: int = 3,
                                swing_window_weak: int = 2,
                                use_order_blocks: bool = False,
                                use_fvg: bool = False,
                                rejection_min_wick_ratio: float = 2.0,
                                rejection_max_body_ratio: float = 0.5,
                                use_vah_val: bool = False,
                                value_area_pct: float = 0.70,
                                vah_val_near_threshold_pct: float = 0.005,
                                poc_near_threshold_pct: float = 0.005,
                                poc_far_threshold_pct: float = 0.007,
                                vah_val_near_bonus: float = 0.5,
                                vah_val_inner_bonus: float = 0.3) -> dict:
    """完整的支撑/压力位分析（含质量加权评分 + 趋势一致性 + 新鲜度 + VAH/VAL）。

    结合波段高低点聚类、Order Block、FVG、成交量分布（POC/HVN/VAH/VAL）、
    成交量爆发、拒绝蜡烛确认、趋势一致性和新鲜度，得到更可靠的价位及质量评分。

    参数:
        klines: OHLCV 数据（新→旧，index 0 为最新）
        min_touches: 最少触碰次数（加权评分后筛选）
        lookback: 分析 K 线根数
        volume_surge_ratio: 成交量爆发阈值倍数
        require_rejection: 是否要求拒绝蜡烛确认
        bias: 交易偏向
        recency_lookback: 新鲜度检查回看周期
        adaptive_swing_window: 是否根据 ADX 自适应 swing 窗口
        swing_window_adx_threshold: ADX 阈值（高于此值用 strong 窗口）
        swing_window_strong: 强趋势时的窗口大小
        swing_window_weak: 弱趋势时的窗口大小
        use_order_blocks: 是否使用 Order Block 作为补充 S/R
        use_fvg: 是否使用 FVG 作为补充 S/R
        use_vah_val: 是否启用 VAH/VAL 评分加成
        value_area_pct: Value Area 累计成交量百分比
        vah_val_near_threshold_pct: VAH/VAL 近区距离阈值（如 0.005 表示 ±0.5%）
        poc_near_threshold_pct: POC 近距离阈值（默认 ±0.5%）
        poc_far_threshold_pct: POC 远距离阈值（默认 ±0.7%，B+D方案缩小）
        vah_val_near_bonus: VAH/VAL 边界加分（默认 +0.5，温和）
        vah_val_inner_bonus: Value Area 内部加分（默认 +0.3）

    返回:
        {
            "support": [{"price", "strength", "quality_score", ...}, ...],
            "resistance": [{"price", "strength", "quality_score", ...}, ...]
        }
    """
    # 注意：klines 为新→旧，但 find_swing_highs_lows 需要旧→新
    # 取最新的 lookback 根（index 0 为最新），反转后得到旧→新
    data = list(reversed(klines[:lookback])) if len(klines) > lookback else list(reversed(klines))

    # --- 自适应 swing 窗口 ---
    left = right = 2
    if adaptive_swing_window and len(data) > 50:
        try:
            adx_vals = adx(data, period=14)
            current_adx_for_swing = float(adx_vals[-1]) if len(adx_vals) > 0 else 0.0
            if current_adx_for_swing > swing_window_adx_threshold:
                left = right = swing_window_strong
            else:
                left = right = swing_window_weak
        except Exception:
            pass  # ADX 计算失败时回退到默认窗口

    swing_highs, swing_lows = find_swing_highs_lows(data, left=left, right=right)

    # --- 收集所有来源的价格点 ---
    all_support_prices = [p for _, p in swing_lows]
    all_resistance_prices = [p for _, p in swing_highs]

    # Order Block：加入价格点增强聚类强度（不标记来源，混合聚类）
    if use_order_blocks:
        try:
            obs = find_order_blocks(data)
            all_support_prices.extend(obs["bullish"])
            all_resistance_prices.extend(obs["bearish"])
        except Exception:
            pass

    # FVG：加入价格点增强聚类强度（暂不过滤已回补，避免过度减少 S/R 位）
    if use_fvg:
        try:
            fvgs = find_fvg(data)
            for low, high in fvgs["bullish"]:
                all_support_prices.extend([low, high])
            for low, high in fvgs["bearish"]:
                all_resistance_prices.extend([low, high])
        except Exception:
            pass

    # --- 统一聚类 ---
    resistance_clusters = cluster_levels(all_resistance_prices)
    support_clusters = cluster_levels(all_support_prices)

    # 成交量分布确认：POC / HVN / VAH / VAL / 高量节点分层加分
    vp_result = compute_volume_profile(data, value_area_pct=value_area_pct)
    volume_profile = vp_result["profile"]
    poc_price = vp_result["poc"]
    hvn_prices = vp_result["hvn"]
    vah_price = vp_result["vah"] if use_vah_val else 0.0
    val_price = vp_result["val"] if use_vah_val else 0.0
    high_volume_prices = set(
        vp["price"] for vp in volume_profile[:10]  # 前 10 个高量节点（兜底）
    )

    # 检查最新 K 线成交量爆发
    has_volume_surge, surge_ratio = check_volume_surge(klines, lookback=20, surge_ratio=volume_surge_ratio)

    def _enrich_levels(levels: list[dict], level_type: str) -> list[dict]:
        """为每个价位添加质量评分和确认标志。"""
        enriched = []
        for level in levels:
            price = level["price"]
            strength = level["strength"]
            density = level["density"]

            # 成交量分布加成（分层：POC > HVN > 普通高量节点）
            vp_bonus = 0.0
            vp_detail = "none"

            # 1. POC (Point of Control) — 成交量最大的节点
            if poc_price and abs(price - poc_price) / poc_price < poc_near_threshold_pct:
                vp_bonus = 2.0
                vp_detail = "POC_near"
            elif poc_price and abs(price - poc_price) / poc_price < poc_far_threshold_pct:
                vp_bonus = 1.0
                vp_detail = "POC_far"
            # 2. HVN (High Volume Node) — 成交量显著高于平均的节点
            elif hvn_prices:
                for hvn in hvn_prices:
                    if abs(price - hvn) / hvn < 0.005:
                        vp_bonus = 1.5
                        vp_detail = "HVN_near"
                        break
                    elif abs(price - hvn) / hvn < 0.01:
                        vp_bonus = 0.5
                        vp_detail = "HVN_far"
                        break
            # 3. 普通高量节点兜底（top 10）
            if vp_bonus == 0.0:
                for hvp in high_volume_prices:
                    if abs(price - hvp) / hvp < 0.01:
                        vp_bonus = 0.5
                        vp_detail = "LVN"
                        break

            # 4. VAH/VAL 边界加成（Value Area 区域）
            # 与 POC/HVN 叠加：VAH/VAL 附近的位额外加分（温和版本）
            if use_vah_val and vah_price > 0 and val_price > 0:
                near_vah = abs(price - vah_price) / price < vah_val_near_threshold_pct
                near_val = abs(price - val_price) / price < vah_val_near_threshold_pct
                if near_vah or near_val:
                    vp_bonus += vah_val_near_bonus
                    vp_detail = "VAH_near" if near_vah else "VAL_near"
                elif val_price <= price <= vah_price:
                    vp_bonus += vah_val_inner_bonus
                    if vp_detail == "none":
                        vp_detail = "VA_inner"

            # 成交量爆发加成（仅最新一根）
            vol_surge_bonus = 1 if has_volume_surge else 0

            # 聚类密度加成：密度越高权重越高（线性加权）
            density_bonus = min(density * 0.3, 2.0)

            # 来源加成（混合聚类模式下不区分来源，OB/FVG 已通过增强聚类强度发挥作用）
            source_bonus = 0.0

            # === 趋势一致性 bonus/penalty ===
            # 顺势 S/R 位加分，逆势 S/R 位惩罚（力度由参数控制）
            trend_bonus = 0.0
            if bias in ("uptrend", "long_only", "long_primary"):
                if level_type == "support":
                    trend_bonus = trend_bonus_aligned
                elif level_type == "resistance":
                    trend_bonus = trend_bonus_counter
            elif bias in ("downtrend", "short_only", "short_primary"):
                if level_type == "resistance":
                    trend_bonus = trend_bonus_aligned
                elif level_type == "support":
                    trend_bonus = trend_bonus_counter

            # === 新鲜度 bonus ===
            # 最近 recency_lookback 根 K 线内被测试过的位加分
            recency_bonus = 0.0
            if len(klines) >= recency_lookback:
                for k in klines[:recency_lookback]:
                    if level_type == "support":
                        # 价格接近支撑位（低点在支撑附近或收盘价在支撑附近）
                        if abs(k["low"] - price) / price < 0.008 or abs(k["close"] - price) / price < 0.005:
                            recency_bonus = 1.0
                            break
                    else:
                        # 价格接近压力位（高点在压力附近或收盘价在压力附近）
                        if abs(k["high"] - price) / price < 0.008 or abs(k["close"] - price) / price < 0.005:
                            recency_bonus = 1.0
                            break

            # 基础质量分数（不含趋势和新鲜度，便于调试分析）
            base_score = strength + vp_bonus + vol_surge_bonus + density_bonus + source_bonus

            # 最终质量分数 = 基础分 + 趋势一致性 + 新鲜度
            quality_score = base_score + trend_bonus + recency_bonus

            # 拒绝蜡烛检查（仅启用时计算）
            has_rejection = False
            if require_rejection and len(klines) >= 5:
                for k in klines[:5]:
                    if level_type == "support":
                        if abs(k["low"] - price) / price < 0.005:
                            has_rejection = check_rejection_candle(
                                k, "support",
                                min_wick_ratio=rejection_min_wick_ratio,
                                max_body_ratio=rejection_max_body_ratio,
                            )
                    else:
                        if abs(k["high"] - price) / price < 0.005:
                            has_rejection = check_rejection_candle(
                                k, "resistance",
                                min_wick_ratio=rejection_min_wick_ratio,
                                max_body_ratio=rejection_max_body_ratio,
                            )
                    if has_rejection:
                        break

                if not has_rejection:
                    quality_score *= 0.3

            enriched.append({
                "price": price,
                "strength": strength,
                "vp_bonus": round(vp_bonus, 1),
                "vp_detail": vp_detail,
                "base_score": round(base_score, 2),
                "quality_score": round(quality_score, 2),
                "trend_bonus": round(trend_bonus, 1),
                "recency_bonus": round(recency_bonus, 1),
                "has_rejection": has_rejection,
                "has_volume_surge": has_volume_surge,
                "surge_ratio": round(surge_ratio, 2),
            })

        return enriched

    support = _enrich_levels(support_clusters, "support")
    resistance = _enrich_levels(resistance_clusters, "resistance")

    # 按质量分数筛选（替代原来的简单 strength 筛选）
    support = [s for s in support if s["quality_score"] >= min_touches]
    resistance = [r for r in resistance if r["quality_score"] >= min_touches]

    return {
        "support": sorted(support, key=lambda x: x["price"]),
        "resistance": sorted(resistance, key=lambda x: x["price"]),
    }


def analyze_sr_levels(
    klines: list[dict],
    parent_klines: list[dict] | None = None,
    params: dict | None = None,
) -> dict:
    """S/R 位纯分析函数（不生成交易信号）。

    整合 S/R 位识别、趋势判断、ADX/RSI/ATR 指标计算，
    返回策略视角下的完整市场分析结果，供查询工具和策略共用。

    参数:
        klines: 当前周期 K 线数据（新→旧，index 0 为最新）
        parent_klines: 上级周期 K 线数据（可选，用于多周期趋势确认）
        params: 分析参数字典，None 时使用默认参数

    返回:
        {
            "current_price": float,
            "timestamp": str,
            "trend": {"trend": str, "ema_fast": float, "ema_slow": float},
            "parent_trend": {"trend": str, "ema_fast": float, "ema_slow": float} | None,
            "bias": str,
            "adx": float,
            "plus_di": float,
            "minus_di": float,
            "rsi": float,
            "atr": float,
            "volume_ratio": float,
            "levels": {"support": [...], "resistance": [...]},
            "is_ranging": bool,
        }
    """
    from ..technical.indicators import ema, rsi as calc_rsi, adx as calc_adx, plus_di, minus_di, atr as calc_atr

    # 默认参数
    default_params = {
        "sr_lookback": 100,
        "ema_fast_period": 21,
        "ema_slow_period": 50,
        "ema_gap_threshold_pct": 1.0,
        "adx_period": 14,
        "adx_min": 22.0,
        "rsi_period": 14,
        "volume_burst_lookback": 20,
        "volume_burst_ratio": 1.8,
        "min_touches": 3.0,
        "entry_threshold_pct": 0.5,
        "require_parent_ema_aligned": True,
        "trend_bonus_aligned": 1.3,
        "trend_bonus_counter": -0.5,
        "require_rejection_candle": False,
        "rejection_min_wick_ratio": 2.0,
        "rejection_max_body_ratio": 0.5,
        "adaptive_swing_window": False,
        "swing_window_adx_threshold": 25.0,
        "swing_window_strong": 3,
        "swing_window_weak": 2,
        "use_order_blocks": False,
        "use_fvg": False,
        "use_vah_val": False,
        "value_area_pct": 0.70,
        "vah_val_near_threshold_pct": 0.005,
        "poc_near_threshold_pct": 0.005,
        "poc_far_threshold_pct": 0.007,
        "vah_val_near_bonus": 0.5,
        "vah_val_inner_bonus": 0.3,
    }

    p = {**default_params, **(params or {})}

    if len(klines) < 50:
        return {"error": "K 线数据不足（需要至少 50 根）"}

    # 恢复时间顺序（旧→新）用于指标计算
    chrono = list(reversed(klines))

    current_price = klines[0]["close"]
    timestamp = klines[0].get("timestamp", "")

    # ---- 1. EMA 趋势判断 ----
    ema_fast_arr = ema(chrono, p["ema_fast_period"])
    ema_slow_arr = ema(chrono, p["ema_slow_period"])
    fast = ema_fast_arr[-1]
    slow = ema_slow_arr[-1]
    gap_pct = abs(fast - slow) / slow * 100 if slow != 0 else 0

    if gap_pct < p["ema_gap_threshold_pct"]:
        trend = "ranging"
    else:
        trend = "uptrend" if fast > slow else "downtrend"

    trend_info = {
        "trend": trend,
        "ema_fast": round(float(fast), 2),
        "ema_slow": round(float(slow), 2),
    }

    # ---- 2. 上级周期趋势 ----
    parent_trend_info = None
    parent_aligned = None

    if parent_klines and len(parent_klines) >= 50:
        parent_chrono = list(reversed(parent_klines))
        parent_ema_fast = ema(parent_chrono, p["ema_fast_period"])
        parent_ema_slow = ema(parent_chrono, p["ema_slow_period"])
        parent_fast = parent_ema_fast[-1]
        parent_slow = parent_ema_slow[-1]
        parent_gap = abs(parent_fast - parent_slow) / parent_slow * 100 if parent_slow != 0 else 0

        if parent_gap < p["ema_gap_threshold_pct"]:
            parent_trend = "ranging"
        else:
            parent_trend = "uptrend" if parent_fast > parent_slow else "downtrend"

        parent_aligned = parent_trend != "ranging"
        parent_trend_info = {
            "trend": parent_trend,
            "ema_fast": round(float(parent_fast), 2),
            "ema_slow": round(float(parent_slow), 2),
        }

    # ---- 3. 交易偏向判断 ----
    def _compute_bias(current_trend: str, parent_trend: str | None, require_aligned: bool) -> str:
        if parent_trend is None:
            if current_trend == "uptrend":
                return "long_primary"
            elif current_trend == "downtrend":
                return "short_primary"
            return "neutral"

        if require_aligned and parent_trend == "ranging":
            return "neutral"

        if current_trend == parent_trend:
            if current_trend == "uptrend":
                return "long_only"
            elif current_trend == "downtrend":
                return "short_only"
            return "neutral"

        if parent_trend == "uptrend":
            return "long_primary"
        elif parent_trend == "downtrend":
            return "short_primary"

        if current_trend == "uptrend":
            return "long_primary"
        elif current_trend == "downtrend":
            return "short_primary"
        return "neutral"

    bias = _compute_bias(
        trend,
        parent_trend_info["trend"] if parent_trend_info else None,
        p["require_parent_ema_aligned"],
    )

    # ---- 4. ADX 计算 ----
    adx_vals = calc_adx(chrono, period=p["adx_period"])
    pdi_vals = plus_di(chrono, period=p["adx_period"])
    mdi_vals = minus_di(chrono, period=p["adx_period"])

    current_adx = float(adx_vals[-1]) if len(adx_vals) > 0 else 0.0
    current_pdi = float(pdi_vals[-1]) if len(pdi_vals) > 0 else 0.0
    current_mdi = float(mdi_vals[-1]) if len(mdi_vals) > 0 else 0.0

    if current_adx != current_adx:  # NaN check
        current_adx = 0.0

    # ---- 5. RSI 计算 ----
    rsi_vals = calc_rsi(chrono, period=p["rsi_period"])
    current_rsi = float(rsi_vals[-1]) if len(rsi_vals) > 0 else 0.0
    if current_rsi != current_rsi:
        current_rsi = 0.0

    # ---- 6. ATR 计算 ----
    atr_vals = calc_atr(chrono, period=p["adx_period"])
    current_atr = float(atr_vals[-1]) if len(atr_vals) > 0 else 0.0
    if current_atr != current_atr or current_atr <= 0:
        current_atr = 0.0

    # ---- 7. 成交量爆发 ----
    has_surge, surge_ratio = check_volume_surge(klines, lookback=p["volume_burst_lookback"], surge_ratio=p["volume_burst_ratio"])

    # ---- 8. 震荡判断 ----
    is_ranging = (current_adx < p["adx_min"]) or (trend == "ranging")
    if parent_trend_info and parent_trend_info["trend"] == "ranging":
        is_ranging = True

    # ---- 9. S/R 位识别 ----
    levels = identify_support_resistance(
        klines,
        min_touches=p["min_touches"],
        lookback=p["sr_lookback"],
        volume_surge_ratio=1.5,
        require_rejection=p["require_rejection_candle"],
        bias=bias,
        trend_bonus_aligned=p["trend_bonus_aligned"],
        trend_bonus_counter=p["trend_bonus_counter"],
        adaptive_swing_window=p["adaptive_swing_window"],
        swing_window_adx_threshold=p["swing_window_adx_threshold"],
        swing_window_strong=p["swing_window_strong"],
        swing_window_weak=p["swing_window_weak"],
        use_order_blocks=p["use_order_blocks"],
        use_fvg=p["use_fvg"],
        rejection_min_wick_ratio=p["rejection_min_wick_ratio"],
        rejection_max_body_ratio=p["rejection_max_body_ratio"],
        use_vah_val=p["use_vah_val"],
        value_area_pct=p["value_area_pct"],
        vah_val_near_threshold_pct=p["vah_val_near_threshold_pct"],
        poc_near_threshold_pct=p["poc_near_threshold_pct"],
        poc_far_threshold_pct=p["poc_far_threshold_pct"],
        vah_val_near_bonus=p["vah_val_near_bonus"],
        vah_val_inner_bonus=p["vah_val_inner_bonus"],
    )

    return {
        "current_price": round(current_price, 2),
        "timestamp": timestamp,
        "trend": trend_info,
        "parent_trend": parent_trend_info,
        "bias": bias,
        "parent_aligned": parent_aligned,
        "adx": round(current_adx, 1),
        "plus_di": round(current_pdi, 1),
        "minus_di": round(current_mdi, 1),
        "rsi": round(current_rsi, 1),
        "atr": round(current_atr, 2),
        "volume_ratio": round(surge_ratio, 2),
        "has_volume_surge": has_surge,
        "levels": levels,
        "is_ranging": is_ranging,
    }


def compute_position_analysis(
    analysis: dict,
    current_price: float,
    entry: float,
    side: str,
    qty: float,
    manual_config: dict,
) -> dict:
    """计算持仓监控的结构化止损/止盈建议（纯函数，无 I/O）。

    与查询服务的引擎端（query_server._query_handler）和客户端兜底
    （query_sr._fetch_local_analysis）共用同一套逻辑，保证持仓模式下两条
    路径输出一致。

    参数:
        analysis: analyze_sr_levels 返回的分析结果（含 levels / atr）
        current_price: 当前价格
        entry: 入场价格（float）
        side: 持仓方向 "long" / "short"
        qty: 持仓数量
        manual_config: 手动交易辅助配置（manual_config.yaml）

    返回:
        position_analysis dict，字段与引擎端一致。
    """
    atr_val = analysis.get("atr", 0.0) or 0.0
    atr_multiplier = manual_config.get("atr_multiplier", 5.5)

    is_long = side.lower() == "long"

    # ATR 止损（手动缓冲版）
    long_stop = current_price - atr_val * atr_multiplier if atr_val > 0 else None
    short_stop = current_price + atr_val * atr_multiplier if atr_val > 0 else None

    # 当前盈亏
    if is_long:
        unrealized_pnl = (current_price - entry) * qty
        profit_pct = (current_price - entry) / entry * 100 if entry != 0 else 0.0
    else:
        unrealized_pnl = (entry - current_price) * qty
        profit_pct = (entry - current_price) / entry * 100 if entry != 0 else 0.0

    all_levels = analysis["levels"]["support"] + analysis["levels"]["resistance"]
    buffer_pct = (manual_config.get("structured_stop") or {}).get("buffer_pct", 0.3)

    # 结构化止损（基于最近 S/R 位）
    structured_stop = None
    if is_long:
        # 做多：找低于当前价的最近支撑
        supports = sorted(
            [l for l in all_levels if l["price"] < current_price],
            key=lambda x: x["price"], reverse=True,
        )
        if supports:
            s_price = supports[0]["price"]
            structured_stop = s_price * (1 - buffer_pct / 100)
            if current_price > entry:
                structured_stop = max(structured_stop, entry)   # 浮盈：保本
            else:
                structured_stop = min(structured_stop, current_price)  # 浮亏：止损低于当前价
    else:
        # 做空：找高于当前价的最近压力
        resistances = sorted(
            [l for l in all_levels if l["price"] > current_price],
            key=lambda x: x["price"],
        )
        if resistances:
            r_price = resistances[0]["price"]
            structured_stop = r_price * (1 + buffer_pct / 100)
            if current_price < entry:
                structured_stop = min(structured_stop, entry)   # 浮盈：保本
            else:
                structured_stop = max(structured_stop, current_price)  # 浮亏：止损高于当前价

    # 止盈目标（S/R 质量分降序，ATR 兜底）
    tp1 = tp2 = None
    tp1_source = tp2_source = None
    tp1_quality = tp2_quality = None

    if is_long:
        tp_candidates = sorted(
            [l for l in all_levels if l["price"] > current_price],
            key=lambda x: x["quality_score"], reverse=True,
        )
        if len(tp_candidates) >= 1:
            tp1, tp1_source, tp1_quality = tp_candidates[0]["price"], "sr", tp_candidates[0]["quality_score"]
        if len(tp_candidates) >= 2:
            tp2, tp2_source, tp2_quality = tp_candidates[1]["price"], "sr", tp_candidates[1]["quality_score"]
        if tp1 is None and atr_val > 0:
            tp1, tp1_source = entry + 2 * atr_val, "atr"
        if tp2 is None and atr_val > 0:
            tp2, tp2_source = entry + 3 * atr_val, "atr"
    else:
        tp_candidates = sorted(
            [l for l in all_levels if l["price"] < current_price],
            key=lambda x: x["quality_score"], reverse=True,
        )
        if len(tp_candidates) >= 1:
            tp1, tp1_source, tp1_quality = tp_candidates[0]["price"], "sr", tp_candidates[0]["quality_score"]
        if len(tp_candidates) >= 2:
            tp2, tp2_source, tp2_quality = tp_candidates[1]["price"], "sr", tp_candidates[1]["quality_score"]
        if tp1 is None and atr_val > 0:
            tp1, tp1_source = entry - 2 * atr_val, "atr"
        if tp2 is None and atr_val > 0:
            tp2, tp2_source = entry - 3 * atr_val, "atr"

    return {
        "entry_price": entry,
        "current_price": current_price,
        "side": side,
        "quantity": qty,
        "unrealized_pnl": round(unrealized_pnl, 2),
        "profit_pct": round(profit_pct, 2),
        "suggested_stop": round(structured_stop, 2) if structured_stop else None,
        "atr_stop_long": round(long_stop, 2) if long_stop else None,
        "atr_stop_short": round(short_stop, 2) if short_stop else None,
        "tp1": round(tp1, 2) if tp1 else None,
        "tp1_source": tp1_source,
        "tp1_quality": round(tp1_quality, 2) if tp1_quality else None,
        "tp2": round(tp2, 2) if tp2 else None,
        "tp2_source": tp2_source,
        "tp2_quality": round(tp2_quality, 2) if tp2_quality else None,
    }
