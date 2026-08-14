"""backtest_runner 纯函数单元测试。

不依赖真实 DB 的部分（run_id 生成）。
端到端集成测试见 test_backtest_runner_integration.py。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from backtest_runner import generate_run_id


def test_run_id_format_basic():
    """run_id 格式: bt_{YYYYMMDD_HHMMSS}_{symbol}_{tf}_{hash8}。"""
    run_id = generate_run_id("BTC/USDT", "5m", {"lookback": 100})

    assert run_id.startswith("bt_")
    assert "BTC-USDT" in run_id
    assert "_5m_" in run_id


def test_run_id_hash_is_8_hex_chars():
    """hash 部分是 8 位十六进制。"""
    run_id = generate_run_id("BTC/USDT", "5m", {"lookback": 100})
    hash_part = run_id.split("_")[-1]
    assert len(hash_part) == 8
    int(hash_part, 16)  # 验证是合法十六进制


def test_run_id_deterministic_for_same_params():
    """相同参数生成的 hash 部分一致（不含时间戳）。"""
    params = {"lookback": 100, "min_touches": 5.3}
    run1 = generate_run_id("BTC/USDT", "5m", params)
    run2 = generate_run_id("BTC/USDT", "5m", params)

    assert run1.split("_")[-1] == run2.split("_")[-1]


def test_run_id_differs_for_different_params():
    """不同参数生成不同 hash。"""
    run1 = generate_run_id("BTC/USDT", "5m", {"lookback": 100})
    run2 = generate_run_id("BTC/USDT", "5m", {"lookback": 200})

    assert run1.split("_")[-1] != run2.split("_")[-1]


def test_run_id_handles_colon_symbols():
    """symbol 含冒号（如 BTC/USDT:USDT）时正常生成。"""
    run_id = generate_run_id("BTC/USDT:USDT", "1h", {"x": 1})

    assert "BTC-USDT-USDT" in run_id
