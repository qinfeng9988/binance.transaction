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
    """订单记录（模拟/真实下单的成交流水）。"""
    symbol: str
    price: float
    quantity: float                  # 正数=开仓/加仓，负数=减仓/平仓
    leverage: int
    timestamp: datetime
    mode: str                        # "paper" 或 "live"
    position_id: int                 # 关联持仓 ID
    order_type: str = "open"         # "open" / "add" / "close" / "partial_close" / "stop_loss_close" / "take_profit_close"
    fee: float = 0.0                 # 本笔手续费（USDT）
    status: str = "filled"           # "filled" / "rejected" / "pending"
    order_id: str = ""               # 订单编号
    signal_id: int | None = None     # 触发该订单的信号 ID

    def __post_init__(self):
        self.price = float(self.price)
        self.quantity = float(self.quantity)
        self.leverage = int(self.leverage)
        self.fee = float(self.fee)
        self.position_id = int(self.position_id)
        if self.signal_id is not None:
            self.signal_id = int(self.signal_id)


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

    def __post_init__(self):
        self.price = float(self.price)
        self.stop_loss = float(self.stop_loss)
        self.leverage = int(self.leverage)


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
    pnl: float = 0.0                 # 累计已实现盈亏（USDT）
    status: str = "open"             # "open" 或 "closed"
    closed_quantity: float = 0.0     # 累计已平仓数量
    close_count: int = 0             # 平仓次数
    partial_tp_triggered: str = ""   # 已触发的部分止盈级别（逗号分隔）
    id: int | None = None            # 数据库中的主键 ID

    def __post_init__(self):
        self.entry_price = float(self.entry_price)
        self.quantity = float(self.quantity)
        self.leverage = int(self.leverage)
        self.stop_loss = float(self.stop_loss)
        self.exit_price = float(self.exit_price)
        self.pnl = float(self.pnl)
        self.closed_quantity = float(self.closed_quantity)
        self.close_count = int(self.close_count)
        if self.id is not None:
            self.id = int(self.id)


@dataclass
class EntryRecord:
    """入场明细记录。"""
    position_id: int
    seq: int
    orig_qty: float
    remaining_qty: float
    entry_price: float
    signal_id: int | None = None
    status: str = "open"
    id: int | None = None

    def __post_init__(self):
        self.position_id = int(self.position_id)
        self.seq = int(self.seq)
        self.orig_qty = float(self.orig_qty)
        self.remaining_qty = float(self.remaining_qty)
        self.entry_price = float(self.entry_price)
        if self.signal_id is not None:
            self.signal_id = int(self.signal_id)
        if self.id is not None:
            self.id = int(self.id)


@dataclass
class ClosureRecord:
    """出场明细记录（归因）。"""
    entry_id: int
    close_qty: float
    entry_price: float
    exit_price: float
    pnl: float
    fee: float
    reason: str
    signal_id: int | None = None

    def __post_init__(self):
        self.entry_id = int(self.entry_id)
        self.close_qty = float(self.close_qty)
        self.entry_price = float(self.entry_price)
        self.exit_price = float(self.exit_price)
        self.pnl = float(self.pnl)
        self.fee = float(self.fee)


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
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 首次写入时间
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 最后更新时间
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
    leverage INTEGER DEFAULT 1,                     -- 建议杠杆倍数，1 表示无杠杆（现货）
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 首次写入时间
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()  -- 最后更新时间
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
    price NUMERIC(20,8) NOT NULL,                   -- 成交价格
    quantity NUMERIC(20,8) NOT NULL,                -- 成交数量，正数=开仓/加仓，负数=减仓/平仓
    leverage INTEGER DEFAULT 1,                     -- 杠杆倍数
    timestamp TIMESTAMPTZ NOT NULL,                 -- 下单时间（带时区）
    mode VARCHAR(20) NOT NULL,                      -- 交易模式，可选值："paper"(模拟交易) | "live_confirm"(实盘人工确认) | "live_auto"(实盘全自动)
    position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,  -- 关联持仓 ID
    order_type VARCHAR(20) DEFAULT 'open',          -- 订单类型："open"=开仓 | "add"=加仓 | "close"=反向信号全平 | "partial_close"=部分平仓 | "stop_loss_close"=止损平仓 | "take_profit_close"=止盈平仓
    fee NUMERIC(20,8) DEFAULT 0,                   -- 本笔订单手续费（USDT）
    status VARCHAR(20) DEFAULT 'filled',            -- 订单状态："filled"=已成交 | "rejected"=已拒绝 | "pending"=待成交
    order_id VARCHAR(50) DEFAULT '',                -- 订单编号，模拟单以 "PAPER-" 前缀，真实单为交易所返回的 ID
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,  -- 触发该订单的信号
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 首次写入时间
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()  -- 最后更新时间
);

-- 订单表注释
COMMENT ON TABLE orders IS '交易订单记录表，包含模拟和真实下单的成交流水';
COMMENT ON COLUMN orders.price IS '实际成交价格（USDT）';
COMMENT ON COLUMN orders.quantity IS '成交数量，正数表示开仓/加仓，负数表示减仓/平仓（基础币数量）';
COMMENT ON COLUMN orders.leverage IS '实际使用的杠杆倍数';
COMMENT ON COLUMN orders.mode IS '交易模式："paper"=模拟交易(不下真实订单), "live_confirm"=实盘需人工确认, "live_auto"=实盘全自动';
COMMENT ON COLUMN orders.position_id IS '关联的持仓 ID';
COMMENT ON COLUMN orders.order_type IS '订单类型："open"=开仓, "add"=加仓, "close"=反向信号全平, "partial_close"=部分平仓, "stop_loss_close"=止损平仓, "take_profit_close"=止盈平仓';
COMMENT ON COLUMN orders.fee IS '本笔订单手续费（USDT）';
COMMENT ON COLUMN orders.status IS '订单状态："filled"=已成交, "rejected"=已拒绝, "pending"=待成交';
COMMENT ON COLUMN orders.order_id IS '订单编号，模拟单格式 "PAPER-1"，真实单为 Binance 返回的 orderId';

-- ============================================================
-- 持仓记录表（当前及历史持仓）
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,                          -- 自增主键
    symbol VARCHAR(20) NOT NULL,                    -- 交易对标识
    side VARCHAR(10) NOT NULL,                      -- 持仓方向，可选值："buy"(做多/多单) | "sell"(做空/空单)
    entry_price NUMERIC(20,8) NOT NULL,             -- 加权平均入场价格
    quantity NUMERIC(20,8) NOT NULL,                -- 当前剩余持仓数量（币数量）
    leverage INTEGER DEFAULT 1,                     -- 杠杆倍数
    entry_time TIMESTAMPTZ NOT NULL,                -- 开仓时间（带时区）
    stop_loss NUMERIC(20,8) DEFAULT 0,             -- 止损价，做多时低于此价止损，做空时高于此价止损
    exit_price NUMERIC(20,8) DEFAULT 0,            -- 最后一次平仓价格（未平仓时为 0）
    exit_time TIMESTAMPTZ,                          -- 最后一次平仓时间（未平仓时为 NULL）
    pnl NUMERIC(20,8) DEFAULT 0,                   -- 累计已实现盈亏（USDT，正=盈利，负=亏损）
    status VARCHAR(10) DEFAULT 'open',             -- 持仓状态，可选值："open"(持仓中) | "closed"(已全平)
    closed_quantity NUMERIC(20,8) DEFAULT 0,       -- 累计已平仓数量
    close_count INTEGER DEFAULT 0,                  -- 平仓次数
    partial_tp_triggered TEXT DEFAULT '',           -- 已触发的部分止盈级别（逗号分隔，如 "2.0R,3.5R"）
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 首次写入时间
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()  -- 最后更新时间
);

-- 持仓表注释
COMMENT ON TABLE positions IS '交易持仓记录表，一个仓位一条记录，关联 orders 表中的开仓/加仓/平仓订单';
COMMENT ON COLUMN positions.side IS '持仓方向："buy"=做多(多单), "sell"=做空(空单)';
COMMENT ON COLUMN positions.entry_price IS '加权平均入场价格（USDT），加仓时重新计算';
COMMENT ON COLUMN positions.quantity IS '当前剩余持仓数量（基础币数量），加仓增加，平仓减少';
COMMENT ON COLUMN positions.leverage IS '开仓时使用的杠杆倍数';
COMMENT ON COLUMN positions.stop_loss IS '止损触发价，价格触及此价时应自动平仓';
COMMENT ON COLUMN positions.exit_price IS '最后一次平仓价格（USDT），未全平前为 0';
COMMENT ON COLUMN positions.exit_time IS '最后一次平仓时间，未全平前为 NULL';
COMMENT ON COLUMN positions.pnl IS '累计已实现盈亏（USDT），每次部分平仓和全平时累加';
COMMENT ON COLUMN positions.status IS '持仓状态："open"=持仓中, "closed"=已全平';
COMMENT ON COLUMN positions.closed_quantity IS '累计已平仓数量（基础币数量）';
COMMENT ON COLUMN positions.close_count IS '平仓次数（含部分平仓和全平）';
COMMENT ON COLUMN positions.partial_tp_triggered IS '已触发的部分止盈级别，逗号分隔（如 "2.0R,3.5R"），防止同一级别重复触发';

-- ============================================================
-- 入场明细表（每笔开仓/加仓一条记录，用于交易归因）
-- ============================================================
CREATE TABLE IF NOT EXISTS position_entries (
    id SERIAL PRIMARY KEY,
    position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,                           -- 入场顺序（1=首次开仓, 2=第一次加仓, ...）
    orig_qty NUMERIC(20,8) NOT NULL,                -- 原始入场数量（不变）
    remaining_qty NUMERIC(20,8) NOT NULL,            -- 剩余未平数量（每次平仓递减）
    entry_price NUMERIC(20,8) NOT NULL,              -- 本笔入场价格（不变）
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    status VARCHAR(10) DEFAULT 'open',              -- "open" / "closed"
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(position_id, seq)
);

COMMENT ON TABLE position_entries IS '入场明细表，每笔开仓/加仓一条记录，用于交易归因';
COMMENT ON COLUMN position_entries.seq IS '入场顺序，1=首次开仓, 2=第一次加仓, 以此类推';
COMMENT ON COLUMN position_entries.orig_qty IS '原始入场数量（不变）';
COMMENT ON COLUMN position_entries.remaining_qty IS '剩余未平数量，每次平仓递减，归零时 status 变为 closed';
COMMENT ON COLUMN position_entries.signal_id IS '触发本笔入场的信号 ID';

-- ============================================================
-- 出场明细表（每笔平仓按 FIFO 扣减 entries 生成，用于归因）
-- ============================================================
CREATE TABLE IF NOT EXISTS position_entry_closures (
    id SERIAL PRIMARY KEY,
    entry_id INTEGER NOT NULL REFERENCES position_entries(id) ON DELETE CASCADE,
    close_qty NUMERIC(20,8) NOT NULL,               -- 本次平仓数量
    entry_price NUMERIC(20,8) NOT NULL,              -- 入场价（冗余，方便查询）
    exit_price NUMERIC(20,8) NOT NULL,               -- 出场价
    pnl NUMERIC(20,8) NOT NULL,                      -- 本笔归因盈亏（已扣分摊手续费）
    fee NUMERIC(20,8) NOT NULL,                      -- 本笔分摊的手续费
    reason VARCHAR(20) NOT NULL,                     -- "partial_close" / "stop_loss_close" / "take_profit_close" / "close"
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    close_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE position_entry_closures IS '出场明细表，每笔平仓按 FIFO 扣减 entries 生成，同一 entry 可有多条 closure';
COMMENT ON COLUMN position_entry_closures.close_qty IS '本次平仓数量（从对应 entry 中扣减的部分）';
COMMENT ON COLUMN position_entry_closures.pnl IS '本笔归因盈亏（已扣分摊手续费）';
COMMENT ON COLUMN position_entry_closures.fee IS '本笔分摊的手续费，按各 entry 被扣减数量比例分摊';
COMMENT ON COLUMN position_entry_closures.reason IS '出场原因: partial_close=部分平仓, stop_loss_close=止损, take_profit_close=止盈, close=反向信号全平';
COMMENT ON COLUMN position_entry_closures.signal_id IS '触发本次平仓的信号 ID，止损时为 NULL';

-- ============================================================
-- 索引
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_klines_symbol_tf ON klines(symbol, timeframe, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_orders_position ON orders(position_id, timestamp ASC);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(status) WHERE status = 'open';

-- ============================================================
-- 过滤统计表（策略信号拦截原因累计统计，行模式：每 filter_type 一行）
-- ============================================================
CREATE TABLE IF NOT EXISTS filter_stats (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    filter_type VARCHAR(30) NOT NULL,
    rejected_count INTEGER DEFAULT 0,
    checked_count INTEGER DEFAULT 0,
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 首次写入时间
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(), -- 最后更新时间
    UNIQUE(strategy_name, symbol, timeframe, direction, filter_type)
);

CREATE INDEX IF NOT EXISTS idx_filter_stats_lookup ON filter_stats(strategy_name, symbol, timeframe, direction);
CREATE INDEX IF NOT EXISTS idx_entries_position ON position_entries(position_id, seq ASC);
CREATE INDEX IF NOT EXISTS idx_entries_open ON position_entries(status) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_entry_closures_entry ON position_entry_closures(entry_id, close_time ASC);
CREATE INDEX IF NOT EXISTS idx_entry_closures_signal ON position_entry_closures(signal_id);

-- ============================================================
-- 自动刷新 update_time 的通用触发器
-- ============================================================
CREATE OR REPLACE FUNCTION update_update_time_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.update_time = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_klines_update_time') THEN
        CREATE TRIGGER update_klines_update_time
            BEFORE UPDATE ON klines
            FOR EACH ROW EXECUTE FUNCTION update_update_time_column();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_signals_update_time') THEN
        CREATE TRIGGER update_signals_update_time
            BEFORE UPDATE ON signals
            FOR EACH ROW EXECUTE FUNCTION update_update_time_column();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_orders_update_time') THEN
        CREATE TRIGGER update_orders_update_time
            BEFORE UPDATE ON orders
            FOR EACH ROW EXECUTE FUNCTION update_update_time_column();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_positions_update_time') THEN
        CREATE TRIGGER update_positions_update_time
            BEFORE UPDATE ON positions
            FOR EACH ROW EXECUTE FUNCTION update_update_time_column();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_filter_stats_update_time') THEN
        CREATE TRIGGER update_filter_stats_update_time
            BEFORE UPDATE ON filter_stats
            FOR EACH ROW EXECUTE FUNCTION update_update_time_column();
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'update_position_entries_update_time') THEN
        CREATE TRIGGER update_position_entries_update_time
            BEFORE UPDATE ON position_entries
            FOR EACH ROW EXECUTE FUNCTION update_update_time_column();
    END IF;
END $$;
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
        """创建表和索引（如不存在），并为已有数据库执行迁移。"""
        with self._conn.cursor() as cur:
            # 再创建新表和索引
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

    def insert_signal(self, signal: SignalRecord) -> int:
        """记录策略产生的交易信号，返回信号 ID。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO signals (strategy_name, symbol, timeframe, signal_type,
                                        price, stop_loss, timestamp, leverage)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (signal.strategy_name, signal.symbol, signal.timeframe,
                 signal.signal_type, signal.price, signal.stop_loss,
                 signal.timestamp, signal.leverage),
            )
            return cur.fetchone()[0]

    # ==================== 订单记录 ====================

    def insert_order(self, order: OrderRecord) -> int:
        """记录一笔订单，返回订单 ID。

        新流程：先插 positions（status='open'），再插 orders（带 position_id）。
        """
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO orders (symbol, price, quantity, leverage, timestamp,
                                       mode, position_id, order_type, fee, status,
                                       order_id, signal_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (order.symbol, order.price, order.quantity, order.leverage,
                 order.timestamp, order.mode, order.position_id, order.order_type,
                 order.fee, order.status, order.order_id, order.signal_id),
            )
            return cur.fetchone()[0]

    # ==================== 持仓管理 ====================

    def open_position(self, position: PositionRecord) -> int:
        """开仓记录，返回持仓 ID。先插 positions，再插 orders。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO positions (symbol, side, entry_price, quantity, leverage,
                                          entry_time, stop_loss, status,
                                          closed_quantity, close_count, partial_tp_triggered)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (position.symbol, position.side, position.entry_price,
                 position.quantity, position.leverage, position.entry_time,
                 position.stop_loss, position.status,
                 position.closed_quantity, position.close_count,
                 position.partial_tp_triggered),
            )
            return cur.fetchone()[0]

    def update_position(self, symbol: str, quantity: float, entry_price: float,
                        stop_loss: float = 0.0, closed_quantity: float = None,
                        close_count: int = None, pnl: float = None,
                        partial_tp_triggered: str = None):
        """更新持仓的数量、入场价和止损价（用于加仓/减仓/部分平仓场景）。"""
        with self._get_conn().cursor() as cur:
            sets = ["quantity = %s", "entry_price = %s", "stop_loss = %s"]
            params: list = [quantity, entry_price, stop_loss]
            if closed_quantity is not None:
                sets.append("closed_quantity = %s")
                params.append(closed_quantity)
            if close_count is not None:
                sets.append("close_count = %s")
                params.append(close_count)
            if pnl is not None:
                sets.append("pnl = %s")
                params.append(pnl)
            if partial_tp_triggered is not None:
                sets.append("partial_tp_triggered = %s")
                params.append(partial_tp_triggered)
            params.append(symbol)
            cur.execute(
                f"""UPDATE positions
                    SET {', '.join(sets)}
                    WHERE symbol = %s AND status = 'open'""",
                params,
            )

    def close_position(self, position_id: int, exit_price: float,
                       closed_quantity: float, pnl: float, close_count: int):
        """全平记录。更新 quantity=0, exit_price, exit_time, 累加 pnl, 设 status='closed'。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """UPDATE positions
                   SET exit_price = %s, exit_time = NOW(), quantity = 0,
                       closed_quantity = %s, pnl = %s, close_count = %s, status = 'closed'
                   WHERE id = %s""",
                (exit_price, closed_quantity, pnl, close_count, position_id),
            )

    def get_open_positions(self) -> list[PositionRecord]:
        """查询所有当前持仓。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT id, symbol, side, entry_price, quantity, leverage,
                          entry_time, stop_loss, exit_price, exit_time, pnl, status,
                          closed_quantity, close_count, partial_tp_triggered
                   FROM positions WHERE status = 'open'"""
            )
            rows = cur.fetchall()
        return [
            PositionRecord(
                id=r[0],
                symbol=r[1], side=r[2], entry_price=float(r[3]),
                quantity=float(r[4]), leverage=r[5], entry_time=r[6],
                stop_loss=float(r[7]), exit_price=float(r[8]),
                exit_time=r[9], pnl=float(r[10]), status=r[11],
                closed_quantity=float(r[12]), close_count=r[13],
                partial_tp_triggered=r[14] or "",
            )
            for r in rows
        ]

    def get_position_count(self) -> int:
        """当前持仓数量。"""
        with self._get_conn().cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'open'")
            return cur.fetchone()[0]

    # ==================== 过滤统计 ====================

    def upsert_filter_stats(self, strategy_name: str, symbol: str, timeframe: str,
                            direction: str, stats: dict):
        """更新或插入过滤统计（行模式，先 UPDATE 后 INSERT）。"""
        with self._get_conn().cursor() as cur:
            for filter_type, count in stats.items():
                if count == 0:
                    continue
                # 先 UPDATE
                cur.execute(
                    """UPDATE filter_stats
                       SET rejected_count = %s, update_time = NOW()
                       WHERE strategy_name = %s AND symbol = %s
                         AND timeframe = %s AND direction = %s
                         AND filter_type = %s""",
                    (count, strategy_name, symbol, timeframe, direction, filter_type),
                )
                if cur.rowcount == 0:
                    # 无此行，INSERT
                    cur.execute(
                        """INSERT INTO filter_stats
                           (strategy_name, symbol, timeframe, direction, filter_type,
                            rejected_count, checked_count, create_time, update_time)
                           VALUES (%s, %s, %s, %s, %s, %s, 0, NOW(), NOW())""",
                        (strategy_name, symbol, timeframe, direction, filter_type, count),
                    )

    def get_filter_stats(self, strategy_name: str | None = None,
                         symbol: str | None = None) -> list[dict]:
        """查询过滤统计。"""
        sql = "SELECT * FROM filter_stats WHERE 1=1"
        params = []
        if strategy_name:
            sql += " AND strategy_name = %s"
            params.append(strategy_name)
        if symbol:
            sql += " AND symbol = %s"
            params.append(symbol)
        sql += " ORDER BY symbol, timeframe, direction, filter_type"
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    # ==================== 入场明细（归因） ====================

    def insert_entry(self, entry: EntryRecord) -> int:
        """写入一条入场明细，返回 entry ID。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO position_entries
                       (position_id, seq, orig_qty, remaining_qty, entry_price,
                        signal_id, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (entry.position_id, entry.seq, entry.orig_qty, entry.remaining_qty,
                 entry.entry_price, entry.signal_id, entry.status),
            )
            return cur.fetchone()[0]

    def update_entry(self, entry_id: int, remaining_qty: float, status: str):
        """更新 entry 的剩余数量和状态。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """UPDATE position_entries
                   SET remaining_qty = %s, status = %s
                   WHERE id = %s""",
                (remaining_qty, status, entry_id),
            )

    def get_open_entries(self, position_id: int) -> list[EntryRecord]:
        """查询某持仓下所有未平 entries（按 seq ASC，FIFO 顺序）。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT id, position_id, seq, orig_qty, remaining_qty,
                          entry_price, signal_id, status
                   FROM position_entries
                   WHERE position_id = %s AND status = 'open'
                   ORDER BY seq ASC""",
                (position_id,),
            )
            rows = cur.fetchall()
        return [
            EntryRecord(
                id=r[0], position_id=r[1], seq=r[2],
                orig_qty=float(r[3]), remaining_qty=float(r[4]),
                entry_price=float(r[5]), signal_id=r[6], status=r[7],
            )
            for r in rows
        ]

    # ==================== 出场明细（归因） ====================

    def insert_closure(self, closure: ClosureRecord) -> int:
        """写入一条出场明细，返回 closure ID。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO position_entry_closures
                       (entry_id, close_qty, entry_price, exit_price,
                        pnl, fee, reason, signal_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (closure.entry_id, closure.close_qty, closure.entry_price,
                 closure.exit_price, closure.pnl, closure.fee,
                 closure.reason, closure.signal_id),
            )
            return cur.fetchone()[0]

    # ==================== 归因查询 ====================

    def get_attribution_by_signal(self, signal_id: int = None) -> list[dict]:
        """按信号归因查询盈亏。signal_id 为 None 时返回所有信号的归因。"""
        sql = """
            SELECT
                s.id AS signal_id,
                s.signal_type,
                s.price AS signal_price,
                s.timestamp AS signal_time,
                pe.entry_price,
                SUM(ec.close_qty) AS total_closed_qty,
                SUM(ec.pnl) AS total_attributed_pnl,
                SUM(ec.fee) AS total_fee
            FROM position_entries pe
            JOIN signals s ON pe.signal_id = s.id
            JOIN position_entry_closures ec ON ec.entry_id = pe.id
            {where_clause}
            GROUP BY s.id, s.signal_type, s.price, s.timestamp, pe.entry_price
            ORDER BY s.timestamp
        """
        if signal_id is not None:
            sql = sql.format(where_clause="WHERE s.id = %s")
            params = [signal_id]
        else:
            sql = sql.format(where_clause="")
            params = []
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_attribution_by_position(self, position_id: int = None) -> list[dict]:
        """按持仓归因查询明细。position_id 为 None 时返回所有持仓的归因。"""
        sql = """
            SELECT
                p.id AS position_id,
                p.symbol,
                p.side,
                pe.id AS entry_id,
                pe.seq,
                pe.orig_qty,
                pe.entry_price,
                pe.signal_id,
                pe.remaining_qty,
                pe.status AS entry_status,
                SUM(ec.close_qty) AS total_closed_qty,
                SUM(ec.pnl) AS total_attributed_pnl,
                SUM(ec.fee) AS total_fee
            FROM positions p
            JOIN position_entries pe ON pe.position_id = p.id
            LEFT JOIN position_entry_closures ec ON ec.entry_id = pe.id
            {where_clause}
            GROUP BY p.id, p.symbol, p.side, pe.id, pe.seq,
                     pe.orig_qty, pe.entry_price, pe.signal_id,
                     pe.remaining_qty, pe.status
            ORDER BY p.id, pe.seq
        """
        if position_id is not None:
            sql = sql.format(where_clause="WHERE p.id = %s")
            params = [position_id]
        else:
            sql = sql.format(where_clause="")
            params = []
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_attribution_by_time_range(self, start_time, end_time) -> list[dict]:
        """按时间段汇总归因盈亏。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT
                    s.id AS signal_id,
                    s.signal_type,
                    s.symbol,
                    s.price AS signal_price,
                    s.timestamp AS signal_time,
                    pe.entry_price,
                    SUM(ec.close_qty) AS total_closed_qty,
                    SUM(ec.pnl) AS total_attributed_pnl,
                    SUM(ec.fee) AS total_fee,
                    MIN(ec.close_time) AS first_close_time,
                    MAX(ec.close_time) AS last_close_time
                FROM position_entries pe
                JOIN signals s ON pe.signal_id = s.id
                JOIN position_entry_closures ec ON ec.entry_id = pe.id
                WHERE ec.close_time >= %s AND ec.close_time <= %s
                GROUP BY s.id, s.signal_type, s.symbol, s.price,
                         s.timestamp, pe.entry_price
                ORDER BY s.timestamp""",
                (start_time, end_time),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
