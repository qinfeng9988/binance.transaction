"""BacktestConfig 加载单元测试。

验证：
- ConfigLoader 能加载 backtest.yaml
- 缺失新字段时使用默认值
"""

import pytest

from quant_trader.core.config_loader import ConfigLoader, BacktestConfig


@pytest.fixture
def tmp_config_dir(tmp_path):
    """创建包含 backtest.yaml 的临时配置目录。"""
    (tmp_path / "exchanges.yaml").write_text(
        "binance: {api_key: '', api_secret: '', sandbox: true, default_type: swap}\n"
        "database: {host: localhost, port: 5432, dbname: db_test, user: postgres, password: ''}\n",
        encoding="utf-8",
    )
    (tmp_path / "strategies.yaml").write_text("mode: paper\nstrategies: []\n", encoding="utf-8")
    (tmp_path / "risk.yaml").write_text(
        "leverage_limits: {major_coins: 5, alt_coins: 3}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_backtest_config_loads_from_yaml(tmp_config_dir):
    """当 backtest.yaml 存在时，所有字段正确加载。"""
    (tmp_config_dir / "backtest.yaml").write_text(
        "backtest_database:\n"
        "  host: localhost\n"
        "  port: 5433\n"
        "  dbname: db_backtest\n"
        "  user: bt_user\n"
        "  password: secret\n"
        "backtest:\n"
        "  initial_balance: 50000.0\n"
        "  funding_rate_cache_dir: data/funding\n"
        "  default_type: spot\n"
        "data_fetch:\n"
        "  current_tolerance: 0.002\n"
        "  parent_tolerance: 0.006\n"
        "  max_retries: 3\n",
        encoding="utf-8",
    )

    loader = ConfigLoader(config_dir=tmp_config_dir)
    bt = loader.load_backtest()

    assert isinstance(bt, BacktestConfig)
    assert bt.db_host == "localhost"
    assert bt.db_port == 5433
    assert bt.db_name == "db_backtest"
    assert bt.db_user == "bt_user"
    assert bt.db_password == "secret"
    assert bt.initial_balance == 50000.0
    assert bt.funding_rate_cache_dir == "data/funding"
    assert bt.default_type == "spot"
    assert bt.current_tolerance == 0.002
    assert bt.parent_tolerance == 0.006
    assert bt.max_retries == 3


def test_backtest_config_uses_defaults_when_yaml_missing(tmp_config_dir):
    """当 backtest.yaml 不存在时，BacktestConfig 使用默认值。"""
    loader = ConfigLoader(config_dir=tmp_config_dir)
    bt = loader.load_backtest()

    assert bt.db_name == "db_backtest"
    assert bt.db_port == 5432
    assert bt.initial_balance == 100000.0
    assert bt.default_type == "swap"
    assert bt.current_tolerance == 0.001
    assert bt.parent_tolerance == 0.005
    assert bt.max_retries == 5
