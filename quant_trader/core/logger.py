"""日志系统配置。

提供：
- 控制台输出（INFO 及以上级别）
- 文件全量记录（DEBUG 及以上）
- 信号专用日志（signals.log）
- 交易专用日志（trades.log）
"""

import logging
import sys
from pathlib import Path


def setup_logging(log_dir: str | Path | None = None):
    """配置全局日志，同时输出到终端和文件。"""
    log_dir = Path(log_dir) if log_dir else Path(__file__).parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 日志格式: 时间 [级别] 模块名 消息
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-7s] %(name)-20s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 根日志器
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 控制台：INFO 及以上
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # 文件：全量日志
    file_handler = logging.FileHandler(log_dir / "quant_trader.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # 信号日志：独立文件，仅记录交易信号
    signal_fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    signal_handler = logging.FileHandler(log_dir / "signals.log", encoding="utf-8")
    signal_handler.setLevel(logging.INFO)
    signal_handler.setFormatter(signal_fmt)
    signal_logger = logging.getLogger("signals")
    signal_logger.addHandler(signal_handler)

    # 交易日志：独立文件，记录执行结果
    trade_handler = logging.FileHandler(log_dir / "trades.log", encoding="utf-8")
    trade_handler.setLevel(logging.INFO)
    trade_handler.setFormatter(fmt)
    trade_logger = logging.getLogger("trades")
    trade_logger.addHandler(trade_handler)

    logger = logging.getLogger(__name__)
    logger.info("日志系统已初始化: %s", log_dir)
