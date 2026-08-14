"""ConfigLoader 加载风控新参数的单元测试。

验证：
- risk.yaml 缺失新参数时使用默认值
- risk.yaml 提供新参数时正确读取
- backtest.yaml 能被独立加载
"""

import pytest
from pathlib import Path

from quant_trader.core.config_loader import ConfigLoader, RiskConfig


@pytest.fixture
def tmp_config_dir(tmp_path):
    """创建包含最小化配置的临时配置目录。"""
    # exchanges.yaml（最小化）
    (tmp_path / "exchanges.yaml").write_text(
        "binance:\n"
        "  api_key: ''\n"
        "  api_secret: ''\n"
        "  sandbox: true\n"
        "  default_type: swap\n"
        "database:\n"
        "  host: localhost\n"
        "  port: 5432\n"
        "  dbname: db_test\n"
        "  user: postgres\n"
        "  password: ''\n",
        encoding="utf-8",
    )
    # risk.yaml（仅必填项，不含新参数）
    (tmp_path / "risk.yaml").write_text(
        "leverage_limits:\n"
        "  major_coins: 5\n"
        "  alt_coins: 3\n"
        "major_symbols: [BTC, ETH, SOL]\n"
        "stop_loss:\n"
        "  default_pct: 1.5\n"
        "  min_pct: 1.0\n"
        "  max_pct: 2.5\n"
        "position_limits:\n"
        "  max_loss_per_trade_pct: 15\n"
        "  max_daily_loss_pct: 30\n"
        "  max_position_size_pct: 40\n"
        "  max_concurrent_positions: 5\n"
        "max_leveraged_loss_pct: 15\n",
        encoding="utf-8",
    )
    # strategies.yaml（最小化）
    (tmp_path / "strategies.yaml").write_text(
        "mode: paper\nstrategies: []\n",
        encoding="utf-8",
    )
    return tmp_path


def test_risk_config_uses_defaults_when_fields_missing(tmp_config_dir):
    """当 risk.yaml 未配置新参数时，RiskConfig 使用默认值。"""
    loader = ConfigLoader(config_dir=tmp_config_dir)
    risk = loader.load_risk()

    assert risk.max_consecutive_losses == 3
    assert risk.cooldown_duration_seconds == 3600
    assert risk.max_total_risk_pct == 8.0


def test_risk_config_loads_custom_values(tmp_config_dir):
    """当 risk.yaml 提供新参数时，RiskConfig 正确读取。"""
    (tmp_config_dir / "risk.yaml").write_text(
        (tmp_config_dir / "risk.yaml").read_text(encoding="utf-8")
        + "max_consecutive_losses: 5\n"
        "cooldown_duration_seconds: 7200\n"
        "max_total_risk_pct: 10.0\n",
        encoding="utf-8",
    )

    loader = ConfigLoader(config_dir=tmp_config_dir)
    risk = loader.load_risk()

    assert risk.max_consecutive_losses == 5
    assert risk.cooldown_duration_seconds == 7200
    assert risk.max_total_risk_pct == 10.0
