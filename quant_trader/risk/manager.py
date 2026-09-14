"""风控管理器。

所有真实下单前必须通过风控检查。包含：
- 杠杆上限（主流币/山寨币分类）
- 单笔止损（按仓位价值百分比）
- 单笔最大亏损（按保证金百分比）
- 自动降杠杆
- 日累计亏损限制
- 仓位占比限制
- 最大同时持仓数
- 连续亏损冷静期（参数化）
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class RiskManager:
    """风控检查器。"""

    def __init__(self, risk_config, data_store, clock: Callable[[], datetime] | None = None):
        """初始化风控。

        参数:
            risk_config: RiskConfig 对象
            data_store: DataStore 实例
            clock: 业务时钟（返回 UTC datetime），默认真实墙钟。
                回测必须传入跟随 bar 时间的时钟，否则冷静期/日重置
                按真实时间计算，回测几分钟跑完数月，行为完全失真。
        """
        self._config = risk_config
        self._data_store = data_store
        self._clock = clock or (lambda: datetime.now(tz=timezone.utc))
        self._daily_loss = 0.0
        self._last_reset_date = self._clock().date()

        # 连续亏损/冷静期状态
        self._consecutive_losses = 0
        self._cooldown_end: datetime | None = None

    # ============================================================
    # 参数读取（从配置）
    # ============================================================

    @property
    def max_consecutive_losses(self) -> int:
        return self._config.max_consecutive_losses

    @property
    def cooldown_duration_seconds(self) -> int:
        return self._config.cooldown_duration_seconds

    @property
    def max_total_risk_pct(self) -> float:
        return self._config.max_total_risk_pct

    @property
    def consecutive_losses(self) -> int:
        """当前连续亏损次数（供信号路由层判断加仓降级）。"""
        return self._consecutive_losses

    def check_and_adjust(self, signal) -> dict | None:
        """对信号进行风控检查，必要时自动调整杠杆。

        返回:
            调整后的信号参数字典 {"leverage", "stop_loss", "approved"}，
            如果拒绝交易则 approved=False。
        """
        # 每日重置
        self._reset_if_new_day()

        # 连续亏损冷静期检查（仅对新开仓生效，反向平仓和加仓由信号路由层处理）
        existing_pos = None
        try:
            open_positions = self._data_store.get_open_positions()
            existing_pos = next((p for p in open_positions if p.symbol == signal.symbol), None)
        except Exception:
            logger.debug("查询 open positions 失败", exc_info=True)

        is_new_open = existing_pos is None
        if is_new_open and self._is_in_cooldown():
            logger.warning(
                "[风控] %s 新开仓被拒：连续亏损 %d 次，冷静期至 %s",
                signal.symbol, self._consecutive_losses, self._cooldown_end,
            )
            remaining = int((self._cooldown_end - self._clock()).total_seconds())
            return {
                "approved": False,
                "reason": f"连续亏损冷静期（剩 {remaining} 秒）",
            }

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

        # 5. 日累计亏损检查（仅对新开仓拦截，反向平仓/加仓不受限）
        if is_new_open and self._daily_loss >= cfg.position_limits.get("max_daily_loss_pct", 30):
            logger.warning("[风控] 日累计亏损 %.2f%% 已达上限，拒绝新开仓", self._daily_loss)
            return {"approved": False, "reason": "日亏损已达上限"}

        # 6. 最大持仓数（仅对新开仓检查；反向平仓减少持仓、加仓不增持仓数）
        if is_new_open:
            open_count = self._data_store.get_position_count()
            max_positions = cfg.position_limits.get("max_concurrent_positions", 5)
            if open_count >= max_positions:
                logger.warning("[风控] 当前持仓 %d 已达上限 %d，拒绝新开仓", open_count, max_positions)
                return {"approved": False, "reason": "持仓数已达上限"}

        # 7. 单币种仓位占比
        symbol_positions = [p for p in open_positions if p.symbol == signal.symbol]
        if symbol_positions:
            # 粗略检查：新增仓位后是否超限（需要总资金信息，此处简化）
            pass  # TODO: 需要传入总资金才能计算占比

        logger.debug("[风控] %s 检查通过: 杠杆=%dx, 止损距离=%.2f%%, 杠杆亏损=%.2f%%",
                     signal.symbol, signal.leverage, stop_loss_distance_pct, leveraged_loss_pct)

        return {
            "approved": True,
            "leverage": signal.leverage,
            "stop_loss": signal.stop_loss,
        }

    # ============================================================
    # 连续亏损/冷静期管理
    # ============================================================

    def _is_in_cooldown(self) -> bool:
        """当前是否处于连续亏损冷静期内。"""
        if self._cooldown_end is None:
            return False
        return self._clock() < self._cooldown_end

    def record_loss(self, loss_pct: float):
        """记录亏损（平仓后调用）。

        连续亏损达阈值时触发冷静期。
        """
        self._daily_loss += loss_pct
        self._consecutive_losses += 1
        logger.info(
            "[风控] 记录亏损 %.2f%%，连续 %d 次，日累计 %.2f%%",
            loss_pct, self._consecutive_losses, self._daily_loss,
        )

        if self._consecutive_losses >= self.max_consecutive_losses and self._cooldown_end is None:
            self._cooldown_end = self._clock() + timedelta(seconds=self.cooldown_duration_seconds)
            logger.warning(
                "[风控] 连续亏损 %d 次达阈值，进入冷静期 %d 秒，至 %s",
                self._consecutive_losses, self.cooldown_duration_seconds, self._cooldown_end,
            )

    def record_win(self):
        """记录盈利（平仓后调用），重置连续亏损计数。"""
        if self._consecutive_losses > 0:
            logger.info("[风控] 盈利，重置连续亏损计数 %d → 0", self._consecutive_losses)
        self._consecutive_losses = 0
        # 盈利不立即解除冷静期（防止抖动），到期自动解除

    def _reset_if_new_day(self):
        """新的一天重置日亏损计数器。"""
        today = self._clock().date()
        if today != self._last_reset_date:
            logger.info("[风控] 新交易日，重置日亏损计数器（昨日累计 %.2f%%）", self._daily_loss)
            self._daily_loss = 0.0
            self._last_reset_date = today
