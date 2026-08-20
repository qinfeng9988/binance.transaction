"""支撑/压力位交易策略 v1.1.0（多周期趋势确认 + 质量过滤版）。

相比 v1.0.0 的升级点：
- S/R 位质量加权评分（触碰次数 + 成交量爆发 + 聚类密度）
- 拒绝蜡烛确认（Rejection Candle）
- 上级周期 EMA 锚定（4H/1H EMA21/50 同向才允许下级信号）
- ADX(14) 趋势强度过滤（>22 才允许交易）
- 价格 vs EMA21 位置过滤
- RSI(14) 动量过滤（主方向 / 逆势方向不同阈值）
- 成交量爆发确认（当前量 > 前20平均 × 1.8）
- ATR(14) 动态止损（替代固定百分比）
- 回踩/重新测试确认（Pullback/Retest）

逻辑：
- 从历史 K 线中识别高质量支撑位和压力位
- 用 EMA + ADX 判断当前周期趋势方向和强度
- 用上级周期 EMA 趋势锚定交易偏向
- 多维度过滤（RSI / 成交量 / 拒绝蜡烛 / 回踩）确认信号质量
- 动态 ATR 止损
"""

import logging

from ..models import Signal
from .base import BaseStrategy
from ..technical.indicators import ema, rsi, adx, atr, plus_di, minus_di
from ..technical.support_resistance import (
    identify_support_resistance,
    check_rejection_candle,
    check_volume_surge,
)

logger = logging.getLogger(__name__)


def _chronological(klines: list[dict]) -> list[dict]:
    """将反转后的 K 线（新→旧）恢复为时间顺序（旧→新），用于指标计算。"""
    return list(reversed(klines))


def detect_trend(klines: list[dict], threshold_pct: float = 1.0,
                 fast_period: int = 21, slow_period: int = 50) -> dict:
    """用双 EMA 交叉判断趋势方向，并返回 EMA 值。

    参数:
        klines: K 线数据（新→旧）
        threshold_pct: EMA 间距阈值（%），低于此值视为震荡
        fast_period: 快线周期
        slow_period: 慢线周期

    返回: {"trend": "uptrend"/"downtrend"/"ranging", "ema_fast": float, "ema_slow": float}
    """
    if len(klines) < slow_period:
        return {"trend": "ranging", "ema_fast": 0.0, "ema_slow": 0.0}

    # 注意：klines 由引擎传入为 新→旧 顺序，ema() 需要旧→新顺序
    ema_fast_arr = ema(_chronological(klines), fast_period)
    ema_slow_arr = ema(_chronological(klines), slow_period)

    fast = ema_fast_arr[-1]
    slow = ema_slow_arr[-1]
    gap_pct = abs(fast - slow) / slow * 100 if slow != 0 else 0

    if gap_pct < threshold_pct:
        return {"trend": "ranging", "ema_fast": fast, "ema_slow": slow}

    return {
        "trend": "uptrend" if fast > slow else "downtrend",
        "ema_fast": fast,
        "ema_slow": slow,
    }


def detect_trend_with_parent(
    klines: list[dict],
    parent_klines: list[dict] | None = None,
    require_parent_aligned: bool = True,
    threshold_pct: float = 1.0,
    fast_period: int = 21,
    slow_period: int = 50,
    trend_lookback: int | None = None,
    parent_lookback: int | None = None,
) -> dict:
    """多周期趋势确认：结合当前和上级周期趋势判断交易偏向。

    新增：上级周期 EMA 同向锚定 — 若上级 EMA21/50 不同向，则视为震荡。

    参数:
        klines: 当前周期 K 线（新→旧）
        parent_klines: 上级周期 K 线（新→旧），可为 None
        require_parent_aligned: 是否要求上级 EMA 同向
        threshold_pct: EMA 间距阈值（%），低于此值视为震荡
        trend_lookback: 当前周期趋势判断使用的 K 线数，None 表示使用全部
        parent_lookback: 上级周期趋势判断使用的 K 线数，None 表示使用全部

    返回 dict:
        trend: 当前周期趋势 ("uptrend"/"downtrend"/"ranging")
        parent_trend: 上级周期趋势（无上级数据时为 None）
        bias: 交易偏向
            - "long_only": 两周期一致上涨，只做多
            - "short_only": 两周期一致下跌，只做空
            - "long_primary": 上级上涨，做多为主做空为副
            - "short_primary": 上级下跌，做空为主做多为副
            - "neutral": 震荡或无上级数据，双向均可
        parent_aligned: 上级周期 EMA 是否同向（用于过滤）
    """
    if trend_lookback is not None:
        klines = klines[:trend_lookback]
    current = detect_trend(klines, threshold_pct=threshold_pct, fast_period=fast_period, slow_period=slow_period)

    if parent_klines is None or len(parent_klines) < 50:
        # 无上级数据，回退到单周期逻辑
        if current["trend"] == "uptrend":
            return {
                "trend": current["trend"],
                "parent_trend": None,
                "bias": "long_primary",
                "parent_aligned": None,
            }
        elif current["trend"] == "downtrend":
            return {
                "trend": current["trend"],
                "parent_trend": None,
                "bias": "short_primary",
                "parent_aligned": None,
            }
        return {
            "trend": current["trend"],
            "parent_trend": None,
            "bias": "neutral",
            "parent_aligned": None,
        }

    if parent_lookback is not None:
        parent_klines = parent_klines[:parent_lookback]
    parent = detect_trend(parent_klines, threshold_pct=threshold_pct, fast_period=fast_period, slow_period=slow_period)
    parent_aligned = parent["trend"] != "ranging"

    # 上级周期 EMA 锚定：若要求上级对齐但上级不同向，强制 neutral
    if require_parent_aligned and not parent_aligned:
        return {
            "trend": current["trend"],
            "parent_trend": parent["trend"],
            "bias": "neutral",
            "parent_aligned": False,
        }

    # 两周期一致
    if current["trend"] == parent["trend"]:
        if current["trend"] == "uptrend":
            return {
                "trend": current["trend"],
                "parent_trend": parent["trend"],
                "bias": "long_only",
                "parent_aligned": True,
            }
        elif current["trend"] == "downtrend":
            return {
                "trend": current["trend"],
                "parent_trend": parent["trend"],
                "bias": "short_only",
                "parent_aligned": True,
            }
        return {
            "trend": current["trend"],
            "parent_trend": parent["trend"],
            "bias": "neutral",
            "parent_aligned": True,
        }

    # 不一致：上级周期决定主方向
    if parent["trend"] == "uptrend":
        return {
            "trend": current["trend"],
            "parent_trend": parent["trend"],
            "bias": "long_primary",
            "parent_aligned": True,
        }
    elif parent["trend"] == "downtrend":
        return {
            "trend": current["trend"],
            "parent_trend": parent["trend"],
            "bias": "short_primary",
            "parent_aligned": True,
        }

    # 上级震荡，以当前趋势为主方向
    if current["trend"] == "uptrend":
        return {
            "trend": current["trend"],
            "parent_trend": parent["trend"],
            "bias": "long_primary",
            "parent_aligned": False,
        }
    elif current["trend"] == "downtrend":
        return {
            "trend": current["trend"],
            "parent_trend": parent["trend"],
            "bias": "short_primary",
            "parent_aligned": False,
        }
    return {
        "trend": current["trend"],
        "parent_trend": parent["trend"],
        "bias": "neutral",
        "parent_aligned": False,
    }


def check_adx_and_di(
    klines: list[dict],
    period: int = 14,
    min_adx: float = 22.0,
) -> tuple[bool, float, float, float, str | None]:
    """ADX 趋势强度过滤 + 获取 +DI/-DI 值（方向检查由调用方按信号方向决定）。

    参数:
        klines: K 线数据（新→旧）
        period: ADX 周期
        min_adx: 最低 ADX 值

    返回:
        (是否通过, ADX 当前值, +DI 当前值, -DI 当前值, 拒绝原因或 None)
    """
    if len(klines) < period * 2 + 1:
        return False, 0.0, 0.0, 0.0, "data_insufficient"

    chrono = _chronological(klines)
    adx_vals = adx(chrono, period=period)
    pdi_vals = plus_di(chrono, period=period)
    mdi_vals = minus_di(chrono, period=period)

    current_adx = float(adx_vals[-1]) if len(adx_vals) > 0 else 0.0
    current_pdi = float(pdi_vals[-1]) if len(pdi_vals) > 0 else 0.0
    current_mdi = float(mdi_vals[-1]) if len(mdi_vals) > 0 else 0.0

    if current_adx != current_adx:  # NaN check
        return False, 0.0, 0.0, 0.0, "adx_nan"

    if current_adx < min_adx:
        return False, current_adx, current_pdi, current_mdi, f"adx_low({current_adx:.1f}<{min_adx})"

    return True, current_adx, current_pdi, current_mdi, None


def check_rsi_filter(
    klines: list[dict],
    is_long: bool,
    is_primary: bool,
    period: int = 14,
    long_min: float = 45.0,
    long_max: float = 70.0,
    short_min: float = 30.0,
    short_max: float = 55.0,
    counter_long_max: float = 40.0,
    counter_short_min: float = 60.0,
) -> tuple[bool, float]:
    """RSI 动量过滤。

    主方向：RSI 在健康区间（避免超买/超卖追单）
    逆势方向：RSI 在极端区间（捕捉超卖反弹 / 超买回落）

    参数:
        klines: K 线数据（新→旧）
        is_long: 是否为做多方向
        is_primary: 是否为主方向
        period: RSI 周期
        long_min/long_max: 主方向做多 RSI 区间
        short_min/short_max: 主方向做空 RSI 区间
        counter_long_max: 逆势做多 RSI 上限（要求超卖）
        counter_short_min: 逆势做空 RSI 下限（要求超买）

    返回:
        (是否通过, RSI 当前值)
    """
    if len(klines) < period + 1:
        return False, 0.0

    rsi_vals = rsi(_chronological(klines), period=period)
    current_rsi = rsi_vals[-1] if len(rsi_vals) > 0 else 0.0

    if current_rsi is None or (isinstance(current_rsi, float) and current_rsi != current_rsi):
        return False, 0.0

    current_rsi = float(current_rsi)

    if is_long:
        if is_primary:
            passed = long_min <= current_rsi <= long_max
        else:
            # 逆势做多：要求 RSI 较低（超卖反弹）
            passed = current_rsi <= counter_long_max
    else:
        if is_primary:
            passed = short_min <= current_rsi <= short_max
        else:
            # 逆势做空：要求 RSI 较高（超买回落）
            passed = current_rsi >= counter_short_min

    return passed, current_rsi


def check_volume_burst_filter(
    klines: list[dict],
    lookback: int = 20,
    ratio: float = 1.8,
) -> tuple[bool, float]:
    """成交量爆发确认。

    参数:
        klines: K 线数据（新→旧）
        lookback: 平均成交量回看周期
        ratio: 爆发倍数阈值

    返回:
        (是否通过, 当前量/平均量比值)
    """
    has_surge, surge_ratio = check_volume_surge(klines, lookback=lookback, surge_ratio=ratio)
    return has_surge, surge_ratio


def check_price_vs_ema(
    klines: list[dict],
    is_long: bool,
    ema_period: int = 21,
) -> tuple[bool, float]:
    """价格 vs EMA 位置过滤：避免交叉后立刻反转。

    参数:
        klines: K 线数据（新→旧）
        is_long: 是否为做多方向
        ema_period: EMA 周期

    返回:
        (是否通过, 当前 EMA 值)
    """
    if len(klines) < ema_period:
        return False, 0.0

    ema_vals = ema(_chronological(klines), ema_period)
    current_ema = ema_vals[-1] if len(ema_vals) > 0 else 0.0
    current_price = klines[0]["close"]

    if is_long:
        passed = current_price > current_ema
    else:
        passed = current_price < current_ema

    return passed, float(current_ema)


def check_pullback_retest(
    klines: list[dict],
    level_price: float,
    is_long: bool,
    lookback: int = 10,
) -> bool:
    """回踩/重新测试确认。

    逻辑：价格突破 S/R 后回踩该价位，确认支撑/压力有效。
    - 做多：近期（lookback 内）有 K 线低点跌破支撑位，当前价在支撑上方
    - 做空：近期（lookback 内）有 K 线高点突破压力位，当前价在压力下方

    参数:
        klines: K 线数据（新→旧），index 0 为最新
        level_price: S/R 价位
        is_long: 是否为做多方向
        lookback: 回看周期

    返回:
        是否通过回踩确认
    """
    if len(klines) < lookback + 1:
        return False

    recent_klines = klines[1:lookback + 1]  # 排除当前 K 线

    if is_long:
        # 近期有价格跌破支撑位 = 突破后回踩
        for k in recent_klines:
            if k["low"] < level_price:
                return True
    else:
        # 近期有价格突破压力位 = 突破后回踩
        for k in recent_klines:
            if k["high"] > level_price:
                return True

    return False


def compute_trailing_stop(
    entry_price: float,
    current_price: float,
    current_atr: float,
    is_long: bool,
    breakeven_threshold_r: float = 0.8,
    breakeven_target_r: float = 1.8,
    trail_start_r: float = 1.8,
    trail_distance_r: float = 1.8,
    wide_trail_start_r: float = 3.5,
    wide_trail_distance_r: float = 2.5,
) -> float | None:
    """四档跟踪止损计算。

    返回新的止损位；如果不需要更新（未达到第一档），返回 None。
    止损只能向有利方向移动（做多时只能上移，做空时只能下移）。
    """
    if current_atr <= 0:
        return None

    profit = current_price - entry_price if is_long else entry_price - current_price
    profit_r = profit / current_atr

    if profit_r < breakeven_threshold_r:
        return None
    elif profit_r < breakeven_target_r:
        new_stop = entry_price
    elif profit_r < wide_trail_start_r:
        new_stop = current_price - trail_distance_r * current_atr if is_long else current_price + trail_distance_r * current_atr
    else:
        new_stop = current_price - wide_trail_distance_r * current_atr if is_long else current_price + wide_trail_distance_r * current_atr

    return new_stop


def compute_structured_stop(
    entry_price: float,
    current_price: float,
    current_atr: float,
    current_stop: float,
    is_long: bool,
    levels: list[dict],
    min_r: float = 1.5,
    buffer_pct: float = 0.3,
    quality_score: float = 0.0,
    quality_buffer_pct: float = 0.2,
) -> float | None:
    """基于 S/R 位的结构化止损。

    当盈利 >= min_r 后，将止损移动到最近的次要 S/R 位附近。
    高质量信号允许更大缓冲，避免被正常震荡扫出。

    返回新的止损位；如果不满足条件或新止损不比当前好，返回 None。
    """
    if current_atr <= 0:
        return None

    profit = current_price - entry_price if is_long else entry_price - current_price
    profit_r = profit / current_atr
    if profit_r < min_r:
        return None

    if not levels:
        return None

    # 计算有效缓冲：高质量位允许更大空间
    effective_buffer = buffer_pct
    if quality_score >= 6.0:
        effective_buffer += quality_buffer_pct

    candidate_stop = None
    if is_long:
        # 做多：找低于当前价的最近支撑，按价格降序（最接近当前价的优先）
        supports = [lvl for lvl in levels if lvl["price"] < current_price]
        supports.sort(key=lambda x: x["price"], reverse=True)
        if supports:
            s_price = supports[0]["price"]
            # 止损设在支撑位下方 buffer%，给支撑留空间
            candidate_stop = s_price * (1 - effective_buffer / 100)
            # 但不能低于保本价（入场价）
            candidate_stop = max(candidate_stop, entry_price)
    else:
        # 做空：找高于当前价的最近压力，按价格升序
        resistances = [lvl for lvl in levels if lvl["price"] > current_price]
        resistances.sort(key=lambda x: x["price"])
        if resistances:
            r_price = resistances[0]["price"]
            candidate_stop = r_price * (1 + effective_buffer / 100)
            # 不能高于保本价
            candidate_stop = min(candidate_stop, entry_price)

    if candidate_stop is None:
        return None

    # 止损只能向有利方向移动
    if is_long and candidate_stop > current_stop:
        return candidate_stop
    if not is_long and candidate_stop < current_stop:
        return candidate_stop

    return None


def compute_dynamic_stop_loss(
    klines: list[dict],
    entry_price: float,
    is_long: bool,
    atr_period: int = 14,
    atr_multiplier: float = 1.5,
    fallback_pct: float = 1.5,
) -> float:
    """ATR 动态止损计算。

    参数:
        klines: K 线数据（新→旧）
        entry_price: 入场价格
        is_long: 是否为做多
        atr_period: ATR 周期
        atr_multiplier: ATR 倍数
        fallback_pct: ATR 不可用时回退到固定百分比

    返回:
        止损价格
    """
    if len(klines) < atr_period + 1:
        # ATR 数据不足，回退到固定百分比
        if is_long:
            return entry_price * (1 - fallback_pct / 100)
        return entry_price * (1 + fallback_pct / 100)

    atr_vals = atr(_chronological(klines), period=atr_period)
    current_atr = atr_vals[-1] if len(atr_vals) > 0 else 0.0

    if current_atr is None or (isinstance(current_atr, float) and current_atr != current_atr) or current_atr <= 0:
        # ATR 无效，回退到固定百分比
        if is_long:
            return entry_price * (1 - fallback_pct / 100)
        return entry_price * (1 + fallback_pct / 100)

    atr_distance = current_atr * atr_multiplier

    if is_long:
        return entry_price - atr_distance
    return entry_price + atr_distance


def _get_pos_attr(pos, key, default=None):
    """统一读取持仓属性，支持 dict 和 dataclass 对象。"""
    if pos is None:
        return default
    if isinstance(pos, dict):
        return pos.get(key, default)
    return getattr(pos, key, default)


class SupportResistanceStrategy(BaseStrategy):
    """基于支撑/压力位的交易策略 v1.1.0，带多周期趋势确认与多维度质量过滤。"""

    def on_bar(self, symbol: str, timeframe: str, klines: list[dict],
               positions: list, parent_klines: list[dict] | None = None,
               grandparent_klines: list[dict] | None = None,
               confirm_klines: list[dict] | None = None,
               funding_rate: float | None = None) -> list[Signal]:
        if len(klines) < 50:
            return []

        # 惰性初始化累计过滤统计（用于回测结束后的汇总分析）
        if not hasattr(self, "_filter_summary"):
            self._filter_summary = {
                "buy": {"checked": 0, "distance": 0, "adx": 0, "di": 0, "quality": 0, "ema": 0, "rsi": 0, "volume": 0, "rejection": 0, "pullback": 0, "fr_hard_block": 0, "fr_penalty": 0, "add": 0, "add_limit": 0, "add_quality": 0, "passed": 0},
                "sell": {"checked": 0, "distance": 0, "adx": 0, "di": 0, "quality": 0, "ema": 0, "rsi": 0, "volume": 0, "rejection": 0, "pullback": 0, "fr_hard_block": 0, "fr_penalty": 0, "add": 0, "add_limit": 0, "add_quality": 0, "passed": 0},
            }

        params = self.params

        # ---- S/R 检测参数 ----
        sr_lookback = params.get("sr_lookback", params.get("lookback", 100))
        trend_lookback = params.get("trend_lookback", None)
        parent_lookback = params.get("parent_lookback", None)
        min_touches = params.get("min_touches", 3)  # v1.1: 升级为质量分阈值
        entry_threshold_pct = params.get("entry_threshold_pct", 0.5)
        secondary_min_touches = params.get("secondary_min_touches", 4)
        secondary_entry_threshold_pct = params.get("secondary_entry_threshold_pct", 0.3)
        require_rejection = params.get("require_rejection_candle", False)
        rejection_min_wick_ratio = params.get("rejection_min_wick_ratio", 2.0)
        rejection_max_body_ratio = params.get("rejection_max_body_ratio", 0.5)
        trend_bonus_aligned = params.get("trend_bonus_aligned", 1.3)
        trend_bonus_counter = params.get("trend_bonus_counter", -0.5)

        # ---- 趋势过滤参数 ----
        require_parent_aligned = params.get("require_parent_ema_aligned", True)
        ema_gap_threshold = params.get("ema_gap_threshold_pct", 1.0)
        ema_fast = params.get("ema_fast_period", 21)
        ema_slow = params.get("ema_slow_period", 50)
        adx_period = params.get("adx_period", 14)
        adx_min = params.get("adx_min", 22.0)
        adx_min_high_quality = params.get("adx_min_high_quality", adx_min)
        price_vs_ema_filter = params.get("price_vs_ema_filter", True)
        di_tolerance = params.get("di_tolerance", 2.0)
        quality_threshold_for_lower_adx = params.get("quality_threshold_for_lower_adx", 5.5)

        # ---- 动量/波动参数 ----
        rsi_period = params.get("rsi_period", 14)
        rsi_long_min = params.get("rsi_long_min", 45.0)
        rsi_long_max = params.get("rsi_long_max", 70.0)
        rsi_short_min = params.get("rsi_short_min", 30.0)
        rsi_short_max = params.get("rsi_short_max", 55.0)
        rsi_counter_long_max = params.get("rsi_countertrend_long_max", 40.0)
        rsi_counter_short_min = params.get("rsi_countertrend_short_min", 60.0)
        volume_burst_ratio = params.get("volume_burst_ratio", 1.8)
        volume_burst_lookback = params.get("volume_burst_lookback", 20)

        # ---- 逆势交易额外过滤 ----
        secondary_min_quality = params.get("secondary_min_quality", 5.0)
        secondary_volume_ratio = params.get("secondary_volume_ratio", 1.6)

        # ---- ATR 动态止损 ----
        use_atr_stop = params.get("use_atr_stop", True)
        atr_period = params.get("atr_period", 14)
        atr_multiplier = params.get("atr_multiplier", 1.5)
        fallback_stop_pct = params.get("stop_loss_pct", 1.5)

        # ---- 跟踪止损（向后兼容，结构化止损优先） ----
        trailing_stop_enabled = params.get("trailing_stop_enabled", False)
        trailing_breakeven_threshold_r = params.get("trailing_breakeven_threshold_r", 0.8)
        trailing_breakeven_target_r = params.get("trailing_breakeven_target_r", 1.8)
        trailing_start_r = params.get("trailing_start_r", 1.8)
        trailing_distance_r = params.get("trailing_distance_r", 1.8)
        trailing_wide_start_r = params.get("trailing_wide_start_r", 3.5)
        trailing_wide_distance_r = params.get("trailing_wide_distance_r", 2.5)

        # ---- 结构化止损 ----
        structured_stop_enabled = params.get("structured_stop_enabled", False)
        structured_stop_min_r = params.get("structured_stop_min_r", 1.5)
        structured_stop_buffer_pct = params.get("structured_stop_buffer_pct", 0.3)
        structured_stop_quality_buffer_pct = params.get("structured_stop_quality_buffer_pct", 0.2)

        # ---- 部分止盈 ----
        partial_tp_enabled = params.get("partial_tp_enabled", False)
        partial_tp_levels = params.get("partial_tp_levels", [
            {"r_threshold": 2.0, "close_pct": 0.4},
            {"r_threshold": 3.5, "close_pct": 0.3},
        ])

        # ---- 回踩确认 ----
        require_pullback = params.get("require_pullback_retest", True)
        pullback_lookback = params.get("pullback_lookback", 10)

        # ---- S/R 检测增强 ----
        adaptive_swing_window = params.get("adaptive_swing_window", False)
        swing_window_adx_threshold = params.get("swing_window_adx_threshold", 25.0)
        swing_window_strong = params.get("swing_window_strong", 3)
        swing_window_weak = params.get("swing_window_weak", 2)
        use_order_blocks = params.get("use_order_blocks", False)
        use_fvg = params.get("use_fvg", False)

        # ---- Volume Profile VAH/VAL 增强 ----
        use_vah_val = params.get("use_vah_val", False)
        value_area_pct = params.get("value_area_pct", 0.70)
        vah_val_near_threshold_pct = params.get("vah_val_near_threshold_pct", 0.005)
        poc_near_threshold_pct = params.get("poc_near_threshold_pct", 0.005)
        poc_far_threshold_pct = params.get("poc_far_threshold_pct", 0.007)
        vah_val_near_bonus = params.get("vah_val_near_bonus", 0.5)
        vah_val_inner_bonus = params.get("vah_val_inner_bonus", 0.3)

        leverage = params.get("leverage", 1)
        ranging_leverage_multiplier = params.get("ranging_leverage_multiplier", 1.0)
        ranging_atr_multiplier = params.get("ranging_atr_multiplier", None)
        current_price = klines[0]["close"]

        # ---- 多周期共振参数 ----
        use_resonance = params.get("use_resonance", False)
        resonance_timeframe = params.get("resonance_timeframe", "15m")
        resonance_ema_fast = params.get("resonance_ema_fast_period", 9)
        resonance_ema_slow = params.get("resonance_ema_slow_period", 21)
        resonance_ema_gap = params.get("resonance_ema_gap_threshold_pct", 0.5)
        resonance_sr_threshold_pct = params.get("resonance_sr_threshold_pct", 2.0)
        resonance_min_touches = params.get("resonance_min_touches", 3)
        resonance_lookback = params.get("resonance_lookback", 100)

        # ---- 加仓控制参数 ----
        max_additions_ranging = params.get("max_additions_ranging", 1)
        max_additions_trending = params.get("max_additions_trending", 1)
        add_quality_tier2 = params.get("add_quality_tier2", 6.0)

        # ---- 强趋势逆势过滤 ----
        strict_bias_mode = params.get("strict_bias_mode", False)

        # ---- Funding Rate 过滤参数 ----
        funding_filter_enabled = params.get("funding_filter_enabled", False)
        funding_long_penalty_threshold = params.get("funding_long_penalty_threshold", 0.05)
        funding_long_hard_block_threshold = params.get("funding_long_hard_block_threshold", 0.10)
        funding_short_block_threshold = params.get("funding_short_block_threshold", -0.05)
        funding_short_hard_block_threshold = params.get("funding_short_hard_block_threshold", -0.10)
        funding_long_quality_penalty = params.get("funding_long_quality_penalty", -1.0)
        funding_long_leverage_reduction_enabled = params.get("funding_long_leverage_reduction_enabled", False)
        funding_long_leverage_reduction = params.get("funding_long_leverage_reduction", 0.6)

        # ========== 1. 多周期趋势确认 ==========
        trend_info = detect_trend_with_parent(
            klines, parent_klines,
            require_parent_aligned=require_parent_aligned,
            threshold_pct=ema_gap_threshold,
            fast_period=ema_fast,
            slow_period=ema_slow,
            trend_lookback=trend_lookback,
            parent_lookback=parent_lookback,
        )
        trend = trend_info["trend"]
        bias = trend_info["bias"]
        parent_trend = trend_info["parent_trend"]
        parent_aligned = trend_info["parent_aligned"]

        # ========== 2. ADX + DI 趋势强度过滤（DI方向在信号层检查）==========
        # 先用最低阈值（高质量位阈值）做全局 ADX 过滤
        adx_passed, current_adx, current_pdi, current_mdi, adx_reason = check_adx_and_di(
            klines, period=adx_period, min_adx=adx_min_high_quality,
        )
        if not adx_passed:
            logger.debug(
                "ADX 过滤 %s %s: %s, ADX=%.1f, +DI=%.1f, -DI=%.1f, bias=%s",
                symbol, timeframe, adx_reason, current_adx, current_pdi, current_mdi, bias,
            )
            return []

        # 计算当前 ATR（用于跟踪止损）
        atr_vals = atr(_chronological(klines), period=atr_period)
        current_atr = float(atr_vals[-1]) if len(atr_vals) > 0 else 0.0
        if current_atr != current_atr:  # NaN check
            current_atr = 0.0

        # ========== 2b. 震荡过滤 —— ranging 时不交易（1h/4h 等长周期适用）==========
        # 5m 等高噪音周期不判断自身趋势，只依赖 parent 趋势方向
        if trend == "ranging" and timeframe not in ("1m", "3m", "5m", "1h"):
            logger.debug(
                "震荡过滤 %s %s: 趋势=ranging，跳过",
                symbol, timeframe,
            )
            return []

        # 上级周期也是震荡时，不交易（无方向）
        if parent_trend == "ranging":
            logger.debug(
                "上级震荡过滤 %s %s: parent_trend=ranging，跳过",
                symbol, timeframe,
            )
            return []

        # ========== 2c. 震荡期仓位控制（v1.4.1+：增强上级周期锚定）==========
        use_parent_trend_override = params.get("use_parent_trend_override", False)
        ranging_adx_relax_factor = params.get("ranging_adx_relax_factor", 1.0)

        if use_parent_trend_override and parent_trend in ("uptrend", "downtrend"):
            # 上级周期有明确趋势：放宽当前周期的 ranging 判定
            # 只有当前ADX < 放宽阈值 AND 自身trend=ranging 时才视为震荡
            effective_adx_min = adx_min * ranging_adx_relax_factor
            is_ranging = (current_adx < effective_adx_min) and (trend == "ranging")
            if not is_ranging and (current_adx < adx_min or trend == "ranging"):
                logger.debug(
                    "趋势锚定 %s %s: parent=%s, 自身ADX=%.1f/trend=%s, "
                    "放宽后不计为震荡(eff_adx_min=%.1f)",
                    symbol, timeframe, parent_trend, current_adx, trend,
                    effective_adx_min,
                )
        else:
            # 原逻辑：上级也是ranging或无parent时，使用严格判定
            is_ranging = (current_adx < adx_min) or (trend == "ranging")

        original_leverage = leverage
        if is_ranging and ranging_leverage_multiplier < 1.0:
            leverage = round(leverage * ranging_leverage_multiplier)
            if leverage < 1:
                leverage = 1
            logger.info(
                "震荡期降仓 %s %s: ADX=%.1f, trend=%s, 杠杆 %dx → %dx (x%.2f)",
                symbol, timeframe, current_adx, trend,
                original_leverage, leverage, ranging_leverage_multiplier,
            )

        # 震荡期止损收紧
        effective_atr_multiplier = atr_multiplier
        if is_ranging and ranging_atr_multiplier is not None:
            effective_atr_multiplier = ranging_atr_multiplier
            logger.info(
                "震荡期收紧止损 %s %s: ATR倍数 %.1f → %.1f",
                symbol, timeframe, atr_multiplier, effective_atr_multiplier,
            )

        # ========== 3. 识别高质量 S/R 位 ==========
        levels = identify_support_resistance(
            klines,
            min_touches=min_touches,
            lookback=sr_lookback,
            volume_surge_ratio=1.5,
            require_rejection=require_rejection,
            bias=bias,
            trend_bonus_aligned=trend_bonus_aligned,
            trend_bonus_counter=trend_bonus_counter,
            adaptive_swing_window=adaptive_swing_window,
            swing_window_adx_threshold=swing_window_adx_threshold,
            swing_window_strong=swing_window_strong,
            swing_window_weak=swing_window_weak,
            use_order_blocks=use_order_blocks,
            use_fvg=use_fvg,
            rejection_min_wick_ratio=rejection_min_wick_ratio,
            rejection_max_body_ratio=rejection_max_body_ratio,
            use_vah_val=use_vah_val,
            value_area_pct=value_area_pct,
            vah_val_near_threshold_pct=vah_val_near_threshold_pct,
            poc_near_threshold_pct=poc_near_threshold_pct,
            poc_far_threshold_pct=poc_far_threshold_pct,
            vah_val_near_bonus=vah_val_near_bonus,
            vah_val_inner_bonus=vah_val_inner_bonus,
        )

        # ========== 4. 确定交易方向与主次 ==========
        can_buy = False
        can_sell = False
        buy_is_primary = False
        sell_is_primary = False

        if bias == "long_only":
            can_buy, buy_is_primary = True, True
        elif bias == "short_only":
            can_sell, sell_is_primary = True, True
        elif bias == "long_primary":
            can_buy, buy_is_primary = True, True
            can_sell, sell_is_primary = True, False
        elif bias == "short_primary":
            can_buy, buy_is_primary = True, False
            can_sell, sell_is_primary = True, True
        elif bias == "neutral":
            can_buy, buy_is_primary = True, True
            can_sell, sell_is_primary = True, True

        # ---- strict_bias_mode: 上级明确趋势 + 当前ADX较强时，禁止逆势交易 ----
        if strict_bias_mode and parent_trend in ("uptrend", "downtrend") and current_adx >= adx_min:
            if bias in ("long_only", "long_primary"):
                if can_sell:
                    logger.info(
                        "strict_bias 过滤 %s %s: parent=%s, ADX=%.1f, bias=%s, 禁止逆势做空",
                        symbol, timeframe, parent_trend, current_adx, bias,
                    )
                can_sell = False
                sell_is_primary = False
            elif bias in ("short_only", "short_primary"):
                if can_buy:
                    logger.info(
                        "strict_bias 过滤 %s %s: parent=%s, ADX=%.1f, bias=%s, 禁止逆势做多",
                        symbol, timeframe, parent_trend, current_adx, bias,
                    )
                can_buy = False
                buy_is_primary = False

        # 过滤统计（用于诊断分析：每个 K 线周期记录各过滤层的拦截数）
        filter_stats = {
            "buy": {"checked": 0, "distance": 0, "adx": 0, "di": 0, "quality": 0, "ema": 0, "rsi": 0, "volume": 0, "rejection": 0, "pullback": 0, "fr_hard_block": 0, "fr_penalty": 0, "add": 0, "add_limit": 0, "add_quality": 0, "passed": 0},
            "sell": {"checked": 0, "distance": 0, "adx": 0, "di": 0, "quality": 0, "ema": 0, "rsi": 0, "volume": 0, "rejection": 0, "pullback": 0, "fr_hard_block": 0, "fr_penalty": 0, "add": 0, "add_limit": 0, "add_quality": 0, "passed": 0},
        }

        # ========== 4a. Funding Rate 过滤（硬阈值：直接阻断方向）==========
        if funding_filter_enabled and funding_rate is not None:
            # 正 funding 过高：禁止做多
            if funding_rate > funding_long_hard_block_threshold:
                logger.info(
                    "[FR过滤] %s %s: funding=%.4f%% > 硬阈值%.2f%%, 禁止做多",
                    symbol, timeframe, funding_rate, funding_long_hard_block_threshold,
                )
                can_buy = False
                buy_is_primary = False
                filter_stats["buy"]["fr_hard_block"] += 1
            # 负 funding 过低：禁止做空
            if funding_rate < funding_short_hard_block_threshold:
                logger.info(
                    "[FR过滤] %s %s: funding=%.4f%% < 硬阈值%.2f%%, 禁止做空",
                    symbol, timeframe, funding_rate, funding_short_hard_block_threshold,
                )
                can_sell = False
                sell_is_primary = False
                filter_stats["sell"]["fr_hard_block"] += 1

        # ========== 4b. 多周期共振确认（可选）==========
        if use_resonance and confirm_klines and len(confirm_klines) >= 50:
            # 检测确认周期趋势
            confirm_trend_info = detect_trend(
                confirm_klines,
                threshold_pct=resonance_ema_gap,
                fast_period=resonance_ema_fast,
                slow_period=resonance_ema_slow,
            )
            confirm_trend = confirm_trend_info["trend"]

            # 检测确认周期 S/R 位
            confirm_levels = identify_support_resistance(
                confirm_klines,
                min_touches=resonance_min_touches,
                lookback=resonance_lookback,
                volume_surge_ratio=1.5,
                require_rejection=False,
                bias=bias,
                trend_bonus_aligned=trend_bonus_aligned,
                trend_bonus_counter=trend_bonus_counter,
                adaptive_swing_window=adaptive_swing_window,
                swing_window_adx_threshold=swing_window_adx_threshold,
                swing_window_strong=swing_window_strong,
                swing_window_weak=swing_window_weak,
                use_order_blocks=use_order_blocks,
                use_fvg=use_fvg,
                rejection_min_wick_ratio=rejection_min_wick_ratio,
                rejection_max_body_ratio=rejection_max_body_ratio,
                use_vah_val=use_vah_val,
                value_area_pct=value_area_pct,
                vah_val_near_threshold_pct=vah_val_near_threshold_pct,
            )

            # 检查当前价格是否靠近确认周期的 S/R 位
            resonance_buy_ok = False
            resonance_sell_ok = False

            for support in confirm_levels["support"]:
                s_price = support["price"]
                distance_pct = (current_price - s_price) / s_price * 100
                if 0 <= distance_pct <= resonance_sr_threshold_pct:
                    resonance_buy_ok = True
                    break

            for resistance in confirm_levels["resistance"]:
                r_price = resistance["price"]
                distance_pct = (r_price - current_price) / r_price * 100
                if 0 <= distance_pct <= resonance_sr_threshold_pct:
                    resonance_sell_ok = True
                    break

            # 趋势同向 + 靠近 S/R 位
            if can_buy and confirm_trend not in ("uptrend", "long_primary", "long_only", "neutral"):
                resonance_buy_ok = False
            if can_sell and confirm_trend not in ("downtrend", "short_primary", "short_only", "neutral"):
                resonance_sell_ok = False

            if not resonance_buy_ok:
                can_buy = False
            if not resonance_sell_ok:
                can_sell = False

            logger.debug(
                "共振确认 %s %s: confirm_trend=%s, buy_ok=%s, sell_ok=%s",
                symbol, timeframe, confirm_trend, resonance_buy_ok, resonance_sell_ok,
            )

        signals = []

        # 查找当前 symbol 的现有持仓（支持 dict 和 dataclass）
        existing_pos = None
        for pos in positions:
            if _get_pos_attr(pos, "symbol") == symbol:
                existing_pos = pos
                break

        # ========== 5. 做多信号 ==========
        # strict_bias_mode 只拦截逆势新开仓，但允许反向平仓
        if can_buy or (existing_pos and _get_pos_attr(existing_pos, "side") == "sell"):
            touches = min_touches if buy_is_primary else secondary_min_touches
            threshold = entry_threshold_pct if buy_is_primary else secondary_entry_threshold_pct

            buy_levels = identify_support_resistance(
                klines,
                min_touches=touches,
                lookback=sr_lookback,
                volume_surge_ratio=1.5,
                require_rejection=require_rejection,
                bias=bias,
                trend_bonus_aligned=trend_bonus_aligned,
                trend_bonus_counter=trend_bonus_counter,
                adaptive_swing_window=adaptive_swing_window,
                swing_window_adx_threshold=swing_window_adx_threshold,
                swing_window_strong=swing_window_strong,
                swing_window_weak=swing_window_weak,
                use_order_blocks=use_order_blocks,
                use_fvg=use_fvg,
                rejection_min_wick_ratio=rejection_min_wick_ratio,
                rejection_max_body_ratio=rejection_max_body_ratio,
                use_vah_val=use_vah_val,
                value_area_pct=value_area_pct,
                vah_val_near_threshold_pct=vah_val_near_threshold_pct,
            )

            for support in buy_levels["support"]:
                filter_stats["buy"]["checked"] += 1
                s_price = support["price"]
                distance_pct = (current_price - s_price) / s_price * 100

                if not (0 <= distance_pct <= threshold):
                    filter_stats["buy"]["distance"] += 1
                    continue

                quality_score = support.get("quality_score", 0)

                # --- 4x. Funding Rate 软阈值：扣分 + 降杠杆 ---
                original_leverage_for_log = leverage
                if funding_filter_enabled and funding_rate is not None:
                    if funding_rate > funding_long_penalty_threshold:
                        quality_score += funding_long_quality_penalty
                        filter_stats["buy"]["fr_penalty"] += 1
                        logger.debug(
                            "FR软阈值 %s %s 做多: funding=%.4f%% > %.2f%%, quality %.1f → %.1f",
                            symbol, timeframe, funding_rate, funding_long_penalty_threshold,
                            quality_score - funding_long_quality_penalty, quality_score,
                        )
                        if funding_long_leverage_reduction_enabled:
                            leverage = max(1, int(leverage * funding_long_leverage_reduction))
                            logger.debug(
                                "FR降杠杆 %s %s 做多: %dx → %dx",
                                symbol, timeframe, original_leverage_for_log, leverage,
                            )

                # --- 4y. 质量分级 ADX 检查 ---
                effective_adx_min = adx_min_high_quality if quality_score >= quality_threshold_for_lower_adx else adx_min
                if current_adx < effective_adx_min:
                    filter_stats["buy"]["adx"] += 1
                    logger.debug(
                        "ADX分级过滤 %s %s 做多: quality=%.1f < %.1f, "
                        "ADX=%.1f < 要求=%.1f(+DI=%.1f,−DI=%.1f)",
                        symbol, timeframe, quality_score, quality_threshold_for_lower_adx,
                        current_adx, effective_adx_min, current_pdi, current_mdi,
                    )
                    continue

                # --- 4y. +DI/-DI 方向确认 + 逆势质量门槛 ---
                if buy_is_primary:
                    # 主方向（顺势）：严格 DI 确认
                    if current_pdi <= current_mdi + di_tolerance:
                        filter_stats["buy"]["di"] += 1
                        logger.debug(
                            "+DI/-DI 过滤 %s %s 做多(主): +DI=%.1f <= -DI=%.1f + %.1f, "
                            "quality=%.1f, ADX=%.1f",
                            symbol, timeframe, current_pdi, current_mdi, di_tolerance,
                            quality_score, current_adx,
                        )
                        continue
                else:
                    # 副方向（逆势）：不检查 DI，但要求更高质量 + 更严成交量
                    if quality_score < secondary_min_quality:
                        filter_stats["buy"]["quality"] += 1
                        logger.debug(
                            "质量过滤 %s %s 做多(副): quality=%.1f < %.1f, "
                            "ADX=%.1f",
                            symbol, timeframe, quality_score, secondary_min_quality,
                            current_adx,
                        )
                        continue

                # --- 5a. 价格 vs EMA21 位置过滤 ---
                if price_vs_ema_filter:
                    ema_passed, current_ema = check_price_vs_ema(klines, is_long=True, ema_period=21)
                    if not ema_passed:
                        filter_stats["buy"]["ema"] += 1
                        logger.debug(
                            "EMA 过滤 %s %s 做多: 价格=%.2f < EMA21=%.2f, "
                            "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                            symbol, timeframe, current_price, current_ema,
                            quality_score, current_adx, current_pdi, current_mdi,
                        )
                        continue

                # --- 5b. RSI 动量过滤 ---
                rsi_passed, current_rsi = check_rsi_filter(
                    klines,
                    is_long=True,
                    is_primary=buy_is_primary,
                    period=rsi_period,
                    long_min=rsi_long_min,
                    long_max=rsi_long_max,
                    counter_long_max=rsi_counter_long_max,
                )
                if not rsi_passed:
                    filter_stats["buy"]["rsi"] += 1
                    logger.debug(
                        "RSI 过滤 %s %s 做多: RSI=%.1f, primary=%s, "
                        "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                        symbol, timeframe, current_rsi, buy_is_primary,
                        quality_score, current_adx, current_pdi, current_mdi,
                    )
                    continue

                # --- 5c. 成交量爆发确认（主/副方向不同阈值）---
                effective_vol_ratio = volume_burst_ratio if buy_is_primary else secondary_volume_ratio
                vol_passed, vol_ratio = check_volume_burst_filter(
                    klines, lookback=volume_burst_lookback, ratio=effective_vol_ratio,
                )
                if not vol_passed:
                    filter_stats["buy"]["volume"] += 1
                    logger.debug(
                        "成交量过滤 %s %s 做多(%s): 量比=%.2fx < 要求=%.2fx, "
                        "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                        symbol, timeframe, "主" if buy_is_primary else "副",
                        vol_ratio, effective_vol_ratio,
                        quality_score, current_adx, current_pdi, current_mdi,
                    )
                    continue

                # --- 5d. 拒绝蜡烛确认（已在 S/R 检测中处理，此处可额外检查当前 K 线） ---
                if require_rejection and not support.get("has_rejection", False):
                    # 额外检查当前 K 线
                    if not check_rejection_candle(
                        klines[0], "support",
                        min_wick_ratio=rejection_min_wick_ratio,
                        max_body_ratio=rejection_max_body_ratio,
                    ):
                        filter_stats["buy"]["rejection"] += 1
                        logger.debug(
                            "拒绝蜡烛过滤 %s %s 做多: 无拒绝蜡烛, "
                            "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                            symbol, timeframe,
                            quality_score, current_adx, current_pdi, current_mdi,
                        )
                        continue

                # --- 5e. 回踩确认 ---
                if require_pullback:
                    retest_passed = check_pullback_retest(klines, s_price, is_long=True, lookback=pullback_lookback)
                    if not retest_passed:
                        filter_stats["buy"]["pullback"] += 1
                        logger.debug(
                            "回踩过滤 %s %s 做多: 无有效回踩, "
                            "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                            symbol, timeframe,
                            quality_score, current_adx, current_pdi, current_mdi,
                        )
                        continue

                # --- 5f. 计算动态止损 ---
                if use_atr_stop:
                    stop_loss = compute_dynamic_stop_loss(
                        klines, current_price, is_long=True,
                        atr_period=atr_period, atr_multiplier=effective_atr_multiplier,
                        fallback_pct=fallback_stop_pct,
                    )
                else:
                    stop_loss = s_price * (1 - fallback_stop_pct / 100)

                # --- 加仓条件检查 ---
                if existing_pos and _get_pos_attr(existing_pos, "side") == "buy":
                    # 基础质量门槛（第1次加仓）
                    if quality_score < 6.5:
                        filter_stats["buy"]["add"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做多: quality=%.1f < 6.5",
                            symbol, timeframe, quality_score,
                        )
                        continue
                    # 价格必须优于持仓价（震荡期严格要求，趋势期可放宽追仓）
                    trend_addition_allow_chase = params.get("trend_addition_allow_chase", False)
                    if trend_addition_allow_chase and not is_ranging:
                        # 趋势期允许追多：跳过价格条件
                        pass
                    elif current_price >= _get_pos_attr(existing_pos, "entry_price", current_price):
                        filter_stats["buy"]["add"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做多: 价格=%.2f 不优于持仓价=%.2f",
                            symbol, timeframe, current_price, _get_pos_attr(existing_pos, "entry_price"),
                        )
                        continue
                    # 加仓次数上限（震荡期 vs 趋势期）
                    add_count = _get_pos_attr(existing_pos, "add_count", 0)
                    max_add = max_additions_ranging if is_ranging else max_additions_trending
                    if add_count >= max_add:
                        filter_stats["buy"]["add_limit"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做多: 已达最大次数 %d/%d (%s)",
                            symbol, timeframe, add_count, max_add,
                            "震荡" if is_ranging else "趋势",
                        )
                        continue
                    # 第2次及以上加仓额外质量门槛
                    if add_count >= 1 and quality_score < add_quality_tier2:
                        filter_stats["buy"]["add_quality"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做多: 第%d次加仓质量=%.1f < %.1f",
                            symbol, timeframe, add_count + 1, quality_score, add_quality_tier2,
                        )
                        continue

                base_score = support.get("base_score", 0)
                t_bonus = support.get("trend_bonus", 0)
                r_bonus = support.get("recency_bonus", 0)

                signal = Signal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    signal_type="buy",
                    price=current_price,
                    stop_loss=stop_loss,
                    leverage=leverage,
                    quality_score=round(quality_score, 2),
                    adx=round(current_adx, 2),
                    plus_di=round(current_pdi, 2),
                    minus_di=round(current_mdi, 2),
                    rsi=round(current_rsi, 2),
                    volume_ratio=round(vol_ratio, 2),
                    parent_trend=parent_trend,
                    direction_type="main" if buy_is_primary else "secondary",
                    is_counter_trend=not parent_aligned,
                    atr=round(current_atr, 8) if current_atr else None,
                    entry_threshold_pct=round(threshold, 4),
                    distance_pct=round(distance_pct, 4),
                    metadata={
                        "support_level": s_price,
                        "support_strength": support["strength"],
                        "base_score": base_score,
                        "trend_bonus": t_bonus,
                        "recency_bonus": r_bonus,
                        "distance_to_support_pct": round(distance_pct, 3),
                        "trend": trend,
                        "parent_trend": parent_trend,
                        "parent_aligned": parent_aligned,
                        "bias": bias,
                        "is_primary": buy_is_primary,
                        "adx": round(current_adx, 1),
                        "plus_di": round(current_pdi, 1),
                        "minus_di": round(current_mdi, 1),
                        "rsi": round(current_rsi, 1),
                        "volume_ratio": round(vol_ratio, 2),
                        "has_rejection": support.get("has_rejection", False),
                        "stop_loss_type": "atr" if use_atr_stop else "fixed",
                        "max_additions": max_additions_ranging if is_ranging else max_additions_trending,
                        "add_count": _get_pos_attr(existing_pos, "add_count", 0) if existing_pos else 0,
                        "is_ranging": is_ranging,
                        "funding_rate": round(funding_rate, 4) if funding_rate is not None else None,
                        "funding_penalty_applied": funding_filter_enabled and funding_rate is not None and funding_rate > funding_long_penalty_threshold,
                        "pre_fr_leverage": original_leverage_for_log,
                    },
                )
                signals.append(signal)
                filter_stats["buy"]["passed"] += 1
                fr_info = ""
                if funding_filter_enabled and funding_rate is not None and funding_rate > funding_long_penalty_threshold:
                    fr_info = f", FR={funding_rate:.4f}%→penalty={funding_long_quality_penalty}"
                    if funding_long_leverage_reduction_enabled and leverage < original_leverage_for_log:
                        fr_info += f", lev={original_leverage_for_log}x→{leverage}x"
                logger.info(
                    "做多信号 %s %s: 现价=%.2f, 支撑=%.2f, 距离=%.3f%%, "
                    "趋势=%s, 上级=%s, 偏向=%s, %s, 止损=%.2f, "
                    "base=%.1f trend=%+.1f recency=%+.1f final=%.1f, "
                    "ADX=%.1f, +DI=%.1f, −DI=%.1f, RSI=%.1f, 量比=%.2f%s",
                    symbol, timeframe, current_price, s_price, distance_pct,
                    trend, parent_trend, bias,
                    "主方向" if buy_is_primary else "副方向(严格条件)",
                    stop_loss, base_score, t_bonus, r_bonus, quality_score,
                    current_adx, current_pdi, current_mdi,
                    current_rsi, vol_ratio, fr_info,
                )
                break  # 只取最近的一个支撑

        # ========== 6. 做空信号 ==========
        # strict_bias_mode 只拦截逆势新开仓，但允许反向平仓
        if can_sell or (existing_pos and _get_pos_attr(existing_pos, "side") == "buy"):
            touches = min_touches if sell_is_primary else secondary_min_touches
            threshold = entry_threshold_pct if sell_is_primary else secondary_entry_threshold_pct

            sell_levels = identify_support_resistance(
                klines,
                min_touches=touches,
                lookback=sr_lookback,
                volume_surge_ratio=1.5,
                require_rejection=require_rejection,
                bias=bias,
                trend_bonus_aligned=trend_bonus_aligned,
                trend_bonus_counter=trend_bonus_counter,
                adaptive_swing_window=adaptive_swing_window,
                swing_window_adx_threshold=swing_window_adx_threshold,
                swing_window_strong=swing_window_strong,
                swing_window_weak=swing_window_weak,
                use_order_blocks=use_order_blocks,
                use_fvg=use_fvg,
                rejection_min_wick_ratio=rejection_min_wick_ratio,
                rejection_max_body_ratio=rejection_max_body_ratio,
                use_vah_val=use_vah_val,
                value_area_pct=value_area_pct,
                vah_val_near_threshold_pct=vah_val_near_threshold_pct,
            )

            for resistance in sell_levels["resistance"]:
                filter_stats["sell"]["checked"] += 1
                r_price = resistance["price"]
                distance_pct = (r_price - current_price) / r_price * 100

                if not (0 <= distance_pct <= threshold):
                    filter_stats["sell"]["distance"] += 1
                    continue

                quality_score = resistance.get("quality_score", 0)

                # --- 4x. Funding Rate 软阈值：做空扣分（对称设计） ---
                original_leverage_for_log = leverage
                if funding_filter_enabled and funding_rate is not None:
                    if funding_rate < funding_short_block_threshold:
                        quality_score += funding_long_quality_penalty  # 使用相同惩罚值
                        filter_stats["sell"]["fr_penalty"] += 1
                        logger.debug(
                            "FR软阈值 %s %s 做空: funding=%.4f%% < %.2f%%, quality %.1f → %.1f",
                            symbol, timeframe, funding_rate, funding_short_block_threshold,
                            quality_score - funding_long_quality_penalty, quality_score,
                        )
                        if funding_long_leverage_reduction_enabled:
                            leverage = max(1, int(leverage * funding_long_leverage_reduction))
                            logger.debug(
                                "FR降杠杆 %s %s 做空: %dx → %dx",
                                symbol, timeframe, original_leverage_for_log, leverage,
                            )

                # --- 4y. 质量分级 ADX 检查 ---
                effective_adx_min = adx_min_high_quality if quality_score >= quality_threshold_for_lower_adx else adx_min
                if current_adx < effective_adx_min:
                    filter_stats["sell"]["adx"] += 1
                    logger.debug(
                        "ADX分级过滤 %s %s 做空: quality=%.1f < %.1f, "
                        "ADX=%.1f < 要求=%.1f(+DI=%.1f,−DI=%.1f)",
                        symbol, timeframe, quality_score, quality_threshold_for_lower_adx,
                        current_adx, effective_adx_min, current_pdi, current_mdi,
                    )
                    continue

                # --- 4y. +DI/-DI 方向确认 + 逆势质量门槛 ---
                if sell_is_primary:
                    # 主方向（顺势）：严格 DI 确认
                    if current_mdi <= current_pdi + di_tolerance:
                        filter_stats["sell"]["di"] += 1
                        logger.debug(
                            "+DI/-DI 过滤 %s %s 做空(主): -DI=%.1f <= +DI=%.1f + %.1f, "
                            "quality=%.1f, ADX=%.1f",
                            symbol, timeframe, current_mdi, current_pdi, di_tolerance,
                            quality_score, current_adx,
                        )
                        continue
                else:
                    # 副方向（逆势）：不检查 DI，但要求更高质量 + 更严成交量
                    if quality_score < secondary_min_quality:
                        filter_stats["sell"]["quality"] += 1
                        logger.debug(
                            "质量过滤 %s %s 做空(副): quality=%.1f < %.1f, "
                            "ADX=%.1f",
                            symbol, timeframe, quality_score, secondary_min_quality,
                            current_adx,
                        )
                        continue

                # --- 6a. 价格 vs EMA21 位置过滤 ---
                if price_vs_ema_filter:
                    ema_passed, current_ema = check_price_vs_ema(klines, is_long=False, ema_period=21)
                    if not ema_passed:
                        filter_stats["sell"]["ema"] += 1
                        logger.debug(
                            "EMA 过滤 %s %s 做空: 价格=%.2f > EMA21=%.2f, "
                            "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                            symbol, timeframe, current_price, current_ema,
                            quality_score, current_adx, current_pdi, current_mdi,
                        )
                        continue

                # --- 6b. RSI 动量过滤 ---
                rsi_passed, current_rsi = check_rsi_filter(
                    klines,
                    is_long=False,
                    is_primary=sell_is_primary,
                    period=rsi_period,
                    short_min=rsi_short_min,
                    short_max=rsi_short_max,
                    counter_short_min=rsi_counter_short_min,
                )
                if not rsi_passed:
                    filter_stats["sell"]["rsi"] += 1
                    logger.debug(
                        "RSI 过滤 %s %s 做空: RSI=%.1f, primary=%s, "
                        "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                        symbol, timeframe, current_rsi, sell_is_primary,
                        quality_score, current_adx, current_pdi, current_mdi,
                    )
                    continue

                # --- 6c. 成交量爆发确认（主/副方向不同阈值）---
                effective_vol_ratio = volume_burst_ratio if sell_is_primary else secondary_volume_ratio
                vol_passed, vol_ratio = check_volume_burst_filter(
                    klines, lookback=volume_burst_lookback, ratio=effective_vol_ratio,
                )
                if not vol_passed:
                    filter_stats["sell"]["volume"] += 1
                    logger.debug(
                        "成交量过滤 %s %s 做空(%s): 量比=%.2fx < 要求=%.2fx, "
                        "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                        symbol, timeframe, "主" if sell_is_primary else "副",
                        vol_ratio, effective_vol_ratio,
                        quality_score, current_adx, current_pdi, current_mdi,
                    )
                    continue

                # --- 6d. 拒绝蜡烛确认 ---
                if require_rejection and not resistance.get("has_rejection", False):
                    if not check_rejection_candle(
                        klines[0], "resistance",
                        min_wick_ratio=rejection_min_wick_ratio,
                        max_body_ratio=rejection_max_body_ratio,
                    ):
                        filter_stats["sell"]["rejection"] += 1
                        logger.debug(
                            "拒绝蜡烛过滤 %s %s 做空: 无拒绝蜡烛, "
                            "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                            symbol, timeframe,
                            quality_score, current_adx, current_pdi, current_mdi,
                        )
                        continue

                # --- 6e. 回踩确认 ---
                if require_pullback:
                    retest_passed = check_pullback_retest(klines, r_price, is_long=False, lookback=pullback_lookback)
                    if not retest_passed:
                        filter_stats["sell"]["pullback"] += 1
                        logger.debug(
                            "回踩过滤 %s %s 做空: 无有效回踩, "
                            "quality=%.1f, ADX=%.1f, +DI=%.1f, −DI=%.1f",
                            symbol, timeframe,
                            quality_score, current_adx, current_pdi, current_mdi,
                        )
                        continue

                # --- 6f. 计算动态止损 ---
                if use_atr_stop:
                    stop_loss = compute_dynamic_stop_loss(
                        klines, current_price, is_long=False,
                        atr_period=atr_period, atr_multiplier=effective_atr_multiplier,
                        fallback_pct=fallback_stop_pct,
                    )
                else:
                    stop_loss = r_price * (1 + fallback_stop_pct / 100)

                # --- 加仓条件检查 ---
                if existing_pos and _get_pos_attr(existing_pos, "side") == "sell":
                    # 基础质量门槛（第1次加仓）
                    if quality_score < 6.5:
                        filter_stats["sell"]["add"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做空: quality=%.1f < 6.5",
                            symbol, timeframe, quality_score,
                        )
                        continue
                    # 价格必须优于持仓价（震荡期严格要求，趋势期可放宽追仓）
                    trend_addition_allow_chase = params.get("trend_addition_allow_chase", False)
                    if trend_addition_allow_chase and not is_ranging:
                        # 趋势期允许追空：跳过价格条件
                        pass
                    elif current_price <= _get_pos_attr(existing_pos, "entry_price", current_price):
                        filter_stats["sell"]["add"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做空: 价格=%.2f 不优于持仓价=%.2f",
                            symbol, timeframe, current_price, _get_pos_attr(existing_pos, "entry_price"),
                        )
                        continue
                    # 加仓次数上限（震荡期 vs 趋势期）
                    add_count = _get_pos_attr(existing_pos, "add_count", 0)
                    max_add = max_additions_ranging if is_ranging else max_additions_trending
                    if add_count >= max_add:
                        filter_stats["sell"]["add_limit"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做空: 已达最大次数 %d/%d (%s)",
                            symbol, timeframe, add_count, max_add,
                            "震荡" if is_ranging else "趋势",
                        )
                        continue
                    # 第2次及以上加仓额外质量门槛
                    if add_count >= 1 and quality_score < add_quality_tier2:
                        filter_stats["sell"]["add_quality"] += 1
                        logger.debug(
                            "加仓过滤 %s %s 做空: 第%d次加仓质量=%.1f < %.1f",
                            symbol, timeframe, add_count + 1, quality_score, add_quality_tier2,
                        )
                        continue

                base_score = resistance.get("base_score", 0)
                t_bonus = resistance.get("trend_bonus", 0)
                r_bonus = resistance.get("recency_bonus", 0)

                signal = Signal(
                    strategy_name=self.name,
                    symbol=symbol,
                    timeframe=timeframe,
                    signal_type="sell",
                    price=current_price,
                    stop_loss=stop_loss,
                    leverage=leverage,
                    quality_score=round(quality_score, 2),
                    adx=round(current_adx, 2),
                    plus_di=round(current_pdi, 2),
                    minus_di=round(current_mdi, 2),
                    rsi=round(current_rsi, 2),
                    volume_ratio=round(vol_ratio, 2),
                    parent_trend=parent_trend,
                    direction_type="main" if sell_is_primary else "secondary",
                    is_counter_trend=not parent_aligned,
                    atr=round(current_atr, 8) if current_atr else None,
                    entry_threshold_pct=round(threshold, 4),
                    distance_pct=round(distance_pct, 4),
                    metadata={
                        "resistance_level": r_price,
                        "resistance_strength": resistance["strength"],
                        "base_score": base_score,
                        "quality_score": quality_score,
                        "trend_bonus": t_bonus,
                        "recency_bonus": r_bonus,
                        "distance_to_resistance_pct": round(distance_pct, 3),
                        "trend": trend,
                        "parent_trend": parent_trend,
                        "parent_aligned": parent_aligned,
                        "bias": bias,
                        "is_primary": sell_is_primary,
                        "adx": round(current_adx, 1),
                        "plus_di": round(current_pdi, 1),
                        "minus_di": round(current_mdi, 1),
                        "rsi": round(current_rsi, 1),
                        "volume_ratio": round(vol_ratio, 2),
                        "has_rejection": resistance.get("has_rejection", False),
                        "stop_loss_type": "atr" if use_atr_stop else "fixed",
                        "max_additions": max_additions_ranging if is_ranging else max_additions_trending,
                        "add_count": _get_pos_attr(existing_pos, "add_count", 0) if existing_pos else 0,
                        "is_ranging": is_ranging,
                        "funding_rate": round(funding_rate, 4) if funding_rate is not None else None,
                        "funding_penalty_applied": funding_filter_enabled and funding_rate is not None and funding_rate < funding_short_block_threshold,
                        "pre_fr_leverage": original_leverage_for_log,
                    },
                )
                signals.append(signal)
                filter_stats["sell"]["passed"] += 1
                fr_info = ""
                if funding_filter_enabled and funding_rate is not None and funding_rate < funding_short_block_threshold:
                    fr_info = f", FR={funding_rate:.4f}%→penalty={funding_long_quality_penalty}"
                    if funding_long_leverage_reduction_enabled and leverage < original_leverage_for_log:
                        fr_info += f", lev={original_leverage_for_log}x→{leverage}x"
                logger.info(
                    "做空信号 %s %s: 现价=%.2f, 压力=%.2f, 距离=%.3f%%, "
                    "趋势=%s, 上级=%s, 偏向=%s, %s, 止损=%.2f, "
                    "base=%.1f trend=%+.1f recency=%+.1f final=%.1f, "
                    "ADX=%.1f, +DI=%.1f, −DI=%.1f, RSI=%.1f, 量比=%.2f%s",
                    symbol, timeframe, current_price, r_price, distance_pct,
                    trend, parent_trend, bias,
                    "主方向" if sell_is_primary else "副方向(严格条件)",
                    stop_loss, base_score, t_bonus, r_bonus, quality_score,
                    current_adx, current_pdi, current_mdi,
                    current_rsi, vol_ratio, fr_info,
                )
                break

        # ========== 6.5 结构化止损更新 + 部分止盈 ==========
        if existing_pos and current_atr > 0:
            is_long = _get_pos_attr(existing_pos, "side") == "buy"
            entry = _get_pos_attr(existing_pos, "entry_price", 0)
            current_stop = _get_pos_attr(existing_pos, "stop_loss", 0)
            pos_quality = _get_pos_attr(existing_pos, "quality_score", 0.0)

            # --- 结构化止损（优先于固定 trailing） ---
            if structured_stop_enabled:
                struct_levels = identify_support_resistance(
                    klines,
                    min_touches=min_touches,
                    lookback=sr_lookback,
                    volume_surge_ratio=1.5,
                    require_rejection=require_rejection,
                    bias=bias,
                    trend_bonus_aligned=trend_bonus_aligned,
                    trend_bonus_counter=trend_bonus_counter,
                    adaptive_swing_window=adaptive_swing_window,
                    swing_window_adx_threshold=swing_window_adx_threshold,
                    swing_window_strong=swing_window_strong,
                    swing_window_weak=swing_window_weak,
                    use_order_blocks=use_order_blocks,
                    use_fvg=use_fvg,
                    rejection_min_wick_ratio=rejection_min_wick_ratio,
                    rejection_max_body_ratio=rejection_max_body_ratio,
                    use_vah_val=use_vah_val,
                    value_area_pct=value_area_pct,
                    vah_val_near_threshold_pct=vah_val_near_threshold_pct,
                )
                all_levels = struct_levels["support"] + struct_levels["resistance"]

                new_stop = compute_structured_stop(
                    entry_price=entry,
                    current_price=current_price,
                    current_atr=current_atr,
                    current_stop=current_stop,
                    is_long=is_long,
                    levels=all_levels,
                    min_r=structured_stop_min_r,
                    buffer_pct=structured_stop_buffer_pct,
                    quality_score=pos_quality,
                    quality_buffer_pct=structured_stop_quality_buffer_pct,
                )
                if new_stop is not None:
                    logger.info(
                        "结构化止损更新 %s %s: %s 入场=%.2f 当前=%.2f 旧止损=%.2f 新止损=%.2f quality=%.1f",
                        symbol, timeframe, "多" if is_long else "空",
                        entry, current_price, current_stop, new_stop, pos_quality,
                    )
                    signals.append(Signal(
                        strategy_name=self.name,
                        symbol=symbol,
                        timeframe=timeframe,
                        signal_type="buy" if is_long else "sell",
                        price=current_price,
                        stop_loss=new_stop,
                        leverage=leverage,
                        action="update_stop",
                        metadata={"structured_stop": True, "old_stop": current_stop},
                    ))

            # --- 固定 trailing stop（向后兼容，结构化止损关闭时生效） ---
            elif trailing_stop_enabled:
                new_stop = compute_trailing_stop(
                    entry_price=entry,
                    current_price=current_price,
                    current_atr=current_atr,
                    is_long=is_long,
                    breakeven_threshold_r=trailing_breakeven_threshold_r,
                    breakeven_target_r=trailing_breakeven_target_r,
                    trail_start_r=trailing_start_r,
                    trail_distance_r=trailing_distance_r,
                    wide_trail_start_r=trailing_wide_start_r,
                    wide_trail_distance_r=trailing_wide_distance_r,
                )
                if new_stop is not None:
                    should_update = (is_long and new_stop > current_stop) or (not is_long and new_stop < current_stop)
                    if should_update:
                        logger.info(
                            "跟踪止损更新 %s %s: %s 入场=%.2f 当前=%.2f 旧止损=%.2f 新止损=%.2f ATR=%.2f",
                            symbol, timeframe, "多" if is_long else "空",
                            entry, current_price, current_stop, new_stop, current_atr,
                        )
                        signals.append(Signal(
                            strategy_name=self.name,
                            symbol=symbol,
                            timeframe=timeframe,
                            signal_type="buy" if is_long else "sell",
                            price=current_price,
                            stop_loss=new_stop,
                            leverage=leverage,
                            action="update_stop",
                            metadata={"trailing_stop": True, "old_stop": current_stop},
                        ))

            # --- 部分止盈 ---
            if partial_tp_enabled:
                profit = current_price - entry if is_long else entry - current_price
                profit_r = profit / current_atr
                triggered = set(_get_pos_attr(existing_pos, "partial_tp_triggered", []))

                for tp_level in partial_tp_levels:
                    r_thresh = tp_level["r_threshold"]
                    close_pct = tp_level["close_pct"]
                    level_id = f"{r_thresh}R"

                    if profit_r >= r_thresh and level_id not in triggered:
                        logger.info(
                            "部分止盈 %s %s: %s 盈利=%.2fR (>= %.1fR) 平仓 %.0f%%",
                            symbol, timeframe, "多" if is_long else "空",
                            profit_r, r_thresh, close_pct * 100,
                        )
                        signals.append(Signal(
                            strategy_name=self.name,
                            symbol=symbol,
                            timeframe=timeframe,
                            signal_type="buy" if is_long else "sell",
                            price=current_price,
                            stop_loss=current_stop,
                            leverage=leverage,
                            action="partial_close",
                            close_ratio=close_pct,
                            metadata={"partial_tp_level": level_id, "profit_r": round(profit_r, 2)},
                        ))

        # ========== 7. 过滤统计汇总 + 累计 ==========
        for direction, stats in filter_stats.items():
            if any(v > 0 for v in stats.values()):
                logger.info(
                    "[FILTER] %s %s %s: checked=%d distance=%d adx=%d di=%d "
                    "quality=%d ema=%d rsi=%d volume=%d rejection=%d pullback=%d "
                    "fr_hard=%d fr_penalty=%d add=%d add_limit=%d add_quality=%d passed=%d",
                    symbol, timeframe, direction,
                    stats["checked"], stats["distance"], stats["adx"], stats["di"],
                    stats["quality"], stats["ema"], stats["rsi"], stats["volume"],
                    stats["rejection"], stats["pullback"],
                    stats["fr_hard_block"], stats["fr_penalty"],
                    stats["add"], stats["add_limit"], stats["add_quality"], stats["passed"],
                )
                # 累加到实例级累计统计
                cum = self._filter_summary[direction]
                for key in stats:
                    cum[key] += stats[key]

        return signals

    def get_filter_summary(self) -> dict:
        """返回整个回测/实盘期间的累计过滤统计。"""
        return getattr(self, "_filter_summary", {
            "buy": {"checked": 0, "distance": 0, "adx": 0, "di": 0, "quality": 0, "ema": 0, "rsi": 0, "volume": 0, "rejection": 0, "pullback": 0, "fr_hard_block": 0, "fr_penalty": 0, "add": 0, "add_limit": 0, "add_quality": 0, "passed": 0},
            "sell": {"checked": 0, "distance": 0, "adx": 0, "di": 0, "quality": 0, "ema": 0, "rsi": 0, "volume": 0, "rejection": 0, "pullback": 0, "fr_hard_block": 0, "fr_penalty": 0, "add": 0, "add_limit": 0, "add_quality": 0, "passed": 0},
        })
