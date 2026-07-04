#!/usr/bin/env python3
"""
db.py — Oracle data-access layer for the dashboard.
All queries here were validated against a SQLite mock in test_dashboard_logic.py
(20/20 passing) before being ported to Oracle syntax.
"""
import os
import oracledb
import pandas as pd
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

ORACLE_USER            = os.environ['ORACLE_USER']
ORACLE_PASSWORD        = os.environ['ORACLE_PASSWORD']
ORACLE_DSN             = os.environ['ORACLE_DSN']
ORACLE_WALLET_DIR      = os.environ['ORACLE_WALLET_DIR']
ORACLE_WALLET_PASSWORD = os.environ['ORACLE_WALLET_PASSWORD']


def get_connection():
    return oracledb.connect(
        user=ORACLE_USER,
        password=ORACLE_PASSWORD,
        dsn=ORACLE_DSN,
        config_dir=ORACLE_WALLET_DIR,
        wallet_location=ORACLE_WALLET_DIR,
        wallet_password=ORACLE_WALLET_PASSWORD,
    )


def _df(sql, params=None):
    """Run a query and return a pandas DataFrame."""
    conn = get_connection()
    try:
        df = pd.read_sql(sql, conn, params=params or {})
        df.columns = [c.lower() for c in df.columns]
        return df
    finally:
        conn.close()


# ── Read queries ─────────────────────────────────────────────────

def portfolio_summary():
    df = _df("""
        SELECT
            pb.total_budget,
            NVL((SELECT SUM(invested_amount) FROM trades WHERE status='Open'), 0) AS invested,
            (SELECT COUNT(*) FROM trades WHERE status='Open') AS open_positions
        FROM portfolio_budget pb
        WHERE pb.is_active='Y'
    """)
    if df.empty:
        return {'total_budget': 0, 'invested': 0, 'available': 0, 'open_positions': 0, 'utilization_pct': 0}
    row = df.iloc[0]
    total = float(row['total_budget'])
    invested = float(row['invested'])
    return {
        'total_budget': total,
        'invested': invested,
        'available': total - invested,
        'open_positions': int(row['open_positions']),
        'utilization_pct': round(invested / total * 100, 1) if total else 0,
    }


def category_status():
    return _df("""
        SELECT ca.category_name,
               ca.allocation_pct,
               ROUND(pb.total_budget * ca.allocation_pct/100, 2) AS category_budget,
               NVL(SUM(CASE WHEN t.status='Open' THEN t.invested_amount END),0) AS invested,
               ROUND(pb.total_budget * ca.allocation_pct/100, 2)
                   - NVL(SUM(CASE WHEN t.status='Open' THEN t.invested_amount END),0) AS available
        FROM category_allocation ca
        JOIN portfolio_budget pb ON pb.budget_id=ca.budget_id AND pb.is_active='Y'
        LEFT JOIN trades t ON t.category_id=ca.category_id
        WHERE ca.is_active='Y'
        GROUP BY ca.category_name, ca.allocation_pct, pb.total_budget
        ORDER BY ca.category_name
    """)


def stock_type_status(category_name):
    base = _df("""
        SELECT ca.category_name, pb.total_budget,
               ca.large_cap_pct, ca.mid_cap_pct, ca.small_cap_pct, ca.micro_cap_pct
        FROM category_allocation ca
        JOIN portfolio_budget pb ON pb.budget_id=ca.budget_id AND pb.is_active='Y'
        WHERE ca.is_active='Y' AND ca.category_name = :name
    """, {'name': category_name})
    if base.empty:
        return pd.DataFrame()
    r = base.iloc[0]
    total = float(r['total_budget'])
    rows = []
    for cap_type, col in [('Large Cap', 'large_cap_pct'), ('Mid Cap', 'mid_cap_pct'),
                          ('Small Cap', 'small_cap_pct'), ('Micro Cap', 'micro_cap_pct')]:
        pct = float(r[col])
        budget = total * pct / 100
        inv_df = _df("""
            SELECT NVL(SUM(invested_amount),0) AS invested
            FROM trades WHERE category_name = :name AND stock_type = :cap AND status='Open'
        """, {'name': category_name, 'cap': cap_type})
        invested = float(inv_df.iloc[0]['invested']) if not inv_df.empty else 0
        rows.append({'cap_type': cap_type, 'pct': pct, 'budget': budget,
                     'invested': invested, 'available': budget - invested})
    return pd.DataFrame(rows)


def trades(status=None, category=None):
    sql = """
        SELECT trade_id, category_name, stock_name, symbol, stock_type, buy_date,
               status, order_type, my_buy_price, my_buy_qty, invested_amount,
               target_price, gtt_id, gtt_status, my_sell_date, my_sell_price,
               my_gain_loss, notes
        FROM trades WHERE 1=1
    """
    params = {}
    if status:
        sql += " AND status = :status"; params['status'] = status
    if category:
        sql += " AND category_name = :category"; params['category'] = category
    sql += " ORDER BY trade_id DESC"
    return _df(sql, params)


def cap_classification_summary():
    return _df("""
        SELECT cap_type, COUNT(*) AS count, source_period
        FROM stock_cap_classification
        GROUP BY cap_type, source_period
        ORDER BY count DESC
    """)


def lookup_symbol(query):
    """Search both ticker symbol and company name, partial match, ranked by relevance."""
    pattern = f"%{query.upper()}%"
    prefix = f"{query.upper()}%"
    return _df("""
        SELECT symbol, company_name, amfi_rank, cap_type, market_cap_cr, source_period
        FROM (
            SELECT symbol, company_name, amfi_rank, cap_type, market_cap_cr, source_period,
                CASE
                    WHEN UPPER(symbol) = UPPER(:sym) THEN 1
                    WHEN UPPER(symbol) LIKE :prefix THEN 2
                    WHEN UPPER(company_name) LIKE :prefix THEN 3
                    ELSE 4
                END AS match_rank
            FROM stock_cap_classification
            WHERE UPPER(symbol) LIKE :pattern OR UPPER(company_name) LIKE :pattern
        )
        ORDER BY match_rank, company_name
        FETCH FIRST 10 ROWS ONLY
    """, {'sym': query, 'prefix': prefix, 'pattern': pattern})


# ── Write operations ─────────────────────────────────────────────

def update_total_budget(new_amount):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE portfolio_budget SET total_budget = :amt WHERE is_active='Y'",
                    {'amt': new_amount})
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def update_category_pct(category_name, allocation_pct, large_pct, mid_pct, small_pct, micro_pct):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE category_allocation
            SET allocation_pct = :ap, large_cap_pct = :lp, mid_cap_pct = :mp,
                small_cap_pct = :sp, micro_cap_pct = :mcp
            WHERE category_name = :name AND is_active='Y'
        """, {'ap': allocation_pct, 'lp': large_pct, 'mp': mid_pct,
              'sp': small_pct, 'mcp': micro_pct, 'name': category_name})
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def close_trade(trade_id, sell_price, sell_date):
    """Manually close a trade — frees its budget and records sell details."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        # Fetch qty and buy price to compute gain/loss
        cur.execute("SELECT my_buy_price, my_buy_qty FROM trades WHERE trade_id = :id",
                    {'id': trade_id})
        row = cur.fetchone()
        if not row:
            return 0
        buy_price, qty = float(row[0] or 0), int(row[1] or 0)
        gain_loss = (sell_price - buy_price) * qty
        cur.execute("""
            UPDATE trades
            SET status='Closed', my_sell_price = :sp, my_sell_date = :sd,
                my_sell_qty = :qty, my_gain_loss = :gl,
                target_met = 'Manual', target_met_date = :sd, updated_at = SYSTIMESTAMP
            WHERE trade_id = :id
        """, {'sp': sell_price, 'sd': sell_date, 'qty': qty, 'gl': gain_loss, 'id': trade_id})
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def open_trades_for_targets():
    """Open trades, most recent first — for the Set Targets page."""
    return _df("""
        SELECT trade_id, category_name, stock_name, symbol, stock_type, buy_date,
               my_buy_price, my_buy_qty, invested_amount, target_price, timeframe, have_interest
        FROM trades WHERE status = 'Open'
        ORDER BY buy_date DESC, trade_id DESC
    """)


def update_trade_target(trade_id, target_price, have_interest, timeframe):
    """Set target price / have-interest / timeframe on a trade (Oracle-native, no sheet involved)."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE trades
            SET target_price = :tp, have_interest = :hi, timeframe = :tf, updated_at = SYSTIMESTAMP
            WHERE trade_id = :id
        """, {'tp': target_price, 'hi': have_interest, 'tf': timeframe, 'id': trade_id})
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


if __name__ == '__main__':
    # Smoke test against the real DB
    print("Portfolio summary:", portfolio_summary())
    print("\nCategory status:")
    print(category_status().to_string())
    print("\nBig Gems stock-type drill-down:")
    print(stock_type_status('Big Gems').to_string())
    print("\nOpen trades:")
    print(trades(status='Open').to_string())
