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

logger = logging.getLogger(__name__)


@dataclass
class MockOrder:
    """模拟订单。"""
    order_id: str
    symbol: str
    side: str
    amount: float
    price: float
    order_type: str
    leverage: int = 1
    status: str = "filled"       # 模拟单直接成交
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    fee: float = 0.0


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
                    metadata: dict | None = None) -> MockOrder | None:
        """模拟下单。

        模拟模式下市价单立即成交，限价单挂入等待成交。

        参数:
            symbol: 交易对
            side: "buy" 或 "sell"
            amount: 数量（合约时为张数/合约数）
            price: 成交价格
            leverage: 杠杆倍数
            stop_loss: 止损价

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
            order_type="market",
            leverage=leverage,
            status="filled",
            fee=fee,
        )
        self.orders.append(order)

        # 扣除手续费和保证金
        if self.default_type == "swap":
            required_margin = (price * amount) / leverage
            self.balance -= (required_margin + fee)
        else:
            self.balance -= (price * amount + fee)

        # 更新或创建持仓
        self._update_position(order, stop_loss, data_store, metadata=metadata)

        logger.info(
            "[模拟] %s %s %s 价格=%.2f 数量=%.4f 杠杆=%dx 手续费=%.2f",
            side.upper(), symbol, order.order_id, price, amount, leverage, fee,
        )
        return order

    def restore_from_db(self, data_store) -> None:
        """从数据库恢复持仓和余额状态。

        程序重启时，从 positions 表加载未平仓记录，重建持仓对象，
        从 orders 表重建订单计数器和余额。

        参数:
            data_store: DataStore 实例
        """
        # 1. 加载所有历史订单，重建 order_counter 和余额
        with data_store._get_conn().cursor() as cur:
            cur.execute(
                "SELECT id FROM orders WHERE mode = 'paper' ORDER BY id"
            )
            order_rows = cur.fetchall()
            if order_rows:
                self._order_counter = order_rows[-1][0]  # 从最大 ID 继续

        # 2. 加载已平仓记录，用于统计
        with data_store._get_conn().cursor() as cur:
            cur.execute(
                """SELECT symbol, side, entry_price, quantity, leverage,
                          entry_time, exit_price, exit_time, pnl
                   FROM positions WHERE status = 'closed'"""
            )
            closed_rows = cur.fetchall()
            for r in closed_rows:
                self.trades_closed.append({
                    "symbol": r[0], "side": r[1],
                    "entry_price": float(r[2]), "quantity": float(r[3]),
                    "leverage": r[4], "entry_time": r[5],
                    "exit_price": float(r[6]), "exit_time": r[7],
                    "pnl": float(r[8]),
                    "fee": 0.0,  # 手续费从 orders 表推算
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
            )
            self.positions[pos.symbol] = mock_pos

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

    def sync_position_to_db(self, data_store, symbol: str, exit_price: float, pnl: float):
        """将平仓结果同步到数据库。

        参数:
            data_store: DataStore 实例
            symbol: 交易对
            exit_price: 平仓价格
            pnl: 实现的盈亏
        """
        with data_store._get_conn().cursor() as cur:
            cur.execute(
                """SELECT id FROM positions
                   WHERE symbol = %s AND status = 'open'
                   ORDER BY entry_time DESC LIMIT 1""",
                (symbol,)
            )
            row = cur.fetchone()
            if row:
                cur.execute(
                    """UPDATE positions
                       SET exit_price = %s, exit_time = NOW(), pnl = %s, status = 'closed'
                       WHERE id = %s""",
                    (exit_price, pnl, row[0])
                )

    def close_position(self, symbol: str, exit_price: float, data_store=None) -> float | None:
        """平仓。

        参数:
            symbol: 交易对
            exit_price: 平仓价格

        返回:
            实现的盈亏，无持仓则返回 None
        """
        if symbol not in self.positions:
            logger.warning("[模拟] 无持仓，无法平仓: %s", symbol)
            return None

        pos = self.positions[symbol]
        entry_price = pos.entry_price
        quantity = pos.quantity
        leverage = pos.leverage

        # 计算盈亏
        if pos.side == "buy":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity

        # 合约需要乘杠杆
        if self.default_type == "swap":
            pnl = pnl  # 盈亏已在数量中体现

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
        })

        # 移除持仓
        del self.positions[symbol]

        logger.info(
            "[模拟] 平仓 %s %s 入场=%.2f 出场=%.2f 盈亏=%.2f(净) 手续费=%.2f",
            symbol, pos.side, entry_price, exit_price, net_pnl, fee,
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
                pnl = self.close_position(symbol, current_price, data_store)
                if pnl is not None and data_store:
                    self.sync_position_to_db(data_store, symbol, current_price, pnl)

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
                                close_ratio: float, data_store=None) -> float | None:
        """部分平仓指定比例的仓位。

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
        if remaining <= 1e-12:
            del self.positions[symbol]
        else:
            pos.quantity = remaining

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
        })

        logger.info(
            "[模拟] 部分平仓 %s %s 数量=%.4f 剩余=%.4f 入场=%.2f 出场=%.2f 盈亏=%.2f 手续费=%.2f",
            symbol, side, close_qty, remaining, entry, close_price, net_pnl, fee,
        )
        return net_pnl

    def _update_position(self, order: MockOrder, stop_loss: float, data_store=None,
                         metadata: dict | None = None):
        """更新持仓（开仓或加仓或平仓）。

        如果下单方向与现有持仓相反，则执行平仓。
        """
        if order.symbol in self.positions:
            existing = self.positions[order.symbol]
            # 反向单 = 平仓
            if order.side != existing.side:
                # 全部平仓（当前实现为全部平仓）
                pnl = self.close_position(order.symbol, order.price, data_store)
                if pnl is not None and data_store:
                    self.sync_position_to_db(data_store, order.symbol, order.price, pnl)
                return
            # 同向单 = 加仓：检查最大加仓次数
            max_additions = metadata.get("max_additions", 1) if metadata else 1
            if existing.add_count >= max_additions:
                logger.info(
                    "[模拟] 忽略 %s %s 同向加仓: 已达最大次数 %d/%d",
                    order.symbol, order.side, existing.add_count, max_additions,
                )
                return
            # 同向单 = 加仓：计算加权平均价
            total_qty = existing.quantity + order.amount
            avg_price = (existing.entry_price * existing.quantity + order.price * order.amount) / total_qty
            existing.entry_price = avg_price
            existing.quantity = total_qty
            existing.add_count += 1
            # 同步到数据库
            if data_store:
                data_store.update_position(
                    symbol=order.symbol,
                    quantity=total_qty,
                    entry_price=avg_price,
                    stop_loss=stop_loss,
                )
        else:
            self.positions[order.symbol] = MockPosition(
                symbol=order.symbol,
                side=order.side,
                entry_price=order.price,
                quantity=order.amount,
                leverage=order.leverage,
                current_price=order.price,
                stop_loss=stop_loss,
            )
