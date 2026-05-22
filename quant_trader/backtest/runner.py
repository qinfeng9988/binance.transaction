"""回测引擎，基于 backtesting.py。

支持：
- 从 PostgreSQL 加载历史 K 线
- 策略适配 backtesting.py 框架
- 参数优化（网格搜索）
- 绩效报告输出
"""

import logging

logger = logging.getLogger(__name__)


def run_backtest(symbol: str, timeframe: str, data_store, strategy_class,
                 cash: float = 100000, commission: float = 0.001,
                 params: dict | None = None, optimize: bool = False):
    """执行回测。

    参数:
        symbol: 交易对
        timeframe: K 线周期
        data_store: DataStore 实例
        strategy_class: 适配了 backtesting.py 的策略类
        cash: 初始资金
        commission: 手续费率
        params: 策略参数（None 则用默认值）
        optimize: 是否进行参数优化

    返回:
        backtesting.Stats 或优化结果
    """
    try:
        from backtesting import Backtest, Strategy
    except ImportError:
        logger.error("backtesting.py 未安装，请先执行: pip install backtesting")
        raise

    # 从数据库加载 K 线数据
    klines = data_store.get_klines(symbol, timeframe, limit=5000)
    if len(klines) < 100:
        logger.warning("K 线数据不足 (%d 条)，建议至少 100 条", len(klines))
        return None

    # 反转：数据库返回最新在前，backtesting.py 需要最旧在前
    klines.reverse()

    # 转换为 backtesting.py 需要的 DataFrame 格式
    import pandas as pd
    df = pd.DataFrame(klines)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    df = df.rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    })

    # 确保数值类型
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = df[col].astype(float)

    # 创建回测实例
    bt = Backtest(df, strategy_class, cash=cash, commission=commission,
                  exclusive_orders=True, trade_on_close=False)

    if optimize:
        # 参数优化
        optimize_params = {}
        if params:
            for key, value in params.items():
                if isinstance(value, (int, float)):
                    # 网格搜索范围：0.5x ~ 2x
                    optimize_params[key] = range(max(1, int(value * 0.5)), int(value * 2) + 1)

        if optimize_params:
            logger.info("开始参数优化: %s", optimize_params)
            stats = bt.optimize(**optimize_params, maximize="Sharpe Ratio")
        else:
            logger.info("无可优化参数，执行基础回测")
            stats = bt.run()
    else:
        # 使用指定参数运行
        if params:
            stats = bt.run(**params)
        else:
            stats = bt.run()

    # 打印绩效报告
    _print_report(stats, symbol, timeframe)

    return stats


def _print_report(stats, symbol: str, timeframe: str):
    """打印回测绩效报告。"""
    logger.info("=" * 60)
    logger.info("回测报告: %s %s", symbol, timeframe)
    logger.info("=" * 60)

    # 打印关键指标
    metrics = [
        ("初始资金", stats.get("Start", "N/A")),
        ("期末资金", stats.get("End", "N/A")),
        ("收益率", f"{stats.get('Return [%]', 0):.2f}%"),
        ("最大回撤", f"{stats.get('Max. Drawdown [%]', 0):.2f}%"),
        ("夏普比率", f"{stats.get('Sharpe Ratio', 0):.4f}"),
        ("总交易次数", stats.get("# Trades", 0)),
        ("胜率", f"{stats.get('Win Rate [%]', 0):.2f}%"),
        ("盈亏比", f"{stats.get('Profit Factor', 0):.2f}"),
    ]

    for name, value in metrics:
        logger.info("  %-12s: %s", name, value)

    logger.info("=" * 60)
