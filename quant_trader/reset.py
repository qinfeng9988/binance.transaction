"""交易数据归档、重置与日志清理工具。

使用方式:
    # 归档 + 清空交易数据（交互式确认）
    python -m quant_trader.reset

    # 仅归档不清空
    python -m quant_trader.reset --archive-only

    # 列出已有的归档表
    python -m quant_trader.reset --list

    # 删除指定归档表
    python -m quant_trader.reset --drop orders_20260521

    # 清理日志文件（主日志超过一定大小后保留最近 N 行）
    python -m quant_trader.reset --clear-logs     # 清理所有日志
    python -m quant_trader.reset --clear-logs 5000  # 主日志只保留最后 5000 行
"""

import argparse
import os
import sys
from datetime import datetime

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.data_store import DataStore


# 需要归档的交易表
TRADE_TABLES = ["orders", "signals", "positions"]


def get_conn(data_store: DataStore):
    """获取数据库连接和游标。"""
    return data_store._get_conn()


def list_archives(data_store: DataStore):
    """列出所有已有的归档表。"""
    conn = get_conn(data_store)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND (table_name LIKE 'orders_%%'
                OR table_name LIKE 'signals_%%'
                OR table_name LIKE 'positions_%%')
            ORDER BY table_name
        """)
        rows = cur.fetchall()

    if not rows:
        print("  暂无归档表")
        return

    # 按日期分组显示
    from collections import defaultdict
    groups = defaultdict(list)
    for (name,) in rows:
        # 提取日期部分（去掉表名前缀）
        for prefix in ["orders_", "signals_", "positions_"]:
            if name.startswith(prefix):
                date = name[len(prefix):]
                break
        else:
            date = "unknown"
        groups[date].append(name)

    print(f"\n  已有归档 ({len(rows)} 个表):")
    print("  " + "-" * 50)
    for date, tables in sorted(groups.items(), reverse=True):
        print(f"  日期: {date}")
        for t in tables:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM {t}")
                count = cur.fetchone()[0]
            print(f"    {t:<25} ({count} 条记录)")
    print()


def drop_archive(data_store: DataStore, table_name: str):
    """删除指定的归档表。"""
    conn = get_conn(data_store)
    with conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table_name}")
    print(f"  已删除归档表: {table_name}")


def archive_tables(data_store: DataStore, suffix: str | None = None):
    """将当前交易数据归档到带时间戳的备份表。"""
    if suffix is None:
        suffix = datetime.now().strftime("%Y%m%d_%H%M%S")

    conn = get_conn(data_store)

    for table in TRADE_TABLES:
        archive_name = f"{table}_{suffix}"

        # 检查归档表是否已存在
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            """, (archive_name,))
            exists = cur.fetchone()[0]

            if exists:
                print(f"  [跳过] 归档表已存在: {archive_name}")
                continue

            # 创建备份表（复制结构和数据）
            cur.execute(f"CREATE TABLE {archive_name} AS TABLE {table}")
            row_count = cur.rowcount if cur.rowcount >= 0 else 0

            # 获取记录数
            cur.execute(f"SELECT COUNT(*) FROM {archive_name}")
            count = cur.fetchone()[0]

        print(f"  [归档] {table} -> {archive_name} ({count} 条记录)")

    print(f"  归档完成 (suffix={suffix})")


def clear_tables(data_store: DataStore):
    """清空交易表数据。"""
    conn = get_conn(data_store)

    for table in TRADE_TABLES:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table}")
            print(f"  [清理] {table} 已清空")


def main():
    parser = argparse.ArgumentParser(description="交易数据归档与重置工具")
    parser.add_argument("--archive-only", action="store_true",
                        help="仅归档，不清空数据")
    parser.add_argument("--list", action="store_true",
                        help="列出已有归档表")
    parser.add_argument("--drop", metavar="TABLE",
                        help="删除指定的归档表")
    parser.add_argument("--suffix", type=str, default=None,
                        help="归档表后缀（默认使用时间戳）")
    parser.add_argument("--force", "-f", action="store_true",
                        help="跳过确认")
    parser.add_argument("--clear-logs", nargs="?", const=5000, type=int, default=0,
                        help="清理日志文件，保留最近 N 行（默认 5000）")
    args = parser.parse_args()

    cfg = ConfigLoader()
    exchange_cfg = cfg.load_static()

    ds = DataStore(
        host=exchange_cfg.db_host,
        port=exchange_cfg.db_port,
        dbname=exchange_cfg.db_name,
        user=exchange_cfg.db_user,
        password=exchange_cfg.db_password,
    )
    ds.connect()

    try:
        if args.list:
            list_archives(ds)
            return

        if args.drop:
            drop_archive(ds, args.drop)
            return

        if args.clear_logs > 0:
            print(f"\n正在清理日志文件（保留最近 {args.clear_logs} 行）...")
            clear_logs(args.clear_logs)
            return

        # 归档
        print("\n正在归档交易数据...")
        archive_tables(ds, suffix=args.suffix)

        if args.archive_only:
            print("  [仅归档模式] 跳过清理")
            return

        # 确认
        if not args.force:
            print("\n即将清空以下表的数据:")
            for t in TRADE_TABLES:
                with get_conn(ds).cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {t}")
                    count = cur.fetchone()[0]
                print(f"  {t}: {count} 条记录")
            print("\nK 线数据 (klines) 不会被删除。")
            confirm = input("\n确认清空？(yes/no): ")
            if confirm.strip().lower() != "yes":
                print("  已取消")
                return

        # 清理
        print("\n正在清理交易数据...")
        clear_tables(ds)

        print(f"  自增序列已重置")

        print("\n完成！模拟账户已重置，可以开始新的策略运行。")
        print("运行 python -m quant_trader.main 重新开始交易。")

    finally:
        ds.close()

    print()


# ==================== 日志清理 ====================

def clear_logs(keep_lines: int):
    """清理或压缩日志文件，保留指定行数。"""
    import pathlib

    log_dir = pathlib.Path(__file__).parent / "logs"
    if not log_dir.exists():
        print("  日志目录不存在")
        return

    total_before = sum(os.path.getsize(f) for f in log_dir.glob("*"))
    kept = 0
    cleared = 0

    for logfile in sorted(log_dir.glob("*.log")):
        lines = logfile.read_text(encoding="utf-8").splitlines()

        if len(lines) <= keep_lines:
            continue  # 不需要清理

        truncated_lines = lines[-keep_lines:]
        with open(logfile, "w", encoding="utf-8") as f:
            f.write("\n".join(truncated_lines))

        kept += len(truncated_lines)
        cleared += len(lines) - len(truncated_lines)
        saved_mb = (total_before - sum(os.path.getsize(f) for f in log_dir.glob("*"))) / 1024 / 1024
        print(f"  [清理] {logfile.name}: {len(lines):,} 行 → {len(truncated_lines):,} 行（删除 {cleared:,} 行）")

    total_after = sum(os.path.getsize(f) for f in log_dir.glob("*"))
    saved_mb = (total_before - total_after) / 1024 / 1024
    print(f"\n  日志清理完成: 保留 {kept:,} 行，删除 {cleared:,} 行，节省 {saved_mb:.1f} MB")


if __name__ == "__main__":
    main()
