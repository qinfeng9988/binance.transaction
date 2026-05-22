"""风控管理器。

所有真实下单前必须通过风控检查。包含：
- 杠杆上限（主流币/山寨币分类）
- 单笔止损（按仓位价值百分比）
- 单笔最大亏损（按保证金百分比）
- 自动降杠杆
- 日累计亏损限制
- 仓位占比限制
- 最大同时持仓数
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RiskManager:
    """风控检查器。"""

    def __init__(self, risk_config, data_store):
        """初始化风控。

        参数:
            risk_config: RiskConfig 对象
            data_store: DataStore 实例
        """
        self._config = risk_config
        self._data_store = data_store
        self._daily_loss = 0.0
        self._last_reset_date = datetime.now(tz=timezone.utc).date()

    def check_and_adjust(self, signal) -> dict | None:
        """对信号进行风控检查，必要时自动调整杠杆。

        返回:
            调整后的信号参数字典 {"leverage", "stop_loss", "approved"}，
            如果拒绝交易则 approved=False。
        """
        # 每日重置
        self._reset_if_new_day()

        cfg = self._config
        symbol_base = signal.symbol.split("/")[0]  # "BTC" from "BTC/USDT"

        # 1. 杠杆上限（主流币/山寨币）
        is_major = symbol_base in cfg.major_symbols
        max_leverage = cfg.leverage_limits["major_coins"] if is_major else cfg.leverage_limits["alt_coins"]
        if signal.leverage > max_leverage:
            logger.warning("[风控] %s 杠杆 %dx 超过上限 %dx（%s），已自动调整",
                           signal.symbol, signal.leverage, max_leverage,
                           "主流币" if is_major else "山寨币")
            signal.leverage = max_leverage

        # 2. 止损距离（按仓位价值百分比）
        stop_loss_distance_pct = abs(signal.price - signal.stop_loss) / signal.price * 100
        if stop_loss_distance_pct > cfg.stop_loss.get("max_pct", 2.5):
            logger.warning("[风控] %s 止损距离 %.2f%% 超过上限 %.2f%%，拒绝交易",
                           signal.symbol, stop_loss_distance_pct, cfg.stop_loss["max_pct"])
            return {"approved": False, "reason": "止损距离超限"}

        # 3. 单笔最大亏损（保证金百分比）
        max_loss_pct = cfg.position_limits.get("max_loss_per_trade_pct", 15)
        leveraged_loss_pct = stop_loss_distance_pct * signal.leverage
        if leveraged_loss_pct > cfg.max_leveraged_loss_pct:
            # 自动降杠杆
            new_leverage = int(cfg.max_leveraged_loss_pct / stop_loss_distance_pct)
            new_leverage = max(1, new_leverage)
            logger.warning("[风控] %s 杠杆亏损 %.2f%% 超限 %.2f%%，杠杆从 %dx 降至 %dx",
                           signal.symbol, leveraged_loss_pct, cfg.max_leveraged_loss_pct,
                           signal.leverage, new_leverage)
            signal.leverage = new_leverage
            leveraged_loss_pct = stop_loss_distance_pct * new_leverage

        # 4. 降杠杆后仍然超限则拒绝
        if leveraged_loss_pct > max_loss_pct:
            logger.warning("[风控] %s 降杠杆后仍超限 %.2f%% > %.2f%%，拒绝交易",
                           signal.symbol, leveraged_loss_pct, max_loss_pct)
            return {"approved": False, "reason": "降杠杆后仍超限"}

        # 5. 日累计亏损检查
        if self._daily_loss >= cfg.position_limits.get("max_daily_loss_pct", 30):
            logger.warning("[风控] 日累计亏损 %.2f%% 已达上限，拒绝所有新交易", self._daily_loss)
            return {"approved": False, "reason": "日亏损已达上限"}

        # 6. 最大持仓数
        open_count = self._data_store.get_position_count()
        max_positions = cfg.position_limits.get("max_concurrent_positions", 5)
        if open_count >= max_positions:
            logger.warning("[风控] 当前持仓 %d 已达上限 %d，拒绝新开仓", open_count, max_positions)
            return {"approved": False, "reason": "持仓数已达上限"}

        # 7. 单币种仓位占比
        open_positions = self._data_store.get_open_positions()
        symbol_positions = [p for p in open_positions if p.symbol == signal.symbol]
        if symbol_positions:
            total_qty = sum(p.quantity for p in symbol_positions)
            # 粗略检查：新增仓位后是否超限（需要总资金信息，此处简化）
            pass  # TODO: 需要传入总资金才能计算占比

        logger.debug("[风控] %s 检查通过: 杠杆=%dx, 止损距离=%.2f%%, 杠杆亏损=%.2f%%",
                     signal.symbol, signal.leverage, stop_loss_distance_pct, leveraged_loss_pct)

        return {
            "approved": True,
            "leverage": signal.leverage,
            "stop_loss": signal.stop_loss,
        }

    def record_loss(self, loss_pct: float):
        """记录亏损（平仓后调用）。"""
        self._daily_loss += loss_pct
        logger.info("[风控] 记录亏损 %.2f%%，日累计 %.2f%%", loss_pct, self._daily_loss)

    def _reset_if_new_day(self):
        """新的一天重置日亏损计数器。"""
        today = datetime.now(tz=timezone.utc).date()
        if today != self._last_reset_date:
            logger.info("[风控] 新交易日，重置日亏损计数器（昨日累计 %.2f%%）", self._daily_loss)
            self._daily_loss = 0.0
            self._last_reset_date = today
