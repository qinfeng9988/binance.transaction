create table klines
(
    id          serial
        primary key,
    symbol      varchar(20)                            not null,
    timeframe   varchar(10)                            not null,
    timestamp   timestamp with time zone               not null,
    open        numeric(20, 8)                         not null,
    high        numeric(20, 8)                         not null,
    low         numeric(20, 8)                         not null,
    close       numeric(20, 8)                         not null,
    volume      numeric(20, 8)                         not null,
    create_time timestamp with time zone default now() not null,
    update_time timestamp with time zone default now() not null,
    unique (symbol, timeframe, timestamp)
);

comment on table klines is 'K 线行情数据表，回测 DB 中跨 run_id 共享复用';

comment on column klines.symbol is '交易对标识，如 "BTC/USDT"';

comment on column klines.timeframe is 'K 线周期，可选值："1m" | "3m" | "5m" | "15m" | "1h" | "4h" | "1d"';

comment on column klines.timestamp is 'K 线开盘时间（带时区，UTC）';

alter table klines
    owner to postgres;

create index idx_klines_symbol_tf
    on klines (symbol asc, timeframe asc, timestamp desc);

create trigger update_klines_update_time
    before update
    on klines
    for each row
execute procedure update_update_time_column();

create table signals
(
    id            integer                  default nextval('signals_id_seq'::regclass) not null
        primary key,
    run_id        varchar(40)                                                          not null,
    strategy_name varchar(50)                                                          not null,
    symbol        varchar(20)                                                          not null,
    timeframe     varchar(10)                                                          not null,
    signal_type   varchar(10)                                                          not null,
    price         numeric(20, 8)                                                       not null,
    stop_loss     numeric(20, 8),
    timestamp     timestamp with time zone                                             not null,
    leverage      integer                  default 1,
    create_time   timestamp with time zone default now()                               not null,
    update_time   timestamp with time zone default now()                               not null
);

comment on table signals is '策略交易信号表（回测），按 run_id 隔离';

comment on column signals.run_id is '回测运行 ID，格式 bt_{timestamp}_{symbol}_{timeframe}_{param_hash8}';

alter table signals
    owner to postgres;

create index idx_signals_run_id
    on signals (run_id asc, timestamp desc);

create trigger update_signals_update_time
    before update
    on signals
    for each row
execute procedure update_update_time_column();

create table orders
(
    id          integer                  default nextval('orders_id_seq'::regclass) not null
        primary key,
    run_id      varchar(40)                                                         not null,
    symbol      varchar(20)                                                         not null,
    price       numeric(20, 8)                                                      not null,
    quantity    numeric(20, 8)                                                      not null,
    leverage    integer                  default 1,
    timestamp   timestamp with time zone                                            not null,
    mode        varchar(20)                                                         not null,
    fee         numeric(20, 8)           default 0,
    status      varchar(20)              default 'filled'::character varying,
    order_id    varchar(50)              default ''::character varying,
    create_time timestamp with time zone default now()                              not null,
    update_time timestamp with time zone default now()                              not null
);

comment on table orders is '交易订单记录表（回测），按 run_id 隔离';

comment on column orders.run_id is '回测运行 ID';

alter table orders
    owner to postgres;

create index idx_orders_run_id
    on orders (run_id asc, timestamp desc);

create trigger update_orders_update_time
    before update
    on orders
    for each row
execute procedure update_update_time_column();

create table positions
(
    id                   integer                  default nextval('positions_id_seq'::regclass) not null
        primary key,
    run_id               varchar(40)                                                            not null,
    symbol               varchar(20)                                                            not null,
    side                 varchar(10)                                                            not null,
    entry_price          numeric(20, 8)                                                         not null,
    quantity             numeric(20, 8)                                                         not null,
    leverage             integer                  default 1,
    entry_time           timestamp with time zone                                               not null,
    stop_loss            numeric(20, 8)           default 0,
    exit_price           numeric(20, 8)           default 0,
    exit_time            timestamp with time zone,
    pnl                  numeric(20, 8)           default 0,
    status               varchar(10)              default 'open'::character varying,
    closed_quantity      numeric(20, 8)           default 0,
    close_count          integer                  default 0,
    total_fee            numeric(20, 8)           default 0,
    partial_tp_triggered text                     default ''::text,
    create_time          timestamp with time zone default now()                                 not null,
    update_time          timestamp with time zone default now()                                 not null
);

comment on table positions is '交易持仓记录表（回测），按 run_id 隔离';

comment on column positions.run_id is '回测运行 ID';

alter table positions
    owner to postgres;

create index idx_positions_open
    on positions (run_id, status)
    where ((status)::text = 'open'::text);

create trigger update_positions_update_time
    before update
    on positions
    for each row
execute procedure update_update_time_column();

create table position_entries
(
    id            integer                  default nextval('position_entries_id_seq'::regclass) not null
        primary key,
    run_id        varchar(40)                                                                   not null,
    position_id   integer                                                                       not null
        references positions
            on delete cascade,
    seq           integer                                                                       not null,
    orig_qty      numeric(20, 8)                                                                not null,
    remaining_qty numeric(20, 8)                                                                not null,
    entry_price   numeric(20, 8)                                                                not null,
    entry_fee     numeric(20, 8)           default 0                                            not null,
    order_id      varchar(50),
    signal_id     integer
                                                                                                references signals
                                                                                                    on delete set null,
    status        varchar(10)              default 'open'::character varying,
    create_time   timestamp with time zone default now()                                        not null,
    update_time   timestamp with time zone default now()                                        not null,
    unique (position_id, seq)
);

comment on table position_entries is '入场明细表（回测），按 run_id 隔离';

comment on column position_entries.run_id is '回测运行 ID';

alter table position_entries
    owner to postgres;

create index idx_entries_position
    on position_entries (position_id, seq);

create index idx_entries_open_run
    on position_entries (run_id, status)
    where ((status)::text = 'open'::text);

create index idx_entries_order
    on position_entries (order_id);

create trigger update_position_entries_update_time
    before update
    on position_entries
    for each row
execute procedure update_update_time_column();

create table filter_stats
(
    id             integer                  default nextval('filter_stats_id_seq'::regclass) not null
        primary key,
    run_id         varchar(40)                                                               not null,
    strategy_name  varchar(50)                                                               not null,
    symbol         varchar(20)                                                               not null,
    timeframe      varchar(10)                                                               not null,
    direction      varchar(10)                                                               not null,
    filter_type    varchar(30)                                                               not null,
    rejected_count integer                  default 0,
    checked_count  integer                  default 0,
    create_time    timestamp with time zone default now()                                    not null,
    update_time    timestamp with time zone default now()                                    not null,
    constraint filter_stats_run_id_strategy_name_symbol_timeframe_directio_key
        unique (run_id, strategy_name, symbol, timeframe, direction, filter_type)
);

comment on table filter_stats is '过滤统计表（回测），唯一键含 run_id 支持多组实验共存';

comment on column filter_stats.run_id is '回测运行 ID';

alter table filter_stats
    owner to postgres;

create index idx_filter_stats_lookup
    on filter_stats (run_id, strategy_name, symbol, timeframe, direction);

create trigger update_filter_stats_update_time
    before update
    on filter_stats
    for each row
execute procedure update_update_time_column();

create table position_entry_closures
(
    id             serial
        primary key,
    run_id         varchar(40)                            not null,
    entry_id       integer                                not null
        references ??? ()
        on delete cascade,
    close_qty      numeric(20, 8)                         not null,
    entry_price    numeric(20, 8)                         not null,
    exit_price     numeric(20, 8)                         not null,
    pnl            numeric(20, 8)                         not null,
    fee            numeric(20, 8)                         not null,
    reason         varchar(20)                            not null,
    trigger_reason varchar(40),
    order_id       varchar(50),
    signal_id      integer
        references ??? ()
        on delete set null,
    close_time     timestamp with time zone default now() not null
);

comment on table position_entry_closures is '出场明细表（回测），按 run_id 隔离，每笔平仓按 FIFO 扣减 entries 生成';

comment on column position_entry_closures.run_id is '回测运行 ID';

comment on column position_entry_closures.reason is '事件类型（机械事实）: CLOSE_PARTIAL=部分平仓, CLOSE_FULL=信号全平, STOP_LOSS=止损触发, END_OF_DATA=回测结束强平';

comment on column position_entry_closures.trigger_reason is '策略触发原因（业务语义）: 2.0R/3.5R=止盈级别, ema_cross_down 等=策略close信号, reverse_signal=反向信号, stop_loss=止损, end_of_backtest=回测结束';

alter table position_entry_closures
    owner to postgres;

create index idx_entry_closures_entry
    on position_entry_closures (entry_id, close_time);

create index idx_entry_closures_signal
    on position_entry_closures (signal_id);

create index idx_entry_closures_run_id
    on position_entry_closures (run_id, close_time);

create index idx_closures_order
    on position_entry_closures (order_id);

create index idx_closures_reason
    on position_entry_closures (run_id, reason);

