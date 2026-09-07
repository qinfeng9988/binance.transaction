#!/usr/bin/env python3
"""对账脚本：核对一次回测的资金流（orders/entries/closures/positions 与报告数字）。

只读查询，用于解释回测报告中 总盈亏/手续费/funding 与期末资金不一致的问题。
"""
import sys
import psycopg2

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])
from quant_trader.core.config_loader import ConfigLoader

RUN = sys.argv[1] if len(sys.argv) > 1 else "bt_20260829_071641_SOL-USDT_5m_2a762668"

ex = ConfigLoader().load_static()
conn = psycopg2.connect(host=ex.db_host, port=ex.db_port, dbname="db_backtest",
                        user=ex.db_user, password=ex.db_password)
cur = conn.cursor()

def q(sql, params=(RUN,)):
    cur.execute(sql, params)
    return cur.fetchall()

print(f"===== 对账 run_id = {RUN} =====\n")

# 1. positions：已平仓的实现盈亏（引擎 trades_closed Σnet 的 DB 镜像）
rows = q("""SELECT COUNT(*), SUM(pnl), SUM(total_fee), SUM(closed_quantity)
            FROM positions WHERE run_id=%s AND status='closed'""")
print(f"positions(closed): n={rows[0][0]}  Σpnl={float(rows[0][1] or 0):.2f}  "
      f"Σtotal_fee={float(rows[0][2] or 0):.2f}  Σclosed_qty={float(rows[0][3] or 0):.4f}")

# 2. entries：入场明细手续费（开仓时真实从余额扣掉的钱）
rows = q("""SELECT COUNT(*), SUM(entry_fee), SUM(orig_qty) FROM position_entries WHERE run_id=%s""")
print(f"entries:           n={rows[0][0]}  Σentry_fee={float(rows[0][1] or 0):.2f}  Σorig_qty={float(rows[0][2] or 0):.4f}")

# 3. closures：归因明细（应≈ Σraw − 双边费）
rows = q("""SELECT COUNT(*), SUM(pnl), SUM(fee), SUM(close_qty) FROM position_entry_closures WHERE run_id=%s""")
print(f"closures:          n={rows[0][0]}  Σpnl={float(rows[0][1] or 0):.2f}  "
      f"Σfee={float(rows[0][2] or 0):.2f}  Σclose_qty={float(rows[0][3] or 0):.4f}")

# 4. orders：开仓/加仓单 vs 平仓方向单
rows = q("""
    SELECT CASE WHEN quantity > 0 THEN 'open/add' ELSE 'close/partial' END AS kind,
           COUNT(*), SUM(fee), SUM(price*ABS(quantity)), SUM(price*ABS(quantity)/leverage)
    FROM orders WHERE run_id=%s GROUP BY 1""")
for r in rows:
    kind, n, fee, notional, margin = r[0], r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0)
    print(f"orders[{kind:14s}] n={n:5d}  Σfee={fee:10.2f}  Σnotional={notional:14.2f}  Σmargin≈{margin:12.2f}")

# 5. 核心恒等式核对
#    预期余额变动 = Σpositions.pnl(净) − Σentries.entry_fee + funding_delta
sig = q("""SELECT
    (SELECT COALESCE(SUM(pnl),0) FROM positions WHERE run_id=%s AND status='closed'),
    (SELECT COALESCE(SUM(entry_fee),0) FROM position_entries WHERE run_id=%s)""", (RUN, RUN))
sum_net, sum_entry_fee = float(sig[0][0]), float(sig[0][1])
funding = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
expected = sum_net - sum_entry_fee + funding
print(f"\n恒等式: Σnet({sum_net:.2f}) − Σentry_fee({sum_entry_fee:.2f}) + funding({funding:.2f}) "
      f"= 预期余额变动 {expected:.2f}")
actual = float(sys.argv[2]) if len(sys.argv) > 2 else None
if actual is not None:
    print(f"报告实际余额变动 = {actual:.2f}")
    print(f"差额（引擎余额里凭空消失的钱）= {expected - actual:.2f}")

# 6. 开仓/加仓保证金 vs 按入场明细应扣保证金
rows = q("""SELECT COALESCE(SUM(price*quantity/leverage),0) FROM orders WHERE run_id=%s AND quantity>0""")
margin_out_orders = float(rows[0][0])
rows = q("""SELECT COALESCE(SUM(e.entry_price*e.orig_qty/p.leverage),0)
            FROM position_entries e JOIN positions p ON p.id=e.position_id WHERE e.run_id=%s""")
margin_out_entries = float(rows[0][0])
print(f"\n保证金流出(orders 口径)={margin_out_orders:.2f}  vs  (entries 口径)={margin_out_entries:.2f}  "
      f"差={margin_out_orders - margin_out_entries:.2f}")

# 7. 保证金返还核对：closures 按 entry 价位×数量/杠杆 ≈ 应返还总额（全额平仓时=流出）
rows = q("""SELECT COALESCE(SUM(ec.entry_price*ec.close_qty/p.leverage),0)
            FROM position_entry_closures ec JOIN positions p ON p.id=ec.entry_id
            WHERE ec.run_id=%s""")
margin_back = float(rows[0][0])
print(f"按 closures 重建的保证金返还≈{margin_back:.2f}  vs  entries 口径流出 {margin_out_entries:.2f}  "
      f"差={margin_out_entries - margin_back:.2f}")

# 8. 每个持仓的 fees 与 pnl 明细抽查（找出异常持仓）
print("\n── 抽查：Σclosures.pnl 与 positions.pnl 偏差最大的 5 个持仓 ──")
rows = q("""SELECT p.id, p.symbol, p.side, p.leverage, p.pnl, p.total_fee,
                   COALESCE(SUM(ec.pnl),0) AS closure_pnl,
                   COALESCE(SUM(ec.close_qty),0) AS closure_qty,
                   p.closed_quantity
            FROM positions p
            LEFT JOIN position_entry_closures ec ON ec.entry_id IN
                (SELECT id FROM position_entries WHERE position_id = p.id)
            WHERE p.run_id=%s AND p.status='closed'
            GROUP BY p.id
            ORDER BY ABS(p.pnl - COALESCE(SUM(ec.pnl),0)) DESC
            LIMIT 5""", (RUN,))
for r in rows:
    print(f"  pos#{r[0]} {r[1]} {r[2]} L{r[3]}  pnl={float(r[4]):.2f}  total_fee={float(r[5]):.2f}  "
          f"closure_pnl={float(r[6]):.2f}  closure_qty={float(r[7]):.4f}/{float(r[8]):.4f}")

conn.close()
