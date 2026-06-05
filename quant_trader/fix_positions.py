"""修复 positions 表：从 orders 表重新模拟 paper engine 逻辑计算持仓。

修复逻辑完全模拟 paper engine 的行为：
- 已有持仓 + 同向单 = 加仓（加权均价）
- 已有持仓 + 反向单 = 全部平仓（整仓平，不看反向单数量）
- 无持仓 + 任何单 = 开新仓

使用前确保量化交易程序已停止。
建议先备份: pg_dump db_quant_trader > backup_$(date +%Y%m%d).sql

使用方式:
    cd quant_trader
    python -m quant_trader.fix_positions          # 预览差异
    python -m quant_trader.fix_positions --apply   # 实际执行修复
"""

import argparse
import sys
from collections import OrderedDict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from quant_trader.core.config_loader import ConfigLoader
from quant_trader.core.data_store import DataStore


def compute_positions_from_orders(conn):
    """完全模拟 paper engine 逻辑，从 orders 表重算当前持仓。

    规则:
    - 无持仓 + 任何单 → 开新仓
    - 已有持仓 + 同向单 → 加仓（加权均价）
    - 已有持仓 + 反向单 → 全部平仓（整仓平）

    返回 dict: symbol -> {
        'side': str | None,
        'quantity': float,
        'avg_price': float,
        'leverage': int,
        'closed_trades': list,
    }
    """
    with conn.cursor() as cur:
        cur.execute(
            """SELECT symbol, side, price, quantity, leverage
               FROM orders WHERE mode = 'paper'
               ORDER BY symbol, timestamp, id"""
        )
        orders = cur.fetchall()

    # 按 symbol 分组
    symbols = {}
    for sym, side, price, qty, lev in orders:
        symbols.setdefault(sym, []).append(
            (side, float(price), float(qty), lev)
        )

    positions = {}
    closed_trades_all = {}

    for sym, sym_orders in symbols.items():
        pos = None  # None = 无持仓
        closed_trades = []

        for side, price, qty, lev in sym_orders:
            if pos is None:
                # 开新仓
                pos = {
                    'side': side,
                    'quantity': qty,
                    'price': price,
                    'leverage': lev,
                }
            elif side == pos['side']:
                # 加仓：加权平均价
                total_qty = pos['quantity'] + qty
                avg_price = (
                    (pos['price'] * pos['quantity'] + price * qty) / total_qty
                )
                pos['quantity'] = total_qty
                pos['price'] = avg_price
            else:
                # 反向单 = 全部平仓
                if pos['side'] == 'buy':
                    pnl = (price - pos['price']) * pos['quantity']
                else:
                    pnl = (pos['price'] - price) * pos['quantity']

                closed_trades.append({
                    'entry_price': pos['price'],
                    'exit_price': price,
                    'quantity': pos['quantity'],
                    'leverage': pos['leverage'],
                    'pnl': pnl,
                })
                pos = None

        if pos is not None:
            positions[sym] = {
                'side': pos['side'],
                'quantity': pos['quantity'],
                'avg_price': pos['price'],
                'leverage': pos['leverage'],
                'closed_trades': closed_trades,
            }
        else:
            positions[sym] = {
                'side': None, 'quantity': 0, 'avg_price': 0,
                'leverage': 0, 'closed_trades': closed_trades,
            }

    return positions


def get_open_positions(conn):
    """获取当前 positions 表中的 open 记录。"""
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, symbol, side, entry_price, quantity, leverage
               FROM positions WHERE status = 'open'
               ORDER BY symbol, id"""
        )
        return cur.fetchall()


def main():
    parser = argparse.ArgumentParser(description="修复 positions 表数据")
    parser.add_argument("--apply", action="store_true",
                        help="实际执行修复（默认只预览差异）")
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
    conn = ds._get_conn()

    # 1. 从 orders 重算应有持仓
    computed = compute_positions_from_orders(conn)

    # 2. 当前 positions 表 open 记录
    existing = get_open_positions(conn)
    existing_by_symbol = {}
    for row in existing:
        pid, sym, side, ep, qty, lev = row
        existing_by_symbol.setdefault(sym, []).append({
            'id': pid, 'symbol': sym, 'side': side,
            'entry_price': float(ep), 'quantity': float(qty),
            'leverage': lev,
        })

    # 3. 对比差异
    print("\n" + "=" * 95)
    print("  持仓差异对比（基于 orders 表模拟 paper engine 逻辑）")
    print("=" * 95)

    actions = []

    for sym, calc in sorted(computed.items()):
        db_positions = existing_by_symbol.get(sym, [])

        if calc['quantity'] <= 0:
            if db_positions:
                for pos in db_positions:
                    actions.append(('close', {
                        'symbol': sym,
                        'position_id': pos['id'],
                        'old_qty': pos['quantity'],
                        'old_price': pos['entry_price'],
                    }))
            continue

        db_total_qty = sum(p['quantity'] for p in db_positions)
        diff_qty = calc['quantity'] - db_total_qty
        diff_pct = abs(diff_qty / calc['quantity'] * 100)

        side_icon = "多" if calc['side'] == 'buy' else "空"
        print(f"\n  {sym} [{side_icon}]:")
        print(f"    应有持仓: {calc['quantity']:>12.6f}  均价: {calc['avg_price']:>12.2f}  "
              f"杠杆: {calc['leverage']}x")

        if db_positions:
            for pos in db_positions:
                print(f"    DB 记录#{pos['id']}: {pos['quantity']:>12.6f}  均价: {pos['entry_price']:>12.2f}")
        else:
            print(f"    DB 记录: 无")

        if diff_pct < 0.01:
            print(f"    → 一致，无需修复")
        else:
            print(f"    → 差异: {diff_qty:+.6f} ({diff_pct:+.1f}%)")
            if db_positions:
                actions.append(('update', {
                    'symbol': sym,
                    'position_id': db_positions[0]['id'],
                    'new_qty': calc['quantity'],
                    'new_price': calc['avg_price'],
                    'new_leverage': calc['leverage'],
                    'old_qty': db_total_qty,
                    'old_price': db_positions[0]['entry_price'] if len(db_positions) == 1 else 'multiple',
                }))
                if len(db_positions) > 1:
                    for pos in db_positions[1:]:
                        actions.append(('merge_close', {
                            'symbol': sym,
                            'position_id': pos['id'],
                        }))
            else:
                actions.append(('insert', {
                    'symbol': sym,
                    'new_qty': calc['quantity'],
                    'new_price': calc['avg_price'],
                    'new_leverage': calc['leverage'],
                    'side': calc['side'],
                }))

    if not actions:
        print("\n  所有持仓一致，无需修复")
    else:
        print(f"\n" + "-" * 95)
        print(f"  待执行操作 ({len(actions)} 个):")
        print("-" * 95)
        for i, (action_type, detail) in enumerate(actions, 1):
            if action_type == 'update':
                print(f"    {i}. UPDATE {detail['symbol']}: "
                      f"数量 {detail['old_qty']:.6f} → {detail['new_qty']:.6f}, "
                      f"均价 {detail['old_price']} → {detail['new_price']:.2f}")
            elif action_type == 'insert':
                print(f"    {i}. INSERT {detail['symbol']}: "
                      f"数量 {detail['new_qty']:.6f}, 均价 {detail['new_price']:.2f}, "
                      f"方向 {detail['side']}, 杠杆 {detail['new_leverage']}x")
            elif action_type == 'close':
                print(f"    {i}. CLOSE  {detail['symbol']} (#{detail['position_id']}): "
                      f"原数量 {detail['old_qty']:.6f}")
            elif action_type == 'merge_close':
                print(f"    {i}. MERGE-CLOSE {detail['symbol']} (#{detail['position_id']}): "
                      f"合并多余记录")

        if args.apply:
            print(f"\n  正在执行修复...")
            with conn.cursor() as cur:
                for action_type, detail in actions:
                    if action_type == 'update':
                        cur.execute(
                            """UPDATE positions
                               SET quantity = %s, entry_price = %s, leverage = %s
                               WHERE id = %s""",
                            (detail['new_qty'], detail['new_price'],
                             detail['new_leverage'], detail['position_id']),
                        )
                        print(f"    [已更新] {detail['symbol']} #{detail['position_id']}")

                    elif action_type == 'insert':
                        cur.execute(
                            """INSERT INTO positions (symbol, side, entry_price, quantity,
                                                      leverage, entry_time, stop_loss, status)
                               VALUES (%s, %s, %s, %s, %s, NOW(), 0, 'open')""",
                            (detail['symbol'], detail['side'], detail['new_price'],
                             detail['new_qty'], detail['new_leverage']),
                        )
                        print(f"    [已插入] {detail['symbol']}")

                    elif action_type == 'close':
                        cur.execute(
                            """UPDATE positions
                               SET status = 'closed', exit_price = 0, exit_time = NOW(), pnl = 0
                               WHERE id = %s""",
                            (detail['position_id'],),
                        )
                        print(f"    [已关闭] {detail['symbol']} #{detail['position_id']}")

                    elif action_type == 'merge_close':
                        cur.execute(
                            """UPDATE positions
                               SET status = 'closed', exit_price = 0, exit_time = NOW(), pnl = 0
                               WHERE id = %s""",
                            (detail['position_id'],),
                        )
                        print(f"    [已合并关闭] {detail['symbol']} #{detail['position_id']}")

            print("\n  修复完成！")
        else:
            print(f"\n  预览模式。确认无误后加 --apply 执行: "
                  f"python -m quant_trader.fix_positions --apply")

    ds.close()
    print()


if __name__ == "__main__":
    main()
