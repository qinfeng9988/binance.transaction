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
            # 先迁移旧表结构（如果是已有数据库）
            self._migrate_orders_positions_v2(cur)
            # 再创建新表和索引
            cur.execute(SCHEMA_SQL)
            self._migrate_timestamp_columns(cur)
        logger.debug("数据库表结构已初始化")

    def _migrate_orders_positions_v2(self, cur):
        """V2 迁移：重构 orders/positions 表结构。

        positions 表：新增 closed_quantity、close_count；删除 open_order_id。
        orders 表：删除 side；新增 position_id、order_type、fee、status。
        按确认点 4 清空两张表历史数据（klines/signals/filter_stats 不受影响）。

        如果是全新数据库（表不存在），跳过迁移。
        如果已经迁移过（存在 closed_quantity 列），跳过迁移。
        """
        # 检查 orders/positions 表是否已存在
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_name IN ('orders', 'positions')
        """)
        table_count = cur.fetchone()[0]
        if table_count < 2:
            # 全新数据库，不需要迁移
            return

        # 检查是否已经迁移过（positions 表是否有 closed_quantity 列）
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'positions' AND column_name = 'closed_quantity'
        """)
        if cur.fetchone():
            # 已经迁移过，跳过
            logger.debug("V2 迁移已执行过，跳过")
            return

        logger.info("检测到旧表结构，开始 V2 迁移...")

        # 1. 清空 orders（被 positions 的 ON DELETE CASCADE 约束，先清）
        cur.execute("TRUNCATE TABLE orders CASCADE")
        cur.execute("TRUNCATE TABLE positions CASCADE")

        # 2. 删除 orders 表的 position_id 外键约束（如存在，因为旧表可能有）
        # 先删除旧的外键约束，然后再重建
        cur.execute("""
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'orders'::regclass AND contype = 'f'
        """)
        for row in cur.fetchall():
            cur.execute(f"ALTER TABLE orders DROP CONSTRAINT IF EXISTS {row[0]}")

        # 3. positions 表新增列
        cur.execute("""
            ALTER TABLE positions
                ADD COLUMN IF NOT EXISTS closed_quantity NUMERIC(20,8) DEFAULT 0,
                ADD COLUMN IF NOT EXISTS close_count INTEGER DEFAULT 0
        """)
        # 4. positions 表删除 open_order_id（如存在）
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'positions' AND column_name = 'open_order_id'
        """)
        if cur.fetchone():
            cur.execute("ALTER TABLE positions DROP COLUMN open_order_id")

        # 5. orders 表删除 side（如存在）
        cur.execute("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'orders' AND column_name = 'side'
        """)
        if cur.fetchone():
            cur.execute("ALTER TABLE orders DROP COLUMN side")

        # 6. orders 表新增列
        cur.execute("""
            ALTER TABLE orders
                ADD COLUMN IF NOT EXISTS position_id INTEGER,
                ADD COLUMN IF NOT EXISTS order_type VARCHAR(20) DEFAULT 'open',
                ADD COLUMN IF NOT EXISTS fee NUMERIC(20,8) DEFAULT 0,
                ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'filled'
        """)

        # 7. 添加 position_id 外键约束
        cur.execute("""
            ALTER TABLE orders
                ADD CONSTRAINT fk_orders_position
                FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
        """)

        # 8. 补齐索引
        cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_position ON orders(position_id, timestamp ASC)")

        # 9. 重建注释
        cur.execute("COMMENT ON TABLE positions IS '交易持仓记录表，一个仓位一条记录，关联 orders 表中的开仓/加仓/平仓订单'")
        cur.execute("COMMENT ON COLUMN positions.quantity IS '当前剩余持仓数量（基础币数量），加仓增加，平仓减少'")
        cur.execute("COMMENT ON COLUMN positions.entry_price IS '加权平均入场价格（USDT），加仓时重新计算'")
        cur.execute("COMMENT ON COLUMN positions.pnl IS '累计已实现盈亏（USDT），每次部分平仓和全平时累加'")
        cur.execute("COMMENT ON TABLE orders IS '交易订单记录表，包含模拟和真实下单的成交流水'")
        cur.execute("COMMENT ON COLUMN orders.quantity IS '成交数量，正数表示开仓/加仓，负数表示减仓/平仓（基础币数量）'")

        logger.info("orders/positions 表 V2 迁移完成（已清空历史数据）")

    def _migrate_timestamp_columns(self, cur):
        """为已有表增加 create_time/update_time 字段并回填历史数据。"""
        # 为各表增加 create_time / update_time 列（如不存在）
        for table, time_col in [
            ("klines", "timestamp"),
            ("signals", "timestamp"),
            ("orders", "timestamp"),
            ("positions", "entry_time"),
        ]:
            # 先以 nullable 形式增加列，便于回填历史数据
            cur.execute(
                f"""ALTER TABLE {table}
                    ADD COLUMN IF NOT EXISTS create_time TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS update_time TIMESTAMPTZ"""
            )
            # 回填已有数据的 create_time/update_time，使用业务时间戳作为近似值。
            # 无条件覆盖，避免之前失败的迁移留下全为 DEFAULT NOW() 的值。
            cur.execute(
                f"""UPDATE {table}
                    SET create_time = {time_col}, update_time = {time_col}
                    WHERE {time_col} IS NOT NULL"""
            )
            # 回填后设置为 NOT NULL DEFAULT NOW()
            cur.execute(
                f"""ALTER TABLE {table}
                    ALTER COLUMN create_time SET NOT NULL,
                    ALTER COLUMN create_time SET DEFAULT NOW(),
                    ALTER COLUMN update_time SET NOT NULL,
                    ALTER COLUMN update_time SET DEFAULT NOW()"""
            )

        # filter_stats 表已有 updated_at，需要重命名为 update_time 并新增 create_time
        cur.execute(
            """ALTER TABLE filter_stats
               ADD COLUMN IF NOT EXISTS create_time TIMESTAMPTZ,
               ADD COLUMN IF NOT EXISTS update_time TIMESTAMPTZ"""
        )
        # 如果存在旧的 updated_at 列，将数据迁移到 update_time 后删除旧列
        cur.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_name = 'filter_stats' AND column_name = 'updated_at'"""
        )
        if cur.fetchone():
            cur.execute(
                """UPDATE filter_stats
                   SET update_time = updated_at, create_time = updated_at"""
            )
            cur.execute("""ALTER TABLE filter_stats DROP COLUMN updated_at""")
        # 回填后设置为 NOT NULL DEFAULT NOW()
        cur.execute(
            """ALTER TABLE filter_stats
                ALTER COLUMN create_time SET NOT NULL,
                ALTER COLUMN create_time SET DEFAULT NOW(),
                ALTER COLUMN update_time SET NOT NULL,
                ALTER COLUMN update_time SET DEFAULT NOW()"""
        )

        # 确保触发器存在
        cur.execute("""
            CREATE OR REPLACE FUNCTION update_update_time_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.update_time = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        for table in ["klines", "signals", "orders", "positions", "filter_stats"]:
            cur.execute(
                f"""DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_trigger
                        WHERE tgname = 'update_{table}_update_time'
                          AND tgrelid = '{table}'::regclass
                    ) THEN
                        CREATE TRIGGER update_{table}_update_time
                            BEFORE UPDATE ON {table}
                            FOR EACH ROW EXECUTE FUNCTION update_update_time_column();
                    END IF;
                END $$;"""
            )
        logger.info("时间戳字段迁移完成")

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
                                          closed_quantity, close_count)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (position.symbol, position.side, position.entry_price,
                 position.quantity, position.leverage, position.entry_time,
                 position.stop_loss, position.status,
                 position.closed_quantity, position.close_count),
            )
            return cur.fetchone()[0]

    def update_position(self, symbol: str, quantity: float, entry_price: float,
                        stop_loss: float = 0.0, closed_quantity: float = None,
                        close_count: int = None, pnl: float = None):
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
                          closed_quantity, close_count
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
