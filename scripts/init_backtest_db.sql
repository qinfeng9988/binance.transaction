-- ============================================================
-- 回测数据库初始化脚本
-- ============================================================
-- 用法：
--   1. 先由管理员创建 db_backtest database 并授权：
--        CREATE DATABASE db_backtest;
--        GRANT ALL PRIVILEGES ON DATABASE db_backtest TO <app_user>;
--   2. 连接到 db_backtest 后执行本脚本：
--        \c db_backtest
--        \i scripts/init_backtest_db.sql
--
-- 与生产 DB (db_quant_trader) 的差异：
--   - 交易相关表（positions/orders/signals/position_entries/
--     position_entry_closures/filter_stats）均新增 run_id 字段
--   - filter_stats 唯一键含 run_id，允许多组参数实验共存
--   - klines 表与生产一致，跨 run_id 共享复用
--   - run_id 索引覆盖所有需要按 run_id 过滤的查询
-- ============================================================

-- ============================================================
-- K 线数据表（与生产一致，跨 run_id 共享）
-- ============================================================
CREATE TABLE IF NOT EXISTS klines (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    open NUMERIC(20,8) NOT NULL,
    high NUMERIC(20,8) NOT NULL,
    low NUMERIC(20,8) NOT NULL,
    close NUMERIC(20,8) NOT NULL,
    volume NUMERIC(20,8) NOT NULL,
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(symbol, timeframe, timestamp)
);

COMMENT ON TABLE klines IS 'K 线行情数据表，回测 DB 中跨 run_id 共享复用';
COMMENT ON COLUMN klines.symbol IS '交易对标识，如 "BTC/USDT"';
COMMENT ON COLUMN klines.timeframe IS 'K 线周期，可选值："1m" | "3m" | "5m" | "15m" | "1h" | "4h" | "1d"';
COMMENT ON COLUMN klines.timestamp IS 'K 线开盘时间（带时区，UTC）';

-- ============================================================
-- 交易信号表（带 run_id）
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    signal_type VARCHAR(10) NOT NULL,
    price NUMERIC(20,8) NOT NULL,
    stop_loss NUMERIC(20,8),
    timestamp TIMESTAMPTZ NOT NULL,
    leverage INTEGER DEFAULT 1,
    quality_score NUMERIC(10,2),
    adx NUMERIC(10,2),
    plus_di NUMERIC(10,2),
    minus_di NUMERIC(10,2),
    rsi NUMERIC(10,2),
    volume_ratio NUMERIC(10,2),
    parent_trend VARCHAR(20),
    direction_type VARCHAR(20),
    is_counter_trend BOOLEAN,
    atr NUMERIC(20,8),
    entry_threshold_pct NUMERIC(10,4),
    distance_pct NUMERIC(10,4),
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE signals IS '策略交易信号表（回测），按 run_id 隔离';
COMMENT ON COLUMN signals.run_id IS '回测运行 ID，格式 bt_{timestamp}_{symbol}_{timeframe}_{param_hash8}';

-- ============================================================
-- 订单记录表（带 run_id）
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    price NUMERIC(20,8) NOT NULL,
    quantity NUMERIC(20,8) NOT NULL,
    leverage INTEGER DEFAULT 1,
    timestamp TIMESTAMPTZ NOT NULL,
    mode VARCHAR(20) NOT NULL,
    fee NUMERIC(20,8) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'filled',
    order_id VARCHAR(50) DEFAULT '',
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE orders IS '交易订单记录表（回测），按 run_id 隔离';
COMMENT ON COLUMN orders.run_id IS '回测运行 ID';

-- ============================================================
-- 持仓记录表（带 run_id）
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    entry_price NUMERIC(20,8) NOT NULL,
    quantity NUMERIC(20,8) NOT NULL,
    leverage INTEGER DEFAULT 1,
    entry_time TIMESTAMPTZ NOT NULL,
    stop_loss NUMERIC(20,8) DEFAULT 0,
    exit_price NUMERIC(20,8) DEFAULT 0,
    exit_time TIMESTAMPTZ,
    pnl NUMERIC(20,8) DEFAULT 0,
    status VARCHAR(10) DEFAULT 'open',
    closed_quantity NUMERIC(20,8) DEFAULT 0,
    close_count INTEGER DEFAULT 0,
    total_fee NUMERIC(20,8) DEFAULT 0,
    partial_tp_triggered TEXT DEFAULT '',
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE positions IS '交易持仓记录表（回测），按 run_id 隔离';
COMMENT ON COLUMN positions.run_id IS '回测运行 ID';

-- ============================================================
-- 入场明细表（带 run_id）
-- ============================================================
CREATE TABLE IF NOT EXISTS position_entries (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    orig_qty NUMERIC(20,8) NOT NULL,
    remaining_qty NUMERIC(20,8) NOT NULL,
    entry_price NUMERIC(20,8) NOT NULL,
    entry_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entry_fee NUMERIC(20,8) NOT NULL DEFAULT 0,
    order_id VARCHAR(50),
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    status VARCHAR(10) DEFAULT 'open',
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(position_id, seq)
);

COMMENT ON TABLE position_entries IS '入场明细表（回测），按 run_id 隔离';
COMMENT ON COLUMN position_entries.run_id IS '回测运行 ID';

-- ============================================================
-- 出场明细表（带 run_id）
-- ============================================================
CREATE TABLE IF NOT EXISTS position_entry_closures (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    entry_id INTEGER NOT NULL REFERENCES position_entries(id) ON DELETE CASCADE,
    close_qty NUMERIC(20,8) NOT NULL,
    entry_price NUMERIC(20,8) NOT NULL,
    exit_price NUMERIC(20,8) NOT NULL,
    pnl NUMERIC(20,8) NOT NULL,
    fee NUMERIC(20,8) NOT NULL,
    reason VARCHAR(20) NOT NULL,                     -- 事件类型: "CLOSE_PARTIAL" / "CLOSE_FULL" / "STOP_LOSS" / "END_OF_DATA"
    trigger_reason VARCHAR(40),                      -- 策略触发原因: "2.0R" / "ema_cross_down" / "reverse_signal" / "stop_loss" 等
    order_id VARCHAR(50),
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    close_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE position_entry_closures IS '出场明细表（回测），按 run_id 隔离，每笔平仓按 FIFO 扣减 entries 生成';
COMMENT ON COLUMN position_entry_closures.run_id IS '回测运行 ID';
COMMENT ON COLUMN position_entry_closures.reason IS '事件类型（机械事实）: CLOSE_PARTIAL=部分平仓, CLOSE_FULL=信号全平, STOP_LOSS=止损触发, END_OF_DATA=回测结束强平';
COMMENT ON COLUMN position_entry_closures.trigger_reason IS '策略触发原因（业务语义）: 2.0R/3.5R=止盈级别, ema_cross_down 等=策略close信号, reverse_signal=反向信号, stop_loss=止损, end_of_backtest=回测结束';

-- ============================================================
-- 过滤统计表（带 run_id，唯一键含 run_id）
-- ============================================================
CREATE TABLE IF NOT EXISTS filter_stats (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    timeframe VARCHAR(10) NOT NULL,
    direction VARCHAR(10) NOT NULL,
    filter_type VARCHAR(30) NOT NULL,
    rejected_count INTEGER DEFAULT 0,
    checked_count INTEGER DEFAULT 0,
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, strategy_name, symbol, timeframe, direction, filter_type)
);

COMMENT ON TABLE filter_stats IS '过滤统计表（回测），唯一键含 run_id 支持多组实验共存';
COMMENT ON COLUMN filter_stats.run_id IS '回测运行 ID';

-- ============================================================
-- 索引（含 run_id 前缀索引以支持多 run_id 隔离查询）
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_klines_symbol_tf ON klines(symbol, timeframe, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_signals_run_id ON signals(run_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_orders_run_id ON orders(run_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(run_id, status) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_filter_stats_lookup ON filter_stats(run_id, strategy_name, symbol, timeframe, direction);
CREATE INDEX IF NOT EXISTS idx_entries_position ON position_entries(position_id, seq ASC);
CREATE INDEX IF NOT EXISTS idx_entries_open_run ON position_entries(run_id, status) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_entries_order ON position_entries(order_id);
CREATE INDEX IF NOT EXISTS idx_entry_closures_entry ON position_entry_closures(entry_id, close_time ASC);
CREATE INDEX IF NOT EXISTS idx_entry_closures_signal ON position_entry_closures(signal_id);
CREATE INDEX IF NOT EXISTS idx_entry_closures_run_id ON position_entry_closures(run_id, close_time);
CREATE INDEX IF NOT EXISTS idx_closures_order ON position_entry_closures(order_id);
CREATE INDEX IF NOT EXISTS idx_closures_reason ON position_entry_closures(run_id, reason);

-- ============================================================
-- 自动刷新 update_time 触发器
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
