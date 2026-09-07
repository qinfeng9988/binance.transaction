"""position_value_ratio_for 单元测试（仓位比例可配置化）。

背景：开仓仓位比例原先硬编码为 0.1（余额的 10%），无法按策略调整风险敞口。
现在改为从策略参数 position_value_ratio 读取，回测与实盘 paper 共用同一
解析逻辑，确保行为一致。
"""

from quant_trader.core.config_loader import StrategyConfig
from quant_trader.main import position_value_ratio_for


def make_strategy(name="s", params=None):
    return StrategyConfig(
        name=name, enabled=True,
        symbols=["BTC/USDT"], timeframes=["5m"],
        params=params or {},
    )


def test_reads_ratio_from_strategy_params():
    """策略参数声明 position_value_ratio 时按配置返回。"""
    strategies = [make_strategy(name="a", params={"position_value_ratio": 0.2})]

    assert position_value_ratio_for("a", strategies) == 0.2


def test_defaults_to_0_1_when_param_missing():
    """未声明参数时回退默认 0.1（与历史行为一致）。"""
    strategies = [make_strategy(name="a", params={"lookback": 100})]

    assert position_value_ratio_for("a", strategies) == 0.1


def test_unknown_strategy_defaults_to_0_1():
    """找不到对应策略（信号来源不明）时用保守默认 0.1。"""
    strategies = [make_strategy(name="a")]

    assert position_value_ratio_for("unknown", strategies) == 0.1


def test_int_ratio_coerced_to_float():
    """YAML 里写 1（int）也能正常当浮点使用。"""
    strategies = [make_strategy(name="a", params={"position_value_ratio": 1})]

    ratio = position_value_ratio_for("a", strategies)
    assert ratio == 1.0
    assert isinstance(ratio, float)
