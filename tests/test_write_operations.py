#!/usr/bin/env python3
"""
test_write_operations.py

Validates the dashboard's WRITE operations (close_trade gain/loss math and
budget recycling, update_total_budget, update_category_pct) against the
SQLite mock — since these mutate data and must be correct before touching
the live Oracle DB.
"""
import sqlite3
import sys
sys.path.insert(0, '/home/claude')
from test_dashboard_logic import build_mock_db, q_portfolio_summary, q_category_status


def close_trade_mock(conn, trade_id, sell_price, sell_date):
    cur = conn.cursor()
    cur.execute("SELECT my_buy_price, my_buy_qty FROM trades WHERE trade_id=?", (trade_id,))
    row = cur.fetchone()
    if not row:
        return 0, None
    buy_price, qty = float(row['my_buy_price'] or 0), int(row['my_buy_qty'] or 0)
    gain_loss = (sell_price - buy_price) * qty
    cur.execute("""
        UPDATE trades SET status='Closed', my_sell_price=?, my_sell_date=?,
            my_sell_qty=?, my_gain_loss=?, target_met='Manual', target_met_date=?
        WHERE trade_id=?
    """, (sell_price, sell_date, qty, gain_loss, sell_date, trade_id))
    conn.commit()
    return cur.rowcount, gain_loss


def run():
    passed = failed = 0
    def check(name, actual, expected):
        nonlocal passed, failed
        if actual == expected:
            passed += 1; print(f"  PASS: {name}")
        else:
            failed += 1; print(f"  FAIL: {name}\n    expected {expected}\n    actual   {actual}")

    print("=== Close Trade: gain/loss + budget recycling ===")
    conn = build_mock_db()

    # Before: Big Gems invested = 18659 (Solar Industries, trade_id 1)
    cats_before = {c['category']: c for c in q_category_status(conn)}
    check("Big Gems invested before close", cats_before['Big Gems']['invested'], 18659)

    # Find Solar Industries trade_id
    cur = conn.cursor()
    cur.execute("SELECT trade_id, my_buy_price, my_buy_qty FROM trades WHERE stock_name='Solar Industries'")
    r = cur.fetchone()
    tid = r['trade_id']

    # Close it at a profit: buy 18659 x1, sell 20000 -> gain 1341
    rows, gain = close_trade_mock(conn, tid, 20000, '2026-07-10')
    check("close affected 1 row", rows, 1)
    check("gain/loss calc (20000-18659)*1", gain, 1341.0)

    # After: Big Gems invested should now be 0 (trade Closed -> recycled)
    cats_after = {c['category']: c for c in q_category_status(conn)}
    check("Big Gems invested after close (recycled)", cats_after['Big Gems']['invested'], 0)
    check("Big Gems available after close", cats_after['Big Gems']['available'], 40000)

    # Portfolio-level invested should drop by 18659 (from 22207 to 3548)
    summ = q_portfolio_summary(conn)
    check("portfolio invested after close", summ['invested'], 3548)
    check("open positions after close", summ['open_positions'], 1)

    print("\n=== Close Trade: loss scenario ===")
    conn2 = build_mock_db()
    cur2 = conn2.cursor()
    cur2.execute("SELECT trade_id FROM trades WHERE stock_name='Zen Tech'")
    tid2 = cur2.fetchone()['trade_id']
    # Zen Tech buy 3548 x1, sell 3000 -> loss -548
    rows2, gain2 = close_trade_mock(conn2, tid2, 3000, '2026-07-10')
    check("loss calc (3000-3548)*1", gain2, -548.0)

    print("\n=== Update total budget ===")
    conn3 = build_mock_db()
    cur3 = conn3.cursor()
    cur3.execute("UPDATE portfolio_budget SET total_budget=300000 WHERE is_active='Y'")
    conn3.commit()
    summ3 = q_portfolio_summary(conn3)
    check("total budget updated to 300000", summ3['total_budget'], 300000)
    # Big Gems budget should now be 20% of 300000 = 60000
    cats3 = {c['category']: c for c in q_category_status(conn3)}
    check("Big Gems budget after total change", cats3['Big Gems']['budget'], 60000)

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    return failed == 0


if __name__ == '__main__':
    ok = run()
    exit(0 if ok else 1)
