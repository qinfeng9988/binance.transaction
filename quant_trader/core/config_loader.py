"""配置文件加载器，支持热重载。

静态配置（exchanges.yaml）启动时加载一次；
动态配置（strategies.yaml, risk.yaml）通过 watchdog 监听文件变更，自动重新加载。
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)


# ============================================================
# 配置数据模型
# ============================================================


@dataclass
class ExchangeConfig:
    """交易所和数据库的静态配置。修改后需重启程序。"""
    api_key: str = ""
    api_secret: str = ""
    sandbox: bool = True                # True = 测试网/模拟环境
    default_type: str = "swap"          # "spot"(现货) 或 "swap"(合约)
    proxy: str = ""                     # HTTP 代理地址
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "db_quant_trader"
    db_user: str = ""
    db_password: str = ""
    status_host: str = "127.0.0.1"
    status_port: int = 58080
    kline_buffer_seconds: int = 1


@dataclass
class StrategyConfig:
    """单个策略的配置。"""
    name: str = ""
    enabled: bool = True
    symbols: list[str] = field(default_factory=list)    # 交易对列表，如 ["BTC/USDT"]
    timeframes: list[str] = field(default_factory=list) # K 线周期，如 ["5m", "1h"]
    params: dict = field(default_factory=dict)          # 策略参数


@dataclass
class StrategyGlobalConfig:
    """策略全局设置。"""
    mode: str = "paper"                 # "paper" / "live_confirm" / "live_auto"
    strategies: list[StrategyConfig] = field(default_factory=list)


@dataclass
class RiskConfig:
    """风控配置。"""
    leverage_limits: dict = field(default_factory=lambda: {"major_coins": 5, "alt_coins": 3})
    major_symbols: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL"])
    stop_loss: dict = field(default_factory=lambda: {"default_pct": 1.5, "min_pct": 1.0, "max_pct": 2.5})
    position_limits: dict = field(default_factory=lambda: {
        "max_loss_per_trade_pct": 15,       # 单笔最大亏损（占保证金 %）
        "max_daily_loss_pct": 30,           # 日累计亏损上限
        "max_position_size_pct": 40,        # 单币种最大仓位占比
        "max_concurrent_positions": 5,      # 最大同时持仓数
    })
    max_leveraged_loss_pct: float = 15.0    # 止损距离×杠杆的上限
    # 连续亏损风控参数（回测与实盘共享）
    max_consecutive_losses: int = 3         # 触发冷静期/加仓降级的连续亏损次数
    cooldown_duration_seconds: int = 3600   # 冷静期时长（秒），期间拒绝新开仓
    max_total_risk_pct: float = 8.0         # 所有 open positions 的潜在最大亏损占余额上限


@dataclass
class BacktestConfig:
    """回测专用配置。修改后需重启回测脚本。"""
    # 回测 DB 连接
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "db_backtest"
    db_user: str = ""
    db_password: str = ""
    # 回测参数
    initial_balance: float = 100000.0
    funding_rate_cache_dir: str = "quant_trader/data/funding_rates"
    default_type: str = "swap"
    # 数据补全容差
    current_tolerance: float = 0.001
    parent_tolerance: float = 0.005
    max_retries: int = 5


class ConfigLoader:
    """配置加载器，管理静态/动态配置和热重载。"""

    def __init__(self, config_dir: str | Path | None = None):
        self.config_dir = Path(config_dir) if config_dir else Path(__file__).parent.parent / "config"
        self._exchange_cfg: ExchangeConfig | None = None
        self._strategy_cfg: StrategyGlobalConfig | None = None
        self._risk_cfg: RiskConfig | None = None
        self._backtest_cfg: BacktestConfig | None = None
        self._watcher: Observer | None = None
        self._callbacks: list[Callable] = []

    def load_static(self) -> ExchangeConfig:
        """加载 exchanges.yaml（静态配置，修改后需重启）。"""
        path = self.config_dir / "exchanges.yaml"
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        binance = raw.get("binance", {})
        db = raw.get("database", {})
        status = raw.get("status", {})
        kline = raw.get("kline", {})
        self._exchange_cfg = ExchangeConfig(
            api_key=binance.get("api_key", ""),
            api_secret=binance.get("api_secret", ""),
            sandbox=binance.get("sandbox", True),
            default_type=binance.get("default_type", "swap"),
            proxy=binance.get("proxy", ""),
            db_host=db.get("host", "localhost"),
            db_port=int(db.get("port", 5432)),
            db_name=db.get("dbname", "db_quant_trader"),
            db_user=db.get("user", ""),
            db_password=db.get("password", ""),
            status_host=status.get("host", "127.0.0.1"),
            status_port=int(status.get("port", 58080)),
            kline_buffer_seconds=int(kline.get("buffer_seconds", 1)),
        )
        logger.info("已加载静态配置: exchanges.yaml (sandbox=%s)", self._exchange_cfg.sandbox)
        return self._exchange_cfg

    def load_strategies(self) -> StrategyGlobalConfig:
        """加载 strategies.yaml（动态配置，支持热重载）。"""
        path = self.config_dir / "strategies.yaml"
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        self._validate_strategies(raw)

        strategies = []
        for s in raw.get("strategies", []):
            strategies.append(StrategyConfig(
                name=s["name"],
                enabled=s.get("enabled", True),
                symbols=s.get("symbols", []),
                timeframes=s.get("timeframes", []),
                params=s.get("params", {}),
            ))

        self._strategy_cfg = StrategyGlobalConfig(
            mode=raw.get("mode", "paper"),
            strategies=strategies,
        )
        logger.info("已加载策略配置: %d 个策略, 模式=%s", len(strategies), self._strategy_cfg.mode)
        return self._strategy_cfg

    def load_risk(self) -> RiskConfig:
        """加载 risk.yaml（动态配置，支持热重载）。"""
        path = self.config_dir / "risk.yaml"
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        self._validate_risk(raw)

        self._risk_cfg = RiskConfig(
            leverage_limits=raw.get("leverage_limits", {}),
            major_symbols=raw.get("major_symbols", ["BTC", "ETH", "SOL"]),
            stop_loss=raw.get("stop_loss", {}),
            position_limits=raw.get("position_limits", {}),
            max_leveraged_loss_pct=float(raw.get("max_leveraged_loss_pct", 15)),
            max_consecutive_losses=int(raw.get("max_consecutive_losses", 3)),
            cooldown_duration_seconds=int(raw.get("cooldown_duration_seconds", 3600)),
            max_total_risk_pct=float(raw.get("max_total_risk_pct", 8.0)),
        )
        logger.info("已加载风控配置: 杠杆限制=%s", self._risk_cfg.leverage_limits)
        return self._risk_cfg

    def load_backtest(self) -> BacktestConfig:
        """加载 backtest.yaml（回测专用配置）。

        文件不存在时返回默认 BacktestConfig，便于在无 backtest.yaml 环境运行实盘代码。
        """
        path = self.config_dir / "backtest.yaml"
        if not path.exists():
            self._backtest_cfg = BacktestConfig()
            logger.info("backtest.yaml 不存在，使用默认回测配置")
            return self._backtest_cfg

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        db = raw.get("backtest_database", {})
        bt = raw.get("backtest", {})
        fetch = raw.get("data_fetch", {})

        self._backtest_cfg = BacktestConfig(
            db_host=db.get("host", "localhost"),
            db_port=int(db.get("port", 5432)),
            db_name=db.get("dbname", "db_backtest"),
            db_user=db.get("user", ""),
            db_password=db.get("password", ""),
            initial_balance=float(bt.get("initial_balance", 100000.0)),
            funding_rate_cache_dir=bt.get("funding_rate_cache_dir", "quant_trader/data/funding_rates"),
            default_type=bt.get("default_type", "swap"),
            current_tolerance=float(fetch.get("current_tolerance", 0.001)),
            parent_tolerance=float(fetch.get("parent_tolerance", 0.005)),
            max_retries=int(fetch.get("max_retries", 5)),
        )
        logger.info("已加载回测配置: db=%s", self._backtest_cfg.db_name)
        return self._backtest_cfg

    def start_watching(self, on_change: Callable | None = None):
        """启动文件监听，动态配置修改后自动重新加载。"""
        if on_change:
            self._callbacks.append(on_change)

        handler = _ConfigChangeHandler(self._on_config_change)
        self._watcher = Observer()
        self._watcher.schedule(handler, str(self.config_dir), recursive=False)
        self._watcher.start()
        logger.info("配置文件监听已启动")

    def stop_watching(self):
        """停止文件监听。"""
        if self._watcher:
            self._watcher.stop()
            self._watcher.join()
            logger.info("配置文件监听已停止")

    @property
    def exchange(self) -> ExchangeConfig:
        if not self._exchange_cfg:
            self.load_static()
        return self._exchange_cfg

    @property
    def strategies(self) -> StrategyGlobalConfig:
        if not self._strategy_cfg:
            self.load_strategies()
        return self._strategy_cfg

    @property
    def risk(self) -> RiskConfig:
        if not self._risk_cfg:
            self.load_risk()
        return self._risk_cfg

    @property
    def backtest(self) -> BacktestConfig:
        if not self._backtest_cfg:
            self.load_backtest()
        return self._backtest_cfg

    def on_config_change(self, callback: Callable):
        """注册配置变更回调。"""
        self._callbacks.append(callback)

    def _on_config_change(self, event):
        """处理配置文件变更事件。"""
        path = Path(event.src_path)
        if path.name in ("strategies.yaml", "risk.yaml"):
            logger.info("配置文件变更: %s，正在重新加载...", path.name)
            try:
                if path.name == "strategies.yaml":
                    self.load_strategies()
                elif path.name == "risk.yaml":
                    self.load_risk()
                for cb in self._callbacks:
                    cb(path.name)
            except Exception:
                logger.exception("重新加载配置失败 %s", path.name)

    def _validate_strategies(self, raw: dict):
        """校验 strategies.yaml 结构。"""
        strategies = raw.get("strategies", [])
        if not isinstance(strategies, list):
            raise ValueError("strategies 必须是一个列表")
        for i, s in enumerate(strategies):
            if "name" not in s:
                raise ValueError(f"策略 {i} 缺少必填字段 'name'")
            mode = raw.get("mode", "paper")
            if mode not in ("paper", "live_confirm", "live_auto"):
                raise ValueError(f"无效的交易模式 '{mode}'，必须是 paper/live_confirm/live_auto")

    def _validate_risk(self, raw: dict):
        """校验 risk.yaml 数值在安全范围内。"""
        limits = raw.get("leverage_limits", {})
        major = limits.get("major_coins", 5)
        alt = limits.get("alt_coins", 3)
        if major > 20:
            logger.warning("主流币杠杆 %d 过高，已限制为 20", major)
            raw["leverage_limits"]["major_coins"] = 20
        if alt > 10:
            logger.warning("山寨币杠杆 %d 过高，已限制为 10", alt)
            raw["leverage_limits"]["alt_coins"] = 10


class _ConfigChangeHandler(FileSystemEventHandler):
    """Watchdog 文件变更事件处理器。"""

    def __init__(self, on_modified: Callable):
        super().__init__()
        self._on_modified = on_modified
        self._last_modified: dict[str, float] = {}

    def on_modified(self, event):
        if event.is_directory:
            return
        # 去抖：保存操作会触发多次事件，只处理第一次
        now = time.time()
        if now - self._last_modified.get(event.src_path, 0) < 1.0:
            return
        self._last_modified[event.src_path] = now
        self._on_modified(event)
