#!/usr/bin/env python3
"""
test_dashboard_logic.py

Validates all the SQL logic and calculations the Streamlit dashboard will use,
against a SQLite mock of the Oracle schema populated with realistic data
mirroring the actual state (2 real trades + some skipped/closed examples).

This lets us confirm every query and aggregation is correct BEFORE deploying
to the real Oracle DB (which can't be reached from the build environment).
"""
import sqlite3

def build_mock_db():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── Schema (SQLite equivalent of the Oracle tables) ──────────────
    cur.executescript("""
    CREATE TABLE portfolio_budget (
        budget_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        total_budget  REAL NOT NULL,
        effective_from TEXT DEFAULT (date('now')),
        is_active     TEXT DEFAULT 'Y'
    );

    CREATE TABLE category_allocation (
        category_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        budget_id      INTEGER,
        category_name  TEXT NOT NULL,
        allocation_pct REAL NOT NULL,
        large_cap_pct  REAL DEFAULT 0,
        mid_cap_pct    REAL DEFAULT 0,
        small_cap_pct  REAL DEFAULT 0,
        micro_cap_pct  REAL DEFAULT 0,
        is_active      TEXT DEFAULT 'Y'
    );

    CREATE TABLE stock_cap_classification (
        symbol        TEXT PRIMARY KEY,
        company_name  TEXT,
        amfi_rank     INTEGER,
        cap_type      TEXT,
        market_cap_cr REAL,
        source_period TEXT
    );

    CREATE TABLE trades (
        trade_id            INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id         INTEGER,
        category_name       TEXT,
        stock_name          TEXT,
        symbol              TEXT,
        stock_type          TEXT,
        buy_date            TEXT,
        recommended_price   REAL,
        target_price        REAL,
        timeframe           TEXT,
        have_interest       TEXT,
        status              TEXT DEFAULT 'Open',
        target_met          TEXT,
        target_met_date     TEXT,
        gain                REAL,
        my_buy_date         TEXT,
        order_type          TEXT,
        buy_order_id        TEXT,
        market_price_at_buy REAL,
        my_buy_price        REAL,
        my_buy_qty          INTEGER,
        invested_amount     REAL,
        my_sell_date        TEXT,
        my_sell_price       REAL,
        my_sell_qty         INTEGER,
        my_gain_loss        REAL,
        gtt_id              TEXT,
        gtt_status          TEXT,
        notes               TEXT
    );
    """)

    # ── Seed data mirroring the real state ───────────────────────────
    cur.execute("INSERT INTO portfolio_budget (total_budget, is_active) VALUES (200000, 'Y')")
    budget_id = cur.lastrowid

    categories = [
        ('Little Gems', 20, 10, 6, 4, 2),
        ('Big Gems', 20, 10, 6, 4, 2),
        ('Short Term Investments', 15, 10, 6, 4, 2),
        ('Medium Term Investments', 20, 10, 6, 4, 2),
        ('Regular Income Bluechips', 20, 10, 6, 4, 2),
        ('Multibagger Stocks', 10, 6, 4, 2, 2),
    ]
    cat_ids = {}
    for name, ap, lp, mp, sp, mcp in categories:
        cur.execute("""INSERT INTO category_allocation
            (budget_id, category_name, allocation_pct, large_cap_pct, mid_cap_pct, small_cap_pct, micro_cap_pct)
            VALUES (?,?,?,?,?,?,?)""", (budget_id, name, ap, lp, mp, sp, mcp))
        cat_ids[name] = cur.lastrowid

    # Classification samples
    for sym, nm, rank, cap in [
        ('SOLARINDS', 'Solar Industries India Ltd', 85, 'Large Cap'),
        ('ZENTEC', 'Zen Technologies Ltd', 320, 'Small Cap'),
        ('TDPOWERSYS', 'TD Power Systems Ltd', 610, 'Micro Cap'),
        ('ZEEL', 'Zee Entertainment Enterprises Ltd', 540, 'Micro Cap'),
        ('TMPV', 'Tata Motors Passenger Vehicles Ltd', 40, 'Large Cap'),
    ]:
        cur.execute("""INSERT INTO stock_cap_classification (symbol, company_name, amfi_rank, cap_type, source_period)
            VALUES (?,?,?,?,?)""", (sym, nm, rank, cap, 'Dec 2025'))

    # Trades: 2 real Open, some SKIPPED (invested 0), 1 Closed (recycled)
    trades = [
        # (cat, name, sym, type, status, buy_price, qty, invested, gtt_id, gtt_status, notes)
        ('Big Gems', 'Solar Industries', 'SOLARINDS', 'Large Cap', 'Open', 18659, 1, 18659, None, None, None),
        ('Medium Term Investments', 'Zen Tech', 'ZENTEC', 'Small Cap', 'Open', 3548, 1, 3548, None, None, None),
        ('Little Gems', 'TD Power', 'TDPOWERSYS', 'Micro Cap', 'SKIPPED', 4764, 1, 0, None, None, 'Insufficient category/stock-type budget'),
        ('Little Gems', 'Zee Ent', 'ZEEL', 'Micro Cap', 'SKIPPED', 4947, 1, 0, None, None, 'Insufficient category/stock-type budget'),
        ('Big Gems', 'TMPV', 'TMPV', 'Large Cap', 'SKIPPED', 4844, 1, 0, None, None, 'Insufficient category/stock-type budget'),
        # A closed trade — should NOT count against budget (recycled)
        ('Short Term Investments', 'Old Winner', 'OLDWIN', 'Mid Cap', 'Closed', 5000, 1, 5000, '12345', 'TRIGGERED', None),
    ]
    for cat, nm, sym, typ, status, bp, qty, inv, gid, gstat, notes in trades:
        cur.execute("""INSERT INTO trades
            (category_id, category_name, stock_name, symbol, stock_type, buy_date,
             status, my_buy_price, my_buy_qty, invested_amount, gtt_id, gtt_status, notes,
             target_price, order_type, buy_order_id)
            VALUES (?,?,?,?,?, date('now'), ?,?,?,?,?,?,?, ?, 'LIMIT', ?)""",
            (cat_ids[cat], cat, nm, sym, typ, status, bp, qty, inv, gid, gstat, notes,
             bp*1.15 if status != 'SKIPPED' else None, 'ORD'+sym))

    conn.commit()
    return conn


# ── Dashboard queries (SQLite versions; Oracle equivalents noted) ────

def q_portfolio_summary(conn):
    """Overall: total budget, total invested (Open only), total available, # open positions."""
    cur = conn.cursor()
    cur.execute("SELECT total_budget FROM portfolio_budget WHERE is_active='Y'")
    total_budget = cur.fetchone()['total_budget']
    cur.execute("SELECT COALESCE(SUM(invested_amount),0) AS inv, COUNT(*) AS n FROM trades WHERE status='Open'")
    row = cur.fetchone()
    invested, open_positions = row['inv'], row['n']
    return {
        'total_budget': total_budget,
        'invested': invested,
        'available': total_budget - invested,
        'open_positions': open_positions,
        'utilization_pct': round(invested / total_budget * 100, 1) if total_budget else 0,
    }

def q_category_status(conn):
    """Per-category: budget, invested (Open), available."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ca.category_name,
               ca.allocation_pct,
               ROUND(pb.total_budget * ca.allocation_pct/100, 2) AS category_budget,
               COALESCE(SUM(CASE WHEN t.status='Open' THEN t.invested_amount END),0) AS invested
        FROM category_allocation ca
        JOIN portfolio_budget pb ON pb.budget_id=ca.budget_id AND pb.is_active='Y'
        LEFT JOIN trades t ON t.category_id=ca.category_id
        WHERE ca.is_active='Y'
        GROUP BY ca.category_name, ca.allocation_pct, pb.total_budget
        ORDER BY ca.category_name
    """)
    out = []
    for r in cur.fetchall():
        out.append({
            'category': r['category_name'],
            'budget': r['category_budget'],
            'invested': r['invested'],
            'available': r['category_budget'] - r['invested'],
        })
    return out

def q_stock_type_status(conn, category_name):
    """Drill-down: within a category, budget/invested per cap type."""
    cur = conn.cursor()
    cur.execute("""
        SELECT ca.category_name,
               pb.total_budget,
               ca.large_cap_pct, ca.mid_cap_pct, ca.small_cap_pct, ca.micro_cap_pct
        FROM category_allocation ca
        JOIN portfolio_budget pb ON pb.budget_id=ca.budget_id AND pb.is_active='Y'
        WHERE ca.is_active='Y' AND ca.category_name=?
    """, (category_name,))
    r = cur.fetchone()
    if not r:
        return []
    pct_map = {
        'Large Cap': r['large_cap_pct'], 'Mid Cap': r['mid_cap_pct'],
        'Small Cap': r['small_cap_pct'], 'Micro Cap': r['micro_cap_pct'],
    }
    out = []
    for cap_type, pct in pct_map.items():
        budget = r['total_budget'] * pct / 100
        cur.execute("""SELECT COALESCE(SUM(invested_amount),0) AS inv
            FROM trades WHERE category_name=? AND stock_type=? AND status='Open'""",
            (category_name, cap_type))
        invested = cur.fetchone()['inv']
        out.append({
            'cap_type': cap_type, 'pct': pct, 'budget': budget,
            'invested': invested, 'available': budget - invested,
        })
    return out

def q_trades(conn, status=None, category=None):
    """Trade list with optional filters."""
    cur = conn.cursor()
    sql = "SELECT trade_id, category_name, stock_name, symbol, stock_type, status, my_buy_price, my_buy_qty, invested_amount, target_price, gtt_status, notes FROM trades WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"; params.append(status)
    if category:
        sql += " AND category_name=?"; params.append(category)
    sql += " ORDER BY trade_id"
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


# ── Tests ─────────────────────────────────────────────────────────

def run_tests():
    conn = build_mock_db()
    passed = failed = 0

    def check(name, actual, expected):
        nonlocal passed, failed
        if actual == expected:
            passed += 1
            print(f"  PASS: {name}")
        else:
            failed += 1
            print(f"  FAIL: {name}\n    expected {expected}\n    actual   {actual}")

    print("=== Portfolio Summary ===")
    s = q_portfolio_summary(conn)
    # Open invested = Solar 18659 + Zen 3548 = 22207 (Closed Old Winner NOT counted)
    check("total invested (Open only, excludes Closed/Skipped)", s['invested'], 22207)
    check("available", s['available'], 200000 - 22207)
    check("open positions count", s['open_positions'], 2)

    print("\n=== Category Status ===")
    cats = {c['category']: c for c in q_category_status(conn)}
    check("Big Gems invested", cats['Big Gems']['invested'], 18659)
    check("Big Gems available", cats['Big Gems']['available'], 40000 - 18659)
    check("Medium Term invested", cats['Medium Term Investments']['invested'], 3548)
    # Short Term has only a Closed trade → invested should be 0 (recycled)
    check("Short Term invested (Closed excluded)", cats['Short Term Investments']['invested'], 0)
    check("Little Gems invested (only Skipped)", cats['Little Gems']['invested'], 0)

    print("\n=== Stock-Type Drill-down (Big Gems) ===")
    st = {s['cap_type']: s for s in q_stock_type_status(conn, 'Big Gems')}
    # Large Cap budget in Big Gems = 10% of 200000 = 20000; invested = Solar 18659
    check("Big Gems Large Cap budget", st['Large Cap']['budget'], 20000)
    check("Big Gems Large Cap invested", st['Large Cap']['invested'], 18659)
    check("Big Gems Large Cap available", st['Large Cap']['available'], 20000 - 18659)
    # Micro Cap in Big Gems = 2% of 200000 = 4000; no trades
    check("Big Gems Micro Cap budget", st['Micro Cap']['budget'], 4000)
    check("Big Gems Micro Cap invested (none)", st['Micro Cap']['invested'], 0)

    print("\n=== Stock-Type Drill-down (Little Gems) ===")
    st2 = {s['cap_type']: s for s in q_stock_type_status(conn, 'Little Gems')}
    # Skipped trades have invested_amount=0, so Micro Cap invested must be 0
    check("Little Gems Micro Cap invested (skipped=0)", st2['Micro Cap']['invested'], 0)
    check("Little Gems Micro Cap budget", st2['Micro Cap']['budget'], 4000)

    print("\n=== Trades Filtering ===")
    all_trades = q_trades(conn)
    check("total trades", len(all_trades), 6)
    open_trades = q_trades(conn, status='Open')
    check("open trades", len(open_trades), 2)
    skipped_trades = q_trades(conn, status='SKIPPED')
    check("skipped trades", len(skipped_trades), 3)
    closed_trades = q_trades(conn, status='Closed')
    check("closed trades", len(closed_trades), 1)
    bg_trades = q_trades(conn, category='Big Gems')
    check("Big Gems trades (Solar Open + TMPV skipped)", len(bg_trades), 2)

    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    conn.close()
    return failed == 0


if __name__ == '__main__':
    ok = run_tests()
    exit(0 if ok else 1)
