#!/usr/bin/env python3
"""SOL 参数优化脚本 — 直接复用 backtest.py 引擎。

通过临时修改 strategies.yaml 中的 SOL 参数，调用 backtest.py 获取结果。
"""

import subprocess
import sys
import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).parent.parent / "quant_trader/config/strategies.yaml"


def run_backtest(symbol: str, timeframe: str, days: int) -> dict:
    """调用 backtest.py 并解析结果。"""
    result = subprocess.run(
        [sys.executable, "scripts/backtest.py", "--symbol", symbol, "--timeframe", timeframe, "--days", str(days)],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent,
    )
    output = result.stdout + result.stderr

    lines = output.splitlines()
    data = {}
    for line in lines:
        if "总收益率:" in line:
            data["return"] = float(line.split()[-1].replace("%", ""))
        elif "最大回撤:" in line:
            data["max_dd"] = float(line.split()[-1].replace("%", ""))
        elif "总交易次数:" in line:
            data["trades"] = int(line.split()[-1])
        elif "胜率:" in line:
            data["win_rate"] = float(line.split()[-1].replace("%", ""))
        elif "profit factor:" in line:
            data["pf"] = float(line.split()[-1])
    return data


def set_sol_param(key: str, value):
    """修改 strategies.yaml 中 SOL 的指定参数。"""
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    for s in cfg["strategies"]:
        if s["name"] == "support_resistance_sol":
            s["params"][key] = value
            break
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def get_sol_param(key: str):
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)
    for s in cfg["strategies"]:
        if s["name"] == "support_resistance_sol":
            return s["params"][key]


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    args = parser.parse_args()

    # 保存原始参数
    original = {}
    for key in ["entry_threshold_pct", "min_touches", "atr_multiplier", "adx_min", "leverage", "secondary_min_quality"]:
        original[key] = get_sol_param(key)

    results = []

    # 网格搜索：缩小范围，聚焦关键参数
    for entry in [0.5, 0.7, 0.9, 1.2]:
        for touches in [3, 4, 5]:
            for atr in [3.0, 4.0, 5.0]:
                for adx in [15.0, 17.0, 19.0]:
                    set_sol_param("entry_threshold_pct", entry)
                    set_sol_param("min_touches", touches)
                    set_sol_param("atr_multiplier", atr)
                    set_sol_param("adx_min", adx)

                    print(f"\n测试: entry={entry}, touches={touches}, atr={atr}, adx={adx}")
                    r = run_backtest("SOL/USDT", "5m", args.days)
                    print(f"  收益={r.get('return', 0):.2f}%, 回撤={r.get('max_dd', 0):.2f}%, "
                          f"交易={r.get('trades', 0)}, 胜率={r.get('win_rate', 0):.1f}%, PF={r.get('pf', 0):.2f}")
                    results.append({
                        "entry": entry, "touches": touches, "atr": atr, "adx": adx,
                        **r,
                    })

    # 恢复原始参数
    for key, val in original.items():
        set_sol_param(key, val)

    # 排序：收益 - 0.5 * 回撤，至少 5 笔交易
    valid = [r for r in results if r.get("trades", 0) >= 5]
    valid.sort(key=lambda x: (x.get("return", 0) - x.get("max_dd", 0) * 0.5), reverse=True)

    print("\n" + "=" * 90)
    print("TOP 15 参数组合（按 收益 - 0.5×回撤 排序，至少 5 笔交易）")
    print("=" * 90)
    print(f"{'#':<3} {'entry':<6} {'touch':<5} {'atr':<5} {'adx':<5} {'收益%':<8} {'回撤%':<8} {'交易':<5} {'胜率%':<7} {'PF':<6}")
    print("-" * 90)
    for i, r in enumerate(valid[:15]):
        print(f"{i+1:<3} {r['entry']:<6} {r['touches']:<5} {r['atr']:<5} {r['adx']:<5} "
              f"{r.get('return', 0):<8.2f} {r.get('max_dd', 0):<8.2f} {r.get('trades', 0):<5} "
              f"{r.get('win_rate', 0):<7.1f} {r.get('pf', 0):<6.2f}")


if __name__ == "__main__":
    main()
