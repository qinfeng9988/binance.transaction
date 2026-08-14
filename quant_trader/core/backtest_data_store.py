"""回测专用数据访问层。

继承 DataStore，通过覆盖方法注入和过滤 run_id，实现多组参数实验数据隔离。
生产代码（DataStore）零侵入。

覆盖的方法分两类：
1. 写入方法（open_position/insert_order/insert_signal/insert_entry/insert_closure/
   upsert_filter_stats）— INSERT 注入 run_id 值
2. 查询方法（update_position/get_open_positions/get_position_count/get_filter_stats/
   所有归因查询）— WHERE 加 run_id 过滤

filter_stats 唯一键在生产为 (strategy,symbol,timeframe,direction,filter_type)，
在回测为 (run_id,strategy,symbol,timeframe,direction,filter_type)。
"""

import logging
from datetime import datetime

from .data_store import (
    DataStore,
    PositionRecord,
    OrderRecord,
    SignalRecord,
    EntryRecord,
    ClosureRecord,
)
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)


class BacktestDataStore(DataStore):
    """回测专用 DataStore，所有写读操作自动注入和过滤 run_id。"""

    def __init__(
        self,
        host: str,
        port: int,
        dbname: str,
        user: str,
        password: str,
        run_id: str,
    ):
        super().__init__(host=host, port=port, dbname=dbname, user=user, password=password)
        if not run_id:
            raise ValueError("run_id 不能为空")
        self.run_id = run_id

    # ============================================================
    # 建表（含 run_id 字段、调整 filter_stats 唯一键）
    # ============================================================

    def _init_schema(self):
        """建表：与生产结构一致，但交易表均含 run_id 字段。"""
        with self._get_conn().cursor() as cur:
            self._run_migrations(cur)
            cur.execute(_BACKTEST_SCHEMA_SQL)
        logger.debug("回测 DB 表结构已初始化")

    def _run_migrations(self, cur):
        """回测 DB 跳过生产迁移逻辑（schema 由 init_backtest_db.sql 创建）。"""
        pass

    # ============================================================
    # 持仓写入：open_position
    # ============================================================

    def open_position(self, position: PositionRecord, total_fee: float = 0.0) -> int:
        """开仓记录，注入 run_id，返回持仓 ID。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO positions (run_id, symbol, side, entry_price, quantity,
                                          leverage, entry_time, stop_loss, status,
                                          closed_quantity, close_count, total_fee,
                                          partial_tp_triggered)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    self.run_id,
                    position.symbol, position.side, position.entry_price,
                    position.quantity, position.leverage, position.entry_time,
                    position.stop_loss, position.status,
                    position.closed_quantity, position.close_count, total_fee,
                    position.partial_tp_triggered,
                ),
            )
            return cur.fetchone()[0]

    # ============================================================
    # 持仓更新/查询：update_position / get_open_positions / get_position_count
    # 关键：WHERE 必须含 run_id，避免多 run_id 共存时污染
    # ============================================================

    def update_position(self, symbol: str, quantity: float, entry_price: float,
                        stop_loss: float = 0.0, closed_quantity: float = None,
                        close_count: int = None, pnl: float = None,
                        total_fee: float = None,
                        partial_tp_triggered: str = None):
        """更新持仓，WHERE 含 run_id 以隔离多组实验。"""
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
            if total_fee is not None:
                sets.append("total_fee = total_fee + %s")
                params.append(total_fee)
            if partial_tp_triggered is not None:
                sets.append("partial_tp_triggered = %s")
                params.append(partial_tp_triggered)
            params.append(symbol)
            params.append(self.run_id)
            cur.execute(
                f"""UPDATE positions
                    SET {', '.join(sets)}
                    WHERE symbol = %s AND status = 'open' AND run_id = %s""",
                params,
            )

    def get_open_positions(self) -> list[PositionRecord]:
        """查询当前 run_id 下所有 open positions。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT id, symbol, side, entry_price, quantity, leverage,
                          entry_time, stop_loss, exit_price, exit_time, pnl, status,
                          closed_quantity, close_count, partial_tp_triggered
                   FROM positions
                   WHERE status = 'open' AND run_id = %s""",
                (self.run_id,),
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
        """统计当前 run_id 的 open positions 数量。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'open' AND run_id = %s",
                (self.run_id,),
            )
            return cur.fetchone()[0]

    # ============================================================
    # 订单/信号写入：insert_order / insert_signal
    # ============================================================

    def insert_order(self, order: OrderRecord) -> int:
        """记录订单，注入 run_id。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO orders (run_id, symbol, price, quantity, leverage,
                                       timestamp, mode, fee, status, order_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    self.run_id,
                    order.symbol, order.price, order.quantity, order.leverage,
                    order.timestamp, order.mode, order.fee, order.status,
                    order.order_id,
                ),
            )
            return cur.fetchone()[0]

    def insert_signal(self, signal: SignalRecord) -> int:
        """记录信号，注入 run_id。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO signals (run_id, strategy_name, symbol, timeframe,
                                        signal_type, price, stop_loss, timestamp, leverage)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    self.run_id,
                    signal.strategy_name, signal.symbol, signal.timeframe,
                    signal.signal_type, signal.price, signal.stop_loss,
                    signal.timestamp, signal.leverage,
                ),
            )
            return cur.fetchone()[0]

    # ============================================================
    # 入场/出场明细写入：insert_entry / insert_closure
    # ============================================================

    def insert_entry(self, entry: EntryRecord) -> int:
        """写入入场明细，注入 run_id。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO position_entries
                       (run_id, position_id, seq, orig_qty, remaining_qty, entry_price,
                        entry_fee, order_id, signal_id, status)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    self.run_id,
                    entry.position_id, entry.seq, entry.orig_qty, entry.remaining_qty,
                    entry.entry_price, entry.entry_fee, entry.order_id,
                    entry.signal_id, entry.status,
                ),
            )
            return cur.fetchone()[0]

    def insert_closure(self, closure: ClosureRecord) -> int:
        """写出场明细，注入 run_id。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """INSERT INTO position_entry_closures
                       (run_id, entry_id, close_qty, entry_price, exit_price,
                        pnl, fee, reason, trigger_reason, order_id, signal_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (
                    self.run_id,
                    closure.entry_id, closure.close_qty, closure.entry_price,
                    closure.exit_price, closure.pnl, closure.fee,
                    closure.reason, closure.trigger_reason,
                    closure.order_id, closure.signal_id,
                ),
            )
            return cur.fetchone()[0]

    # ============================================================
    # filter_stats: 唯一键含 run_id
    # ============================================================

    def upsert_filter_stats(self, strategy_name: str, symbol: str, timeframe: str,
                            direction: str, stats: dict):
        """更新或插入过滤统计，唯一键含 run_id。"""
        with self._get_conn().cursor() as cur:
            for filter_type, count in stats.items():
                if count == 0:
                    continue
                # 先 UPDATE（含 run_id）
                cur.execute(
                    """UPDATE filter_stats
                       SET rejected_count = %s, update_time = NOW()
                       WHERE strategy_name = %s AND symbol = %s
                         AND timeframe = %s AND direction = %s
                         AND filter_type = %s AND run_id = %s""",
                    (count, strategy_name, symbol, timeframe, direction, filter_type, self.run_id),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        """INSERT INTO filter_stats
                           (run_id, strategy_name, symbol, timeframe, direction, filter_type,
                            rejected_count, checked_count, create_time, update_time)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, 0, NOW(), NOW())""",
                        (self.run_id, strategy_name, symbol, timeframe, direction, filter_type, count),
                    )

    def get_filter_stats(self, strategy_name: str | None = None,
                         symbol: str | None = None) -> list[dict]:
        """查询过滤统计，自动过滤当前 run_id。"""
        sql = "SELECT * FROM filter_stats WHERE run_id = %s"
        params: list = [self.run_id]
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

    # ============================================================
    # 归因查询：自动加 WHERE run_id 过滤
    # ============================================================

    def get_attribution_by_signal(self, signal_id: int = None) -> list[dict]:
        """按信号归因查询盈亏（自动过滤 run_id）。"""
        where_clause = "WHERE pe.run_id = %s"
        params: list = [self.run_id]
        if signal_id is not None:
            where_clause += " AND s.id = %s"
            params.append(signal_id)
        sql = f"""
            SELECT
                s.id AS signal_id,
                s.signal_type,
                s.price AS signal_price,
                s.timestamp AS signal_time,
                pe.entry_price,
                pe.entry_fee,
                SUM(ec.close_qty) AS total_closed_qty,
                SUM(ec.pnl) AS total_attributed_pnl,
                SUM(ec.fee) AS total_fee
            FROM position_entries pe
            JOIN signals s ON pe.signal_id = s.id
            JOIN position_entry_closures ec ON ec.entry_id = pe.id
            {where_clause}
            GROUP BY s.id, s.signal_type, s.price, s.timestamp, pe.entry_price, pe.entry_fee
            ORDER BY s.timestamp
        """
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_attribution_by_position(self, position_id: int = None) -> list[dict]:
        """按持仓归因查询明细（自动过滤 run_id）。"""
        where_clause = "WHERE p.run_id = %s"
        params: list = [self.run_id]
        if position_id is not None:
            where_clause += " AND p.id = %s"
            params.append(position_id)
        sql = f"""
            SELECT
                p.id AS position_id,
                p.symbol,
                p.side,
                pe.id AS entry_id,
                pe.seq,
                pe.orig_qty,
                pe.entry_price,
                pe.entry_fee,
                pe.order_id AS entry_order_id,
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
                     pe.orig_qty, pe.entry_price, pe.entry_fee,
                     pe.order_id, pe.signal_id,
                     pe.remaining_qty, pe.status
            ORDER BY p.id, pe.seq
        """
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_attribution_by_time_range(self, start_time, end_time) -> list[dict]:
        """按时间段汇总归因盈亏（自动过滤 run_id）。"""
        with self._get_conn().cursor() as cur:
            cur.execute(
                """SELECT
                    s.id AS signal_id,
                    s.signal_type,
                    s.symbol,
                    s.price AS signal_price,
                    s.timestamp AS signal_time,
                    pe.entry_price,
                    pe.entry_fee,
                    SUM(ec.close_qty) AS total_closed_qty,
                    SUM(ec.pnl) AS total_attributed_pnl,
                    SUM(ec.fee) AS total_fee,
                    MIN(ec.close_time) AS first_close_time,
                    MAX(ec.close_time) AS last_close_time
                FROM position_entries pe
                JOIN signals s ON pe.signal_id = s.id
                JOIN position_entry_closures ec ON ec.entry_id = pe.id
                WHERE ec.run_id = %s
                  AND ec.close_time >= %s AND ec.close_time <= %s
                GROUP BY s.id, s.signal_type, s.symbol, s.price,
                         s.timestamp, pe.entry_price, pe.entry_fee
                ORDER BY s.timestamp""",
                (self.run_id, start_time, end_time),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_attribution_by_reason(self) -> list[dict]:
        """按事件类型 + 触发原因聚合归因 PNL。

        reason 是事件类型（CLOSE_PARTIAL/CLOSE_FULL/STOP_LOSS/END_OF_DATA），
        trigger_reason 是策略触发原因（2.0R/ema_cross_down/reverse_signal 等）。
        返回每个 (reason, trigger_reason) 组合的笔数、总 PNL、胜率，按 PNL 降序。
        """
        sql = """
            SELECT
                ec.reason,
                ec.trigger_reason,
                COUNT(*) AS count,
                SUM(ec.pnl) AS total_pnl,
                SUM(CASE WHEN ec.pnl > 0 THEN 1 ELSE 0 END) AS win_count,
                SUM(CASE WHEN ec.pnl <= 0 THEN 1 ELSE 0 END) AS loss_count,
                CASE WHEN COUNT(*) > 0
                    THEN 100.0 * SUM(CASE WHEN ec.pnl > 0 THEN 1 ELSE 0 END) / COUNT(*)
                    ELSE 0
                END AS win_rate
            FROM position_entry_closures ec
            WHERE ec.run_id = %s
            GROUP BY ec.reason, ec.trigger_reason
            ORDER BY total_pnl DESC
        """
        with self._get_conn().cursor() as cur:
            cur.execute(sql, (self.run_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def get_attribution_by_entry_seq(self) -> list[dict]:
        """按入场序号（seq）聚合归因 PNL。

        返回每个 seq 的笔数、总 PNL、平均 PNL、胜率，按 seq 升序排列。
        seq=1 是首次开仓，seq>=2 是加仓，用于对比两者的 PNL 贡献差异。
        """
        sql = """
            SELECT
                pe.seq,
                COUNT(*) AS count,
                SUM(ec.pnl) AS total_pnl,
                AVG(ec.pnl) AS avg_pnl,
                CASE WHEN COUNT(*) > 0
                    THEN 100.0 * SUM(CASE WHEN ec.pnl > 0 THEN 1 ELSE 0 END) / COUNT(*)
                    ELSE 0
                END AS win_rate
            FROM position_entry_closures ec
            JOIN position_entries pe ON ec.entry_id = pe.id
            WHERE ec.run_id = %s
            GROUP BY pe.seq
            ORDER BY pe.seq ASC
        """
        with self._get_conn().cursor() as cur:
            cur.execute(sql, (self.run_id,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def compare_runs(self, run_ids: list[str] | None = None) -> list[dict]:
        """多 run_id 横向对比归因指标。

        按 reason 事件类型聚合（不按 pnl 符号过滤——同一事件的归因
        对不同 entry 可正可负，触发语义由 reason/trigger_reason 表达）。

        参数:
            run_ids: 待对比的 run_id 列表，None 表示查询所有 run_id

        返回:
            每个 run_id 的关键指标（总 PNL、笔数、胜率、各事件类型 PNL 贡献），
            按 total_pnl 降序排列。
        """
        if run_ids:
            placeholders = ",".join(["%s"] * len(run_ids))
            where_clause = f"WHERE ec.run_id IN ({placeholders})"
            params: list = list(run_ids)
        else:
            where_clause = ""
            params = []

        sql = f"""
            SELECT
                ec.run_id,
                SUM(ec.pnl) AS total_pnl,
                COUNT(*) AS total_trades,
                CASE WHEN COUNT(*) > 0
                    THEN 100.0 * SUM(CASE WHEN ec.pnl > 0 THEN 1 ELSE 0 END) / COUNT(*)
                    ELSE 0
                END AS win_rate,
                SUM(CASE WHEN ec.reason = 'STOP_LOSS' THEN ec.pnl ELSE 0 END) AS stop_loss_pnl,
                SUM(CASE WHEN ec.reason = 'CLOSE_PARTIAL' THEN ec.pnl ELSE 0 END) AS partial_close_pnl,
                SUM(CASE WHEN ec.reason = 'CLOSE_FULL' THEN ec.pnl ELSE 0 END) AS close_full_pnl,
                SUM(CASE WHEN ec.reason = 'END_OF_DATA' THEN ec.pnl ELSE 0 END) AS end_of_data_pnl
            FROM position_entry_closures ec
            {where_clause}
            GROUP BY ec.run_id
            ORDER BY total_pnl DESC
        """
        with self._get_conn().cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


# ============================================================
# 回测 DB 建表 SQL
# 与生产结构差异：所有交易表新增 run_id 字段；filter_stats 唯一键含 run_id
# 注：klines 表结构与生产完全一致（跨 run_id 共享）
# ============================================================

_BACKTEST_SCHEMA_SQL = """
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
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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

CREATE TABLE IF NOT EXISTS position_entries (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    position_id INTEGER NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    orig_qty NUMERIC(20,8) NOT NULL,
    remaining_qty NUMERIC(20,8) NOT NULL,
    entry_price NUMERIC(20,8) NOT NULL,
    entry_fee NUMERIC(20,8) NOT NULL DEFAULT 0,
    order_id VARCHAR(50),
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    status VARCHAR(10) DEFAULT 'open',
    create_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    update_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(position_id, seq)
);

CREATE TABLE IF NOT EXISTS position_entry_closures (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(40) NOT NULL,
    entry_id INTEGER NOT NULL REFERENCES position_entries(id) ON DELETE CASCADE,
    close_qty NUMERIC(20,8) NOT NULL,
    entry_price NUMERIC(20,8) NOT NULL,
    exit_price NUMERIC(20,8) NOT NULL,
    pnl NUMERIC(20,8) NOT NULL,
    fee NUMERIC(20,8) NOT NULL,
    reason VARCHAR(20) NOT NULL,
    trigger_reason VARCHAR(40),
    order_id VARCHAR(50),
    signal_id INTEGER REFERENCES signals(id) ON DELETE SET NULL,
    close_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

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
"""
