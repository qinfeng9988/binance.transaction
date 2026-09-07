"""临时脚本：基于真实 K 线 + 策略函数，计算 BTC/ETH 多单的止盈/止损位。"""
import sys, os, yaml
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from quant_trader.core.config_loader import ConfigLoader
from quant_trader.technical.indicators import atr
from quant_trader.technical.support_resistance import identify_support_resistance
from quant_trader.strategies.s_r_levels import (
    detect_trend_with_parent, compute_structured_stop, compute_trailing_stop,
)

# 数据库连接从 exchanges.yaml 读取（勿硬编码密码）
_ex = ConfigLoader().load_static()
DB = dict(host=_ex.db_host, port=_ex.db_port, dbname=_ex.db_name,
          user=_ex.db_user, password=_ex.db_password)

# (label, strategy_name in yaml, symbol, entry_price, current_stop, tf, parent_tf)
POSITIONS = [
    ("BTC/USDT", "support_resistance_btc_1h", "BTC/USDT", 63398.90, 62630.59620940, "1h", "4h"),
    ("ETH/USDT", "support_resistance_eth",    "ETH/USDT", 1891.63, 1874.97455140, "1h", "4h"),
]

def fetch_klines(symbol, tf, limit=300):
    sql = "SELECT timestamp, open, high, low, close, volume FROM klines WHERE symbol=%s AND timeframe=%s ORDER BY timestamp DESC LIMIT %s;"
    conn = psycopg2.connect(**DB)
    cur = conn.cursor()
    cur.execute(sql, (symbol, tf, limit))
    rows = cur.fetchall()
    cur.close(); conn.close()
    # newest-first list of dict
    return [{"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])} for r in rows]

def load_params(strategy_name):
    with open("quant_trader/config/strategies.yaml") as f:
        cfg = yaml.safe_load(f)
    for s in cfg["strategies"]:
        if s["name"] == strategy_name:
            return s["params"]
    raise KeyError(strategy_name)

def chrono(klines):
    return list(reversed(klines))  # old→new

def fmt_levels(levels, above, current_price, n=5):
    """返回距离现价 above(True=上方/压力) 或 below(False=下方/支撑) 的最近 N 个位。"""
    out = []
    for lvl in levels:
        p = lvl["price"]
        if above and p > current_price:
            out.append(lvl)
        elif not above and p < current_price:
            out.append(lvl)
    out.sort(key=lambda x: x["price"], reverse=not above)  # 距现价最近优先
    return out[:n]

for label, strat, symbol, entry, cur_stop, tf, ptf in POSITIONS:
    print("=" * 80)
    print(f"  {label}  开仓策略={strat}  周期={tf}  parent={ptf}")
    print("=" * 80)
    p = load_params(strat)

    klines = fetch_klines(symbol, tf, 400)
    parent = fetch_klines(symbol, ptf, 400)
    if len(klines) < 60:
        print("  K 线不足，跳过"); continue
    current_price = klines[0]["close"]

    # bias（identify_support_resistance 需要）
    ti = detect_trend_with_parent(
        klines, parent,
        require_parent_aligned=p.get("require_parent_ema_aligned", True),
        threshold_pct=p.get("ema_gap_threshold_pct", 1.0),
        fast_period=p.get("ema_fast_period", 21),
        slow_period=p.get("ema_slow_period", 50),
        trend_lookback=p.get("trend_lookback", None),
        parent_lookback=p.get("parent_lookback", None),
    )
    bias = ti["bias"]
    print(f"趋势: trend={ti['trend']} parent={ti['parent_trend']} bias={bias} parent_aligned={ti['parent_aligned']}")

    atr_period = p.get("atr_period", 14)
    atr_vals = atr(chrono(klines), period=atr_period)
    current_atr = float(atr_vals[-1]) if len(atr_vals) else 0.0
    if current_atr != current_atr:
        current_atr = 0.0

    profit = current_price - entry  # long
    profit_r = profit / current_atr if current_atr > 0 else 0.0
    print(f"现价={current_price:.2f}  入场={entry:.2f}  ATR({atr_period})={current_atr:.2f}  "
          f"浮盈={profit:.2f} ({profit/entry*100:.2f}%)  = {profit_r:.2f}R")
    print(f"当前固定止损={cur_stop:.2f}  (在入场{'下方' if cur_stop < entry else '上方'}，"
          f"距现价 {(cur_stop-current_price)/current_price*100:.2f}%)")

    # S/R 检测（与策略 6.5 节 structured_stop 调用一致）
    levels = identify_support_resistance(
        klines,
        min_touches=p.get("min_touches", 3),
        lookback=p.get("sr_lookback", p.get("lookback", 100)),
        volume_surge_ratio=1.5,
        require_rejection=p.get("require_rejection_candle", False),
        bias=bias,
        trend_bonus_aligned=p.get("trend_bonus_aligned", 1.3),
        trend_bonus_counter=p.get("trend_bonus_counter", -0.5),
        adaptive_swing_window=p.get("adaptive_swing_window", False),
        swing_window_adx_threshold=p.get("swing_window_adx_threshold", 25.0),
        swing_window_strong=p.get("swing_window_strong", 3),
        swing_window_weak=p.get("swing_window_weak", 2),
        use_order_blocks=p.get("use_order_blocks", False),
        use_fvg=p.get("use_fvg", False),
        rejection_min_wick_ratio=p.get("rejection_min_wick_ratio", 2.0),
        rejection_max_body_ratio=p.get("rejection_max_body_ratio", 0.5),
        use_vah_val=p.get("use_vah_val", False),
        value_area_pct=p.get("value_area_pct", 0.70),
        vah_val_near_threshold_pct=p.get("vah_val_near_threshold_pct", 0.005),
        poc_near_threshold_pct=p.get("poc_near_threshold_pct", 0.005),
        poc_far_threshold_pct=p.get("poc_far_threshold_pct", 0.007),
        vah_val_near_bonus=p.get("vah_val_near_bonus", 0.5),
        vah_val_inner_bonus=p.get("vah_val_inner_bonus", 0.3),
    )
    all_levels = levels["support"] + levels["resistance"]

    sup_near = fmt_levels(levels["support"], above=False, current_price=current_price, n=4)
    res_near = fmt_levels(levels["resistance"], above=True, current_price=current_price, n=4)

    print(f"\n最近支撑(止损参考/下方):")
    for s in sup_near:
        d = (current_price - s["price"]) / s["price"] * 100
        print(f"  价={s['price']:.2f}  距现价 -{d:.2f}%  quality={s.get('quality_score',0):.2f}")
    print(f"最近压力(止盈参考/上方):")
    for r in res_near:
        d = (r["price"] - current_price) / r["price"] * 100
        print(f"  价={r['price']:.2f}  距现价 +{d:.2f}%  quality={r.get('quality_score',0):.2f}")

    # ---- 结构化止损（若启用）----
    print(f"\n--- 若开启 structured_stop (min_r={p.get('structured_stop_min_r',1.5)}, "
          f"buffer={p.get('structured_stop_buffer_pct',0.3)}%) ---")
    new_stop = compute_structured_stop(
        entry_price=entry, current_price=current_price, current_atr=current_atr,
        current_stop=cur_stop, is_long=True, levels=all_levels,
        min_r=p.get("structured_stop_min_r", 1.5),
        buffer_pct=p.get("structured_stop_buffer_pct", 0.3),
        quality_score=0.0,  # positions 表未存 quality_score，按默认
        quality_buffer_pct=p.get("structured_stop_quality_buffer_pct", 0.2),
    )
    if new_stop is None:
        print(f"  不满足条件(需盈利>= {p.get('structured_stop_min_r',1.5)}R 且下方有支撑)。"
              f"当前 {profit_r:.2f}R")
    else:
        print(f"  新止损将移至 {new_stop:.2f}  (距现价 {(new_stop-current_price)/current_price*100:.2f}%, "
              f"锁定盈利 {new_stop-entry:.2f} / {profit:.2f})")

    # ---- 跟踪止损（若启用）----
    print(f"\n--- 若开启 trailing_stop (四档) ---")
    t_stop = compute_trailing_stop(
        entry_price=entry, current_price=current_price, current_atr=current_atr, is_long=True,
    )
    tiers = [
        ("未达0.8R(不更新)", profit_r < 0.8),
        ("保本位(0.8~1.8R)", 0.8 <= profit_r < 1.8),
        ("跟踪1.8R(1.8~3.5R)", 1.8 <= profit_r < 3.5),
        ("宽跟踪3.5R+", profit_r >= 3.5),
    ]
    tier_name = next(n for n, cond in tiers if cond)
    if t_stop is None:
        print(f"  当前 {profit_r:.2f}R → 档位[{tier_name}]，止损暂不更新(仍={cur_stop:.2f})")
    else:
        moved = t_stop > cur_stop
        print(f"  当前 {profit_r:.2f}R → 档位[{tier_name}]  计算止损={t_stop:.2f}  "
              f"{'会更新(优于当前)' if moved else '不更新(不优于当前 '+str(round(cur_stop,2))+')'}")

    # ---- 部分止盈（若启用）----
    ptp = p.get("partial_tp_enabled", False)
    print(f"\n--- 部分止盈 (当前配置 partial_tp_enabled={ptp}) ---")
    for lvl in p.get("partial_tp_levels", [
            {"r_threshold": 2.0, "close_pct": 0.4},
            {"r_threshold": 3.5, "close_pct": 0.3}]):
        r = lvl["r_threshold"]; c = lvl["close_pct"]
        hit = profit_r >= r
        print(f"  {r}R 平{c*100:.0f}%  → {'已应触发' if hit else f'未到(差 {r-profit_r:.2f}R)'}")
    print()
