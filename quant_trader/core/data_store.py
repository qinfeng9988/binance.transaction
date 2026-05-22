"""PostgreSQL 数据访问层。

管理数据库连接，提供 K 线、订单、信号、持仓的存取方法。
"""

import logging
from dataclasses import dataclass
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


# ============================================================
# 数据模型
# ============================================================


@dataclass
class KlineRecord:
    """K 线数据记录。"""
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class OrderRecord:
    """订单记录。"""
    symbol: str
    side: str                       # "buy" 或 "sell"
    price: float
    quantity: float
    leverage: int
    timestamp: datetime
    mode: str                       # "paper" 或 "live"
    order_id: str = ""
    status: str = "pending"
    pnl: float = 0.0


@dataclass
class SignalRecord:
    """交易信号记录。"""
    strategy_name: str
    symbol: str
    timeframe: str
    signal_type: str                # "buy" / "sell" / "hold"
    price: float
    stop_loss: float
    timestamp: datetime
    leverage: int = 1


@dataclass
class PositionRecord:
    """持仓记录。"""
    symbol: str
    side: str
    entry_price: float
    quantity: float
    leverage: int
    entry_time: datetime
    stop_loss: float = 0.0
    exit_price: float = 0.0
    exit_time: datetime | None = None
    pnl: float = 0.0
    status: str = "open"            # "open" 或 "closed"


# ============================================================
# 数据库建表语句
# ============================================================

SCHEMA_SQL = """
-- ============================================================
-- K 线数据表（OHLCV 历史行情）
-- ============================================================
CREATE TABLE IF NOT EXISTS klines (
    id SERIAL PRIMARY KEY,                          -- 自增主键
    symbol VARCHAR(20) NOT NULL,                    -- 交易对标识，如 "BTC/USDT"、"ETH/USDT"
    timeframe VARCHAR(10) NOT NULL,                 -- K 线周期，可选值："1m" | "3m" | "5m" | "15m" | "1h" | "4h" | "1d"
    timestamp TIMESTAMPTZ NOT NULL,                 -- K 线开盘时间（带时区）
    open NUMERIC(20,8) NOT NULL,                    -- 开盘价
    high NUMERIC(20,8) NOT NULL,                    -- 最高价
    low NUMERIC(20,8) NOT NULL,                     -- 最低价
    close NUMERIC(20,8) NOT NULL,                   -- 收盘价
    volume NUMERIC(20,8) NOT NULL,                  -- 成交量（币数量）
    UNIQUE(symbol, timeframe, timestamp)
);

-- K 线表注释
COMMENT ON TABLE klines IS 'K 线行情数据表，存储各币种各周期的 OHLCV 历史数据';
COMMENT ON COLUMN klines.symbol IS '交易对标识，如 "BTC/USDT"、"ETH/USDT"';
COMMENT ON COLUMN klines.timeframe IS 'K 线周期，可选值："1m" | "3m" | "5m" | "15m" | "1h" | "4h" | "1d"';
COMMENT ON COLUMN klines.timestamp IS 'K 线开盘时间（带时区，UTC）';
COMMENT ON COLUMN klines.open IS '开盘价（USDT）';
COMMENT ON COLUMN klines.high IS '最高价（USDT）';
COMMENT ON COLUMN klines.low IS '最低价（USDT）';
COMMENT ON COLUMN klines.close IS '收盘价（USDT）';
COMMENT ON COLUMN klines.volume IS '成交量（基础币数量）';

-- ============================================================
-- 交易信号表（策略产生的买卖信号）
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,                          -- 自增主键
    strategy_name VARCHAR(50) NOT NULL,             -- 策略名称，如 "support_resistance"
    symbol VARCHAR(20) NOT NULL,                    -- 交易对标识，如 "BTC/USDT"
    timeframe VARCHAR(10) NOT NULL,                 -- 产生信号时的 K 线周期
    signal_type VARCHAR(10) NOT NULL,               -- 信号方向，可选值："buy"(做多/买入) | "sell"(做空/卖出) | "hold"(观望)
    price NUMERIC(20,8) NOT NULL,                   -- 触发信号时的价格
    stop_loss NUMERIC(20,8),                        -- 建议止损价
    timestamp TIMESTAMPTZ NOT NULL,                 -- 信号产生时间（带时区）
    leverage INTEGER DEFAULT 1                      -- 建议杠杆倍数，1 表示无杠杆（现货）
);

-- 信号表注释
COMMENT ON TABLE signals IS '策略交易信号表，记录所有策略产生的买卖信号及触发条件';
COMMENT ON COLUMN signals.strategy_name IS '策略名称，对应 strategies/ 目录下的策略类';
COMMENT ON COLUMN signals.signal_type IS '信号方向："buy"=做多/买入, "sell"=做空/卖出, "hold"=观望';
COMMENT ON COLUMN signals.price IS '触发信号时的市场价格（USDT）';
COMMENT ON COLUMN signals.stop_loss IS '建议止损价格，低于此价（做多时）或高于此价（做空时）应平仓';
COMMENT ON COLUMN signals.leverage IS '建议杠杆倍数，1=现货无杠杆, 5=5倍杠杆, 以此类推';

-- ============================================================
-- 订单记录表（模拟/真实下单记录）
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,                          -- 自增主键
    symbol VARCHAR(20) NOT NULL,                    -- 交易对标识
    side VARCHAR(10) NOT NULL,                      -- 交易方向，可选值："buy"(买入/做多) | "sell"(卖出/做空)
    price NUMERIC(20,8) NOT NULL,                   -- 成交价格
    quantity NUMERIC(20,8) NOT NULL,                -- 成交数量（币数量）
    leverage INTEGER DEFAULT 1,                     -- 杠杆倍数
    timestamp TIMESTAMPTZ NOT NULL,                 -- 下单时间（带时区）
    mode VARCHAR(20) NOT NULL,                      -- 交易模式，可选值："paper"(模拟交易) | "live_confirm"(实盘人工确认) | "live_auto"(实盘全自动)
    order_id VARCHAR(50) DEFAULT '',               -- 订单编号，模拟单以 "PAPER-" 前缀，真实单为交易所返回的 ID
    status VARCHAR(20) DEFAULT 'pending',          -- 订单状态，可选值："pending"(待成交) | "filled"(已成交) | "cancelled"(已撤单) | "rejected"(被拒绝)
    pnl NUMERIC(20,8) DEFAULT 0                    -- 盈亏金额（平仓后更新，正=盈利，负=亏损）
);

-- 订单表注释
COMMENT ON TABLE orders IS '交易订单记录表，包含模拟和真实下单的完整流水';
COMMENT ON COLUMN orders.side IS '交易方向："buy"=买入/做多, "sell"=卖出/做空';
COMMENT ON COLUMN orders.price IS '实际成交价格（USDT）';
COMMENT ON COLUMN orders.quantity IS '实际成交数量（基础币数量）';
COMMENT ON COLUMN orders.leverage IS '实际使用的杠杆倍数';
COMMENT ON COLUMN orders.mode IS '交易模式："paper"=模拟交易(不下真实订单), "live_confirm"=实盘需人工确认, "live_auto"=实盘全自动';
COMMENT ON COLUMN orders.order_id IS '订单编号，模拟单格式 "PAPER-1"，真实单为 Binance 返回的 orderId';
COMMENT ON COLUMN orders.status IS '订单状态："pending"=待成交, "filled"=已成交, "cancelled"=已撤单, "rejected"=被风控拒绝';
COMMENT ON COLUMN orders.pnl IS '该笔订单的盈亏（USDT），仅平仓后有意义，正数=盈利，负数=亏损';

-- ============================================================
-- 持仓记录表（当前及历史持仓）
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,                          -- 自增主键
    symbol VARCHAR(20) NOT NULL,                    -- 交易对标识
    side VARCHAR(10) NOT NULL,                      -- 持仓方向，可选值："buy"(做多/多单) | "sell"(做空/空单)
    entry_price NUMERIC(20,8) NOT NULL,             -- 开仓价格
    quantity NUMERIC(20,8) NOT NULL,                -- 持仓数量（币数量）
    leverage INTEGER DEFAULT 1,                     -- 杠杆倍数
    entry_time TIMESTAMPTZ NOT NULL,                -- 开仓时间（带时区）
    stop_loss NUMERIC(20,8) DEFAULT 0,             -- 止损价，做多时低于此价止损，做空时高于此价止损
    exit_price NUMERIC(20,8) DEFAULT 0,            -- 平仓价格（未平仓时为 0）
    exit_time TIMESTAMPTZ,                          -- 平仓时间（未平仓时为 NULL）
    pnl NUMERIC(20,8) DEFAULT 0,                   -- 盈亏金额（正=盈利，负=亏损，未平仓时为 0）
    status VARCHAR(10) DEFAULT 'open'              -- 持仓状态，可选值："open"(持仓中) | "closed"(已平仓) | "stop_loss"(止损平仓) | "take_profit"(止盈平仓)
);

-- 持仓表注释
COMMENT ON TABLE positions IS '交易持仓记录表，追踪所有开仓及平仓记录';
COMMENT ON COLUMN positions.side IS '持仓方向："buy"=做多(多单), "sell"=做空(空单)';
COMMENT ON COLUMN positions.entry_price IS '开仓成交价格（USDT）';
COMMENT ON COLUMN positions.quantity IS '持仓数量（基础币数量）';
COMMENT ON COLUMN positions.leverage IS '开仓时使用的杠杆倍数';
COMMENT ON COLUMN positions.stop_loss IS '止损触发价，价格触及此价时应自动平仓';
COMMENT ON COLUMN positions.exit_price IS '平仓成交价格（USDT），未平仓时为 0';
COMMENT ON COLUMN positions.exit_time IS '平仓时间，未平仓时为 NULL';
COMMENT ON COLUMN positions.pnl IS '该笔持仓的盈亏（USDT），平仓后更新';
COMMENT ON COLUMN positions.status IS '持仓状态："open"=持仓中, "closed"=手动平仓, "stop_loss"=触发止损平仓, "take_profit"=触发止盈平仓';

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_klines_symbol_tf ON klines(symbol, timeframe, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(status) WHERE status = 'open';
"""


class DataStore:
    """PostgreSQL 数据访问层。"""

    def __init__(self, host: str, port: int, dbname: str, user: str, password: str):
        self._conn_params = {
            "host": host,
            "port": port,
            "dbname": dbname,
            "user": user,
            "password": password,
        }
        self._conn: psycopg2.extensions.connection | None = None

    def connect(self):
        """建立数据库连接并初始化表结构。"""
        self._conn = psycopg2.connect(**self._conn_params)
        self._conn.autocommit = True
        self._init_schema()
        logger.info("已连接数据库: %s", self._conn_params["dbname"])

    def close(self):
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            logger.info("数据库连接已关闭")

    def _init_schema(self):
        """创建表和索引（如不存在）。"""
        with self._conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
        logger.debug("数据库表结构已初始化")

    def _get_conn(self) -> psycopg2.extensions.connection:
        """获取连接，如已断开则自动重连。"""
        if not self._conn or self._conn.closed:
            self.connect()
        return self._conn

    # ==================== K 线数据 ====================

    def insert_klines(self, klines: list[KlineRecord]) -> int:
        """批量写入 K 线数据，重复记录自动跳过。"""
        if not klines:
            return 0
        rows = [
            (k.symbol, k.timeframe, k.timestamp, k.open, k.high, k.low, k.close, k.volume)
            for k in klines
        ]
        with self._get_conn().cursor() as cur:
            execute_values(
                cur,
                """INSERT INTO klines (symbol, timeframe, timestamp, open, high, low, close, volume)
                   VALUES %s
                   ON CONFLICT (symbol, timeframe, timestamp) DO NOTHING""",
                rows,
            )
        logger.debug("写入 %d 条 K 线数据", len(klines))
        return len(rows)

    def get_klines(self, symbol: str, timeframe: str, limit: int = 500) -> list[dict]:
        """获取最近的 K 线数据。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT timestamp, open, high, low, close, volume
                   FROM klines
                   WHERE symbol = %s AND timeframe = %s
                   ORDER BY timestamp DESC
                   LIMIT %s""",
                (symbol, timeframe, limit),
            )
            rows = cur.fetchall()
        return [
            {"timestamp": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows
        ]

    def get_latest_timestamp(self, symbol: str, timeframe: str) -> datetime | None:
        """获取某币种某周期的最新时间戳，用于增量拉取。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT MAX(timestamp) FROM klines WHERE symbol = %s AND timeframe = %s""",
                (symbol, timeframe),
            )
            result = cur.fetchone()
        return result[0] if result and result[0] else None

    # ==================== 信号记录 ====================

    def insert_signal(self, signal: SignalRecord):
        """记录策略产生的交易信号。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO signals (strategy_name, symbol, timeframe, signal_type,
                                        price, stop_loss, timestamp, leverage)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (signal.strategy_name, signal.symbol, signal.timeframe,
                 signal.signal_type, signal.price, signal.stop_loss,
                 signal.timestamp, signal.leverage),
            )

    # ==================== 订单记录 ====================

    def insert_order(self, order: OrderRecord) -> int:
        """记录一笔订单。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO orders (symbol, side, price, quantity, leverage,
                                       timestamp, mode, order_id, status, pnl)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (order.symbol, order.side, order.price, order.quantity,
                 order.leverage, order.timestamp, order.mode, order.order_id,
                 order.status, order.pnl),
            )
            return cur.fetchone()[0]

    def update_order_status(self, order_id: int, status: str, pnl: float = 0.0):
        """更新订单状态。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                "UPDATE orders SET status = %s, pnl = %s WHERE id = %s",
                (status, pnl, order_id),
            )

    # ==================== 持仓管理 ====================

    def open_position(self, position: PositionRecord) -> int:
        """开仓记录。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO positions (symbol, side, entry_price, quantity, leverage,
                                          entry_time, stop_loss, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (position.symbol, position.side, position.entry_price,
                 position.quantity, position.leverage, position.entry_time,
                 position.stop_loss, position.status),
            )
            return cur.fetchone()[0]

    def close_position(self, position_id: int, exit_price: float, pnl: float):
        """平仓记录。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """UPDATE positions
                   SET exit_price = %s, exit_time = NOW(), pnl = %s, status = 'closed'
                   WHERE id = %s""",
                (exit_price, pnl, position_id),
            )

    def get_open_positions(self) -> list[PositionRecord]:
        """查询所有当前持仓。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT symbol, side, entry_price, quantity, leverage,
                          entry_time, stop_loss, exit_price, exit_time, pnl, status
                   FROM positions WHERE status = 'open'"""
            )
            rows = cur.fetchall()
        return [
            PositionRecord(
                symbol=r[0], side=r[1], entry_price=float(r[2]),
                quantity=float(r[3]), leverage=r[4], entry_time=r[5],
                stop_loss=float(r[6]), exit_price=float(r[7]),
                exit_time=r[8], pnl=float(r[9]), status=r[10],
            )
            for r in rows
        ]

    def get_position_count(self) -> int:
        """当前持仓数量。"""
        with self._get_conn().cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'open'")
            return cur.fetchone()[0]
