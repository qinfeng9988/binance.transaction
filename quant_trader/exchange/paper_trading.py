"""模拟交易引擎。

在 paper 模式下替代真实交易所下单，模拟：
- 账户余额管理
- 下单、成交、撤单
- 持仓盈亏计算
- 手续费模拟
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core.data_store import OrderRecord, PositionRecord, EntryRecord, ClosureRecord

logger = logging.getLogger(__name__)

# order_type → (reason 事件类型, 默认 trigger_reason)
# reason 是机械事实（怎么平的），trigger_reason 是业务语义（为什么平）
_ORDER_TYPE_MAP = {
    "close": ("CLOSE_FULL", "signal_close"),
    "take_profit_close": ("CLOSE_FULL", "take_profit"),
    "stop_loss_close": ("STOP_LOSS", "stop_loss"),
    "end_of_data": ("END_OF_DATA", "end_of_backtest"),
}


@dataclass
class MockOrder:
    """模拟订单。"""
    order_id: str
    symbol: str
    side: str                      # "buy" 或 "sell"（内部使用，DB 用 quantity 正负表示）
    amount: float                  # 成交数量（始终为正数）
    price: float
    order_type: str                # "open" / "add" / "close" / "partial_close" / "stop_loss_close" / "take_profit_close"
    leverage: int = 1
    status: str = "filled"         # 模拟单直接成交
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    fee: float = 0.0
    position_id: int | None = None # 关联持仓 ID


@dataclass
class MockPosition:
    """模拟持仓。"""
    symbol: str
    side: str
    entry_price: float
    quantity: float
    leverage: int
    current_price: float = 0.0
    stop_loss: float = 0.0
    entry_time: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    add_count: int = 0
    position_id: int | None = None     # 数据库中的持仓 ID
    closed_quantity: float = 0.0       # 累计已平仓数量
    close_count: int = 0               # 平仓次数
    entry_seq_counter: int = 0         # 入场明细序号计数器
    partial_tp_triggered: list = field(default_factory=list)  # 已触发的部分止盈级别


class PaperTradingEngine:
    """模拟交易引擎，维护虚拟账户和持仓。"""

    # 模拟手续费（Binance 现货 0.1%，合约 0.04%）
    FEE_RATE_SPOT = 0.001
    FEE_RATE_SWAP = 0.0004

    def __init__(self, initial_balance: float = 100000.0, default_type: str = "swap"):
        """初始化模拟账户。

        参数:
            initial_balance: 初始资金（USDT）
            default_type: "spot"(现货) 或 "swap"(合约)
        """
        self.initial_balance = initial_balance
        self.balance = initial_balance           # 可用余额
        self.default_type = default_type
        self.orders: list[MockOrder] = []        # 订单历史
        self.positions: dict[str, MockPosition] = {}  # 当前持仓，key=symbol
        self.trades_closed: list[dict] = []      # 已平仓记录
        self._order_counter = 0
        self.fee_rate = self.FEE_RATE_SWAP if default_type == "swap" else self.FEE_RATE_SPOT

    def place_order(self, symbol: str, side: str, amount: float,
                    price: float, leverage: int = 1,
                    stop_loss: float = 0.0, data_store=None,
                    metadata: dict | None = None,
                    order_type: str = "market",
                    signal_id: int | None = None) -> MockOrder | None:
        """模拟下单。

        模拟模式下市价单立即成交，限价单挂入等待成交。
        新流程：先确认/创建 positions 记录拿到 position_id，再插 orders。

        参数:
            symbol: 交易对
            side: "buy" 或 "sell"
            amount: 数量（始终为正数）
            price: 成交价格
            leverage: 杠杆倍数
            stop_loss: 止损价
            order_type: 订单类型提示，不传时由 _update_position 根据场景推断
            signal_id: 触发本笔订单的信号 ID

        返回:
            MockOrder 对象，失败返回 None
        """
        # 检查余额是否足够（合约需要保证金）
        if self.default_type == "swap":
            required_margin = (price * amount) / leverage
            fee = price * amount * self.fee_rate
            if required_margin + fee > self.balance:
                logger.warning("[模拟] 保证金不足: 需要 %.2f, 可用 %.2f",
                               required_margin + fee, self.balance)
                return None
        else:
            cost = price * amount
            fee = cost * self.fee_rate
            if cost + fee > self.balance:
                logger.warning("[模拟] 余额不足: 需要 %.2f, 可用 %.2f",
                               cost + fee, self.balance)
                return None

        self._order_counter += 1
        order = MockOrder(
            order_id=f"PAPER-{self._order_counter}",
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            order_type=order_type,
            leverage=leverage,
            status="filled",
            fee=fee,
        )

        # 扣除手续费和保证金
        if self.default_type == "swap":
            required_margin = (price * amount) / leverage
            self.balance -= (required_margin + fee)
        else:
            self.balance -= (price * amount + fee)

        # 更新或创建持仓，返回实际 order_type 和 position_id
        order.order_type, order.position_id = self._update_position(
            order, stop_loss, data_store, metadata=metadata, signal_id=signal_id
        )

        # 同步订单到数据库
        if data_store:
            data_store.insert_order(self._to_order_record(order))

        self.orders.append(order)

        logger.info(
            "[模拟] %s %s %s 价格=%.2f 数量=%.4f 杠杆=%dx 手续费=%.2f",
            side.upper(), symbol, order.order_id, price, amount, leverage, fee,
        )
        return order

    def _to_order_record(self, order: MockOrder) -> OrderRecord:
        """把 MockOrder 转成 DataStore 的 OrderRecord。"""
        # quantity 符号：开仓/加仓为正，平仓/减仓为负
        if order.order_type in ("open", "add"):
            signed_quantity = order.amount
        else:
            signed_quantity = -order.amount

        return OrderRecord(
            symbol=order.symbol,
            price=order.price,
            quantity=signed_quantity,
            leverage=order.leverage,
            timestamp=order.timestamp,
            mode="paper",
            fee=order.fee,
            status=order.status,
            order_id=order.order_id,
        )

    def restore_from_db(self, data_store) -> None:
        """从数据库恢复持仓和余额状态。

        程序重启时，从 positions 表加载未平仓记录，重建持仓对象，
        从 orders 表重建订单计数器和余额。

        参数:
            data_store: DataStore 实例
        """
        # 1. 加载所有历史订单，重建 order_counter
        with data_store._get_conn().cursor() as cur:
            cur.execute(
                "SELECT id FROM orders WHERE mode = 'paper' ORDER BY id"
            )
            order_rows = cur.fetchall()
            if order_rows:
                self._order_counter = order_rows[-1][0]  # 从最大 ID 继续

        # 2. 加载已平仓记录，用于统计（含手续费）
        with data_store._get_conn().cursor() as cur:
            # 2a. 加载已平仓的 positions 记录
            cur.execute(
                """SELECT p.id, p.symbol, p.side, p.entry_price, p.closed_quantity,
                          p.leverage, p.entry_time, p.exit_price, p.exit_time, p.pnl,
                          p.close_count, p.total_fee
                   FROM positions p WHERE p.status = 'closed'"""
            )
            closed_rows = cur.fetchall()
            for r in closed_rows:
                pos_id = r[0]
                symbol = r[1]
                side = r[2]
                entry_price = float(r[3])
                closed_qty = float(r[4])
                leverage = r[5]
                entry_time = r[6]
                exit_price = float(r[7])
                exit_time = r[8]
                pnl = float(r[9])
                close_count = r[10]
                total_fee = float(r[11] or 0)

                self.trades_closed.append({
                    "symbol": symbol,
                    "side": side,
                    "entry_price": entry_price,
                    "quantity": closed_qty,       # 总平仓数量
                    "leverage": leverage,
                    "entry_time": entry_time,
                    "exit_price": exit_price,
                    "exit_time": exit_time,
                    "pnl": pnl,
                    "fee": total_fee,
                    "position_id": pos_id,
                    "close_count": close_count,
                })

        # 3. 从已平仓交易推算余额
        # 初始余额 + 累计盈亏 = 当前可用余额（不含未平仓部分的占用资金）
        total_closed_pnl = sum(t["pnl"] for t in self.trades_closed)
        self.balance += total_closed_pnl

        # 4. 加载当前持仓
        open_positions = data_store.get_open_positions()
        for pos in open_positions:
            # 重建 MockPosition
            mock_pos = MockPosition(
                symbol=pos.symbol,
                side=pos.side,
                entry_price=pos.entry_price,
                quantity=pos.quantity,
                leverage=pos.leverage,
                current_price=pos.entry_price,  # 价格会在下一轮 K 线更新
                stop_loss=pos.stop_loss,
                entry_time=pos.entry_time,
                position_id=pos.id,
                closed_quantity=pos.closed_quantity,
                close_count=pos.close_count,
                partial_tp_triggered=pos.partial_tp_triggered.split(",") if pos.partial_tp_triggered else [],
            )
            self.positions[pos.symbol] = mock_pos

            # 恢复 entry_seq_counter（从 position_entries 表查询最大 seq）
            if pos.id is not None:
                with data_store._get_conn().cursor() as cur:
                    cur.execute(
                        "SELECT COALESCE(MAX(seq), 0) FROM position_entries WHERE position_id = %s",
                        (pos.id,),
                    )
                    mock_pos.entry_seq_counter = cur.fetchone()[0]

            # 扣除对应的占用资金（恢复被锁定的资金状态）
            if self.default_type == "swap":
                # 合约：扣除保证金
                required_margin = (pos.entry_price * pos.quantity) / pos.leverage
                self.balance -= required_margin
            else:
                # 现货：扣除买入成本
                cost = pos.entry_price * pos.quantity
                fee = cost * self.fee_rate
                self.balance -= (cost + fee)

        if open_positions:
            logger.info(
                "从数据库恢复 %d 个持仓: %s",
                len(open_positions),
                ", ".join(p.symbol for p in open_positions),
            )
        else:
            logger.info("数据库中无未平仓记录，账户状态已重置")

    def close_position(self, symbol: str, exit_price: float, data_store=None,
                       order_type: str = "close", signal_id: int | None = None,
                       order_id: str | None = None,
                       trigger_reason: str | None = None) -> float | None:
        """全平。

        参数:
            symbol: 交易对
            exit_price: 平仓价格
            data_store: DataStore 实例（可选，用于同步到 DB）
            order_type: 订单类型，"close" / "stop_loss_close" / "take_profit_close" / "end_of_data"
            signal_id: 触发平仓的信号 ID（止损时为 None）
            order_id: 触发本次平仓的订单编号
            trigger_reason: 策略触发原因，未传时按 order_type 取默认值

        返回:
            实现的盈亏，无持仓则返回 None
        """
        if symbol not in self.positions:
            logger.warning("[模拟] 无持仓，无法平仓: %s", symbol)
            return None

        reason, default_trigger = _ORDER_TYPE_MAP.get(order_type, ("CLOSE_FULL", "signal_close"))
        trigger = trigger_reason or default_trigger

        pos = self.positions[symbol]
        entry_price = pos.entry_price
        quantity = pos.quantity
        leverage = pos.leverage

        # 计算盈亏
        if pos.side == "buy":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        # 扣除平仓手续费
        exit_value = exit_price * quantity
        fee = exit_value * self.fee_rate
        net_pnl = pnl - fee

        # 返还保证金 + 盈亏
        if self.default_type == "swap":
            margin = (entry_price * quantity) / leverage
            self.balance += margin + net_pnl
        else:
            self.balance += exit_value - fee

        # 更新持仓的累计统计
        pos.closed_quantity += quantity
        pos.close_count += 1
        pos.realized_pnl += net_pnl

        # FIFO 归因：按 entry 顺序扣减并写入 closures
        if data_store and pos.position_id is not None:
            self._close_entries_fifo(
                data_store, pos.position_id, exit_price, quantity,
                fee, reason, pos.side, signal_id, order_id=order_id,
                trigger_reason=trigger,
            )

        # 同步到数据库
        if data_store and pos.position_id is not None:
            data_store.close_position(
                position_id=pos.position_id,
                exit_price=exit_price,
                closed_quantity=pos.closed_quantity,
                pnl=pos.realized_pnl,
                close_count=pos.close_count,
                total_fee=fee,
            )

        # 记录已平仓
        self.trades_closed.append({
            "symbol": symbol,
            "side": pos.side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "leverage": leverage,
            "pnl": net_pnl,
            "fee": fee,
            "entry_time": pos.entry_time,
            "exit_time": datetime.now(tz=timezone.utc),
            "position_id": pos.position_id,
            "order_type": order_type,
        })

        # 移除持仓
        del self.positions[symbol]

        logger.info(
            "[模拟] 全平 %s %s 入场=%.2f 出场=%.2f 盈亏=%.2f(净) 手续费=%.2f order_type=%s",
            symbol, pos.side, entry_price, exit_price, net_pnl, fee, order_type,
        )
        return net_pnl

    def update_prices(self, symbol: str, current_price: float, data_store=None):
        """更新某币种的当前价格，重新计算未实现盈亏。

        参数:
            symbol: 交易对
            current_price: 最新价格
            data_store: DataStore 实例（可选，用于同步止损平仓到 DB）
        """
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        pos.current_price = current_price

        if pos.side == "buy":
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
        else:
            pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity

        # 检查止损
        if pos.stop_loss > 0:
            if (pos.side == "buy" and current_price <= pos.stop_loss) or \
               (pos.side == "sell" and current_price >= pos.stop_loss):
                logger.info("[模拟] 触发止损: %s 现价=%.2f 止损=%.2f",
                            symbol, current_price, pos.stop_loss)
                pnl = self.close_position(symbol, current_price, data_store,
                                          order_type="stop_loss_close",
                                          signal_id=None)

    def get_position_count(self) -> int:
        """当前持仓数。"""
        return len(self.positions)

    def get_total_equity(self) -> float:
        """总权益 = 可用余额 + 未实现盈亏 + 占用保证金。"""
        equity = self.balance
        for pos in self.positions.values():
            if self.default_type == "swap":
                margin = (pos.entry_price * pos.quantity) / pos.leverage
                equity += margin + pos.unrealized_pnl
            else:
                equity += pos.unrealized_pnl
        return equity

    def get_summary(self) -> dict:
        """账户概览。"""
        closed_pnl = sum(t["pnl"] for t in self.trades_closed)
        total_fees = sum(t["fee"] for t in self.trades_closed)
        wins = [t for t in self.trades_closed if t["pnl"] > 0]
        losses = [t for t in self.trades_closed if t["pnl"] <= 0]

        return {
            "initial_balance": self.initial_balance,
            "balance": round(self.balance, 2),
            "total_equity": round(self.get_total_equity(), 2),
            "open_positions": len(self.positions),
            "total_trades": len(self.trades_closed),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(self.trades_closed) * 100, 2) if self.trades_closed else 0,
            "total_pnl": round(closed_pnl, 2),
            "total_fees": round(total_fees, 2),
            "return_pct": round((self.get_total_equity() - self.initial_balance) / self.initial_balance * 100, 2),
        }

    def update_stop_loss(self, symbol: str, new_stop: float) -> bool:
        """更新持仓止损位（只能向有利方向移动）。"""
        pos = self.positions.get(symbol)
        if not pos:
            return False
        side = pos.side
        current_stop = pos.stop_loss
        if side == "buy" and new_stop > current_stop:
            pos.stop_loss = new_stop
            return True
        if side == "sell" and new_stop < current_stop:
            pos.stop_loss = new_stop
            return True
        return False

    def partial_close_position(self, symbol: str, close_price: float,
                                close_ratio: float, data_store=None,
                                signal_id: int = None,
                                partial_tp_level: str = None) -> float | None:
        """部分平仓指定比例的仓位。

        参数:
            partial_tp_level: 部分止盈级别 ID（如 "2.0R"），用于去重防止重复触发

        返回实现盈亏，无持仓或已全部平仓返回 None。
        """
        pos = self.positions.get(symbol)
        if not pos:
            return None
        qty = pos.quantity
        close_qty = qty * close_ratio
        if close_qty <= 0:
            return None
        entry = pos.entry_price
        side = pos.side

        if side == "buy":
            pnl = (close_price - entry) * close_qty
        else:
            pnl = (entry - close_price) * close_qty

        exit_value = close_price * close_qty
        fee = exit_value * self.fee_rate
        net_pnl = pnl - fee

        if self.default_type == "swap":
            margin = (entry * close_qty) / pos.leverage
            self.balance += margin + net_pnl
        else:
            self.balance += close_price * close_qty - fee

        remaining = qty - close_qty

        # 更新持仓的累计统计
        pos.closed_quantity += close_qty
        pos.close_count += 1
        pos.realized_pnl += net_pnl

        # 生成订单号
        self._order_counter += 1
        order_id = f"PAPER-{self._order_counter}"

        # FIFO 归因：按 entry 顺序扣减并写入 closures
        if data_store and pos.position_id is not None:
            self._close_entries_fifo(
                data_store, pos.position_id, close_price, close_qty,
                fee, "CLOSE_PARTIAL", side, signal_id, order_id=order_id,
                trigger_reason=partial_tp_level,
            )

        # 写入 orders 表
        if data_store and pos.position_id is not None:
            data_store.insert_order(OrderRecord(
                symbol=symbol,
                price=close_price,
                quantity=-close_qty,  # 减仓为负
                leverage=pos.leverage,
                timestamp=datetime.now(tz=timezone.utc),
                mode="paper",
                fee=fee,
                status="filled",
                order_id=order_id,
            ))

        # 更新已触发的部分止盈级别
        if partial_tp_level and partial_tp_level not in pos.partial_tp_triggered:
            pos.partial_tp_triggered.append(partial_tp_level)
        triggered_str = ",".join(pos.partial_tp_triggered)

        if remaining <= 1e-12:
            # 全部平掉了，同步到 DB 并删除持仓
            if data_store and pos.position_id is not None:
                data_store.close_position(
                    position_id=pos.position_id,
                    exit_price=close_price,
                    closed_quantity=pos.closed_quantity,
                    pnl=pos.realized_pnl,
                    close_count=pos.close_count,
                    total_fee=fee,
                )
            del self.positions[symbol]
        else:
            # 还有剩余，更新剩余数量并同步到 DB
            pos.quantity = remaining
            if data_store and pos.position_id is not None:
                data_store.update_position(
                    symbol=symbol,
                    quantity=remaining,
                    entry_price=entry,
                    stop_loss=pos.stop_loss,
                    closed_quantity=pos.closed_quantity,
                    close_count=pos.close_count,
                    pnl=pos.realized_pnl,
                    total_fee=fee,
                    partial_tp_triggered=triggered_str,
                )

        self.trades_closed.append({
            "symbol": symbol,
            "side": side,
            "entry_price": entry,
            "exit_price": close_price,
            "quantity": close_qty,
            "leverage": pos.leverage,
            "pnl": net_pnl,
            "fee": fee,
            "entry_time": pos.entry_time,
            "exit_time": datetime.now(tz=timezone.utc),
            "position_id": pos.position_id,
            "order_type": "partial_close",
        })

        logger.info(
            "[模拟] 部分平仓 %s %s 数量=%.4f 剩余=%.4f 入场=%.2f 出场=%.2f 盈亏=%.2f 手续费=%.2f",
            symbol, side, close_qty, remaining, entry, close_price, net_pnl, fee,
        )
        return net_pnl

    def _update_position(self, order: MockOrder, stop_loss: float, data_store=None,
                         metadata: dict | None = None, signal_id: int | None = None) -> tuple[str, int | None]:
        """更新持仓（开仓或加仓或平仓）。

        返回 (order_type, position_id)。
        """
        # 计算入场手续费
        entry_fee = order.price * order.amount * self.fee_rate

        if order.symbol in self.positions:
            existing = self.positions[order.symbol]
            # 反向单 = 平仓
            if order.side != existing.side:
                position_id = existing.position_id
                close_type = order.order_type if order.order_type in (
                    "close", "stop_loss_close", "take_profit_close"
                ) else "close"
                self.close_position(
                    order.symbol, order.price, data_store,
                    order_type=close_type, signal_id=signal_id,
                    order_id=order.order_id,
                    trigger_reason="reverse_signal",
                )
                return close_type, position_id
            # 同向单 = 加仓：检查最大加仓次数
            max_additions = metadata.get("max_additions", 1) if metadata else 1
            if existing.add_count >= max_additions:
                logger.info(
                    "[模拟] 忽略 %s %s 同向加仓: 已达最大次数 %d/%d",
                    order.symbol, order.side, existing.add_count, max_additions,
                )
                return "ignored", existing.position_id
            # 同向单 = 加仓：计算加权平均价
            total_qty = existing.quantity + order.amount
            avg_price = (existing.entry_price * existing.quantity + order.price * order.amount) / total_qty
            existing.entry_price = avg_price
            existing.quantity = total_qty
            existing.add_count += 1
            # 写入 entry 明细
            existing.entry_seq_counter += 1
            self._insert_entry(data_store, existing.position_id,
                               existing.entry_seq_counter, order.amount,
                               order.price, signal_id,
                               order_id=order.order_id, entry_fee=entry_fee)
            # 同步到数据库
            if data_store and existing.position_id is not None:
                data_store.update_position(
                    symbol=order.symbol,
                    quantity=total_qty,
                    entry_price=avg_price,
                    stop_loss=stop_loss,
                    total_fee=entry_fee,
                )
            return "add", existing.position_id

        # 新仓位
        position_id = None
        if data_store:
            position_id = data_store.open_position(PositionRecord(
                symbol=order.symbol,
                side=order.side,
                entry_price=order.price,
                quantity=order.amount,
                leverage=order.leverage,
                entry_time=datetime.now(tz=timezone.utc),
                stop_loss=stop_loss,
                status="open",
            ), total_fee=entry_fee)
        self.positions[order.symbol] = MockPosition(
            symbol=order.symbol,
            side=order.side,
            entry_price=order.price,
            quantity=order.amount,
            leverage=order.leverage,
            current_price=order.price,
            stop_loss=stop_loss,
            position_id=position_id,
            entry_seq_counter=1,
        )
        # 写入 entry 明细（首次开仓 seq=1）
        self._insert_entry(data_store, position_id, 1, order.amount,
                           order.price, signal_id,
                           order_id=order.order_id, entry_fee=entry_fee)
        return "open", position_id

    def _insert_entry(self, data_store, position_id: int | None,
                      seq: int, qty: float, price: float,
                      signal_id: int | None = None,
                      order_id: str | None = None,
                      entry_fee: float = 0.0):
        """写入一条入场明细到数据库。"""
        if data_store and position_id is not None:
            data_store.insert_entry(EntryRecord(
                position_id=position_id,
                seq=seq,
                orig_qty=qty,
                remaining_qty=qty,
                entry_price=price,
                entry_fee=entry_fee,
                order_id=order_id,
                signal_id=signal_id,
                status="open",
            ))

    def _close_entries_fifo(self, data_store, position_id: int,
                            exit_price: float, total_close_qty: float,
                            total_fee: float, reason: str, side: str,
                            signal_id: int | None = None,
                            order_id: str | None = None,
                            trigger_reason: str | None = None):
        """按 FIFO 顺序扣减 entries 并写入 closures。

        参数:
            data_store: DataStore 实例
            position_id: 持仓 ID
            exit_price: 出场价格
            total_close_qty: 本次总平仓数量
            total_fee: 本次平仓总手续费（按比例分摊到各 closure）
            reason: 事件类型 "CLOSE_PARTIAL" / "CLOSE_FULL" / "STOP_LOSS" / "END_OF_DATA"
            side: 持仓方向 "buy" / "sell"
            signal_id: 触发平仓的信号 ID（止损时为 None）
            order_id: 触发本次平仓的订单编号
            trigger_reason: 策略触发原因（如 "2.0R"、"reverse_signal"）
        """
        entries = data_store.get_open_entries(position_id)
        remaining_to_close = total_close_qty

        for entry in entries:
            if remaining_to_close <= 1e-12:
                break

            close_qty = min(entry.remaining_qty, remaining_to_close)

            # 计算归因盈亏
            direction = 1 if side == "buy" else -1
            raw_pnl = (exit_price - entry.entry_price) * close_qty * direction
            # 平仓手续费按比例分摊
            fee_share = total_fee * (close_qty / total_close_qty)
            # 入场手续费按比例分摊
            entry_fee_share = entry.entry_fee * (close_qty / entry.orig_qty)
            attributed_pnl = raw_pnl - fee_share - entry_fee_share

            # 写入 closure 记录
            data_store.insert_closure(ClosureRecord(
                entry_id=entry.id,
                close_qty=close_qty,
                entry_price=entry.entry_price,
                exit_price=exit_price,
                pnl=attributed_pnl,
                fee=fee_share,
                reason=reason,
                trigger_reason=trigger_reason,
                order_id=order_id,
                signal_id=signal_id,
            ))

            # 更新 entry
            new_remaining = entry.remaining_qty - close_qty
            new_status = "closed" if new_remaining <= 1e-12 else "open"
            data_store.update_entry(entry.id, new_remaining, new_status)

            remaining_to_close -= close_qty

        if remaining_to_close > 1e-12:
            logger.warning(
                "[归因] 平仓数量 %.4f 超过 entries 剩余数量，未归因 %.4f",
                total_close_qty, remaining_to_close,
            )
