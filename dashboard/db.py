#!/usr/bin/env python3
"""
db.py — Oracle data-access layer for the dashboard.

All original queries (portfolio_summary, category_status, stock_type_status,
trades, cap_classification_summary, lookup_symbol, and the write operations)
are unchanged from the version validated against test_dashboard_logic.py
(20/20 passing against a SQLite mock) before being ported to Oracle syntax.

Added for the expanded dashboard: realized_performance(), built strictly
from columns that already exist on `trades` — no new tables or schema
changes required.
"""
import os
import datetime
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


def trades(status=None, category=None, symbol=None, date_from=None, date_to=None):
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
    if symbol:
        sql += " AND (UPPER(symbol) LIKE :symbol OR UPPER(stock_name) LIKE :symbol)"
        params['symbol'] = f"%{symbol.upper()}%"
    if date_from:
        sql += " AND buy_date >= :date_from"; params['date_from'] = date_from
    if date_to:
        sql += " AND buy_date <= :date_to"; params['date_to'] = date_to
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


# ── Performance & Analytics (new) ─────────────────────────────────

def realized_performance():
    """All closed trades with buy/sell details and realized P&L."""
    return _df("""
        SELECT trade_id, category_name, stock_name, symbol, stock_type,
               buy_date, my_buy_price, my_buy_qty, invested_amount,
               my_sell_date, my_sell_price, my_gain_loss
        FROM trades
        WHERE status = 'Closed'
        ORDER BY my_sell_date DESC
    """)


def performance_summary():
    """Aggregate stats derived from realized_performance() — computed in
    pandas rather than Oracle-specific date functions, so it behaves the
    same regardless of NLS/session settings."""
    df = realized_performance()
    if df.empty:
        return {
            'total_realized': 0, 'win_rate': 0, 'trade_count': 0,
            'avg_holding_days': 0, 'best_trade': None, 'worst_trade': None,
        }
    df = df.copy()
    df['my_gain_loss'] = pd.to_numeric(df['my_gain_loss'], errors='coerce').fillna(0)
    df['buy_date'] = pd.to_datetime(df['buy_date'], errors='coerce')
    df['my_sell_date'] = pd.to_datetime(df['my_sell_date'], errors='coerce')
    holding_days = (df['my_sell_date'] - df['buy_date']).dt.days
    wins = (df['my_gain_loss'] > 0).sum()
    best = df.loc[df['my_gain_loss'].idxmax()] if not df.empty else None
    worst = df.loc[df['my_gain_loss'].idxmin()] if not df.empty else None
    return {
        'total_realized': float(df['my_gain_loss'].sum()),
        'win_rate': round(100 * wins / len(df), 1) if len(df) else 0,
        'trade_count': len(df),
        'avg_holding_days': round(holding_days.mean(), 1) if holding_days.notna().any() else 0,
        'best_trade': best,
        'worst_trade': worst,
    }


def cumulative_pnl_by_month():
    """Cumulative realized P&L, bucketed by month of sell date — for the
    performance trend chart."""
    df = realized_performance()
    if df.empty:
        return pd.DataFrame(columns=['month', 'cumulative_pnl'])
    df = df.copy()
    df['my_gain_loss'] = pd.to_numeric(df['my_gain_loss'], errors='coerce').fillna(0)
    df['my_sell_date'] = pd.to_datetime(df['my_sell_date'], errors='coerce')
    df = df.dropna(subset=['my_sell_date']).sort_values('my_sell_date')
    if df.empty:
        return pd.DataFrame(columns=['month', 'cumulative_pnl'])
    monthly = df.groupby(df['my_sell_date'].dt.to_period('M'))['my_gain_loss'].sum().cumsum()
    out = monthly.reset_index()
    out.columns = ['month', 'cumulative_pnl']
    out['month'] = out['month'].astype(str)
    return out


def _current_fy_range():
    """Indian fiscal year: April 1 - March 31."""
    today = datetime.date.today()
    if today.month >= 4:
        return datetime.date(today.year, 4, 1), datetime.date(today.year + 1, 3, 31)
    return datetime.date(today.year - 1, 4, 1), datetime.date(today.year, 3, 31)


def realized_pnl_fy():
    """Realized P&L for the current Indian fiscal year (Apr 1 - Mar 31), from
    Oracle's closed trades. (Kite's own P&L data lives in Zerodha Console,
    which uses a separate login/session this dashboard's enctoken auth
    can't reach — confirmed during the Aug 2026 Overview rework, so this is
    computed from what Capital Ledger itself recorded instead.)"""
    fy_start, fy_end = _current_fy_range()
    label = f"FY{fy_start.year}-{str(fy_end.year)[2:]}"
    df = realized_performance()
    if df.empty:
        return {'total_realized': 0.0, 'trade_count': 0, 'fy_label': label}
    df = df.copy()
    df['my_gain_loss'] = pd.to_numeric(df['my_gain_loss'], errors='coerce').fillna(0)
    sell_date = pd.to_datetime(df['my_sell_date'], errors='coerce')
    mask = (sell_date >= pd.Timestamp(fy_start)) & (sell_date <= pd.Timestamp(fy_end))
    fy_df = df[mask]
    return {'total_realized': float(fy_df['my_gain_loss'].sum()), 'trade_count': len(fy_df), 'fy_label': label}


def performance_by_category():
    """Realized P&L, trade count, win rate, and avg holding period — grouped
    by category. Centerpiece of the Performance page, which is deliberately
    category-level only (no per-stock detail there)."""
    df = realized_performance()
    empty = pd.DataFrame(columns=['category_name', 'realized_pnl', 'trade_count', 'win_rate', 'avg_holding_days'])
    if df.empty:
        return empty
    df = df.copy()
    df['my_gain_loss'] = pd.to_numeric(df['my_gain_loss'], errors='coerce').fillna(0)
    df['buy_date'] = pd.to_datetime(df['buy_date'], errors='coerce')
    df['my_sell_date'] = pd.to_datetime(df['my_sell_date'], errors='coerce')
    df['holding_days'] = (df['my_sell_date'] - df['buy_date']).dt.days
    df['is_win'] = df['my_gain_loss'] > 0
    out = df.groupby('category_name').agg(
        realized_pnl=('my_gain_loss', 'sum'),
        trade_count=('trade_id', 'count'),
        win_rate=('is_win', lambda s: round(100 * s.sum() / len(s), 1) if len(s) else 0),
        avg_holding_days=('holding_days', lambda s: round(s.mean(), 1) if s.notna().any() else 0),
    ).reset_index().sort_values('realized_pnl', ascending=False)
    return out


# ── Kite snapshot (new) ───────────────────────────────────────────
# The dashboard doesn't call Kite's live API on every page view — per
# explicit preference to avoid drawing Zerodha's attention / rate limiting
# from a customer-facing web app, kite_data.sync_now() is the ONLY live
# Kite call, triggered manually by the 'Sync Kite Data' button on Overview.
# It writes here; everything else reads the last-synced snapshot back.

def save_kite_snapshot(holdings, gtts, orders):
    """Replace the Kite snapshot tables with a fresh sync. Full
    delete+reinsert each time — this is a point-in-time snapshot, not a
    history, so there's no reconciliation logic needed on write."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        now = datetime.datetime.now()

        cur.execute("DELETE FROM kite_holdings_snapshot")
        if holdings:
            cur.executemany("""
                INSERT INTO kite_holdings_snapshot
                    (tradingsymbol, quantity, average_price, last_price, pnl, synced_at)
                VALUES (:1, :2, :3, :4, :5, :6)
            """, [
                (h.get('tradingsymbol'), h.get('quantity'), h.get('average_price'),
                 h.get('last_price'), h.get('pnl'), now)
                for h in holdings
            ])

        cur.execute("DELETE FROM kite_gtt_snapshot")
        if gtts:
            cur.executemany("""
                INSERT INTO kite_gtt_snapshot
                    (gtt_id, symbol, status, trigger_price, last_price, quantity,
                     sell_price, created_at, expires_at, synced_at)
                VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10)
            """, [
                (str(g.get('id')) if g.get('id') is not None else None, g.get('symbol'),
                 g.get('status'), g.get('trigger_price'), g.get('last_price'),
                 g.get('quantity'), g.get('sell_price'), g.get('created_at'),
                 g.get('expires_at'), now)
                for g in gtts
            ])

        cur.execute("DELETE FROM kite_orders_snapshot")
        if orders:
            cur.executemany("""
                INSERT INTO kite_orders_snapshot
                    (order_id, order_timestamp, tradingsymbol, transaction_type, order_type,
                     quantity, filled_quantity, price, average_price, status, synced_at)
                VALUES (:1, :2, :3, :4, :5, :6, :7, :8, :9, :10, :11)
            """, [
                (str(o.get('order_id')) if o.get('order_id') is not None else None,
                 o.get('order_timestamp'), o.get('tradingsymbol'), o.get('transaction_type'),
                 o.get('order_type'), o.get('quantity'), o.get('filled_quantity'),
                 o.get('price'), o.get('average_price'), o.get('status'), now)
                for o in orders
            ])

        conn.commit()
        return now
    finally:
        conn.close()


def get_kite_holdings():
    """Last-synced Kite holdings snapshot, as a list of dicts (matches the
    shape the old live-fetch code returned, so downstream code in
    kite_data.py didn't need to change)."""
    df = _df("SELECT tradingsymbol, quantity, average_price, last_price, pnl FROM kite_holdings_snapshot")
    return df.to_dict('records')


def get_kite_gtts():
    """Last-synced, already-flattened GTT snapshot."""
    df = _df("""
        SELECT gtt_id AS id, symbol, status, trigger_price, last_price,
               quantity, sell_price, created_at, expires_at
        FROM kite_gtt_snapshot
    """)
    return df.to_dict('records')


def get_kite_orders():
    """Last-synced order book snapshot."""
    df = _df("""
        SELECT order_id, order_timestamp, tradingsymbol, transaction_type,
               order_type, quantity, filled_quantity, price, average_price, status
        FROM kite_orders_snapshot
    """)
    return df.to_dict('records')


def get_kite_last_synced():
    """Timestamp of the last successful Kite sync, or None if never synced."""
    df = _df("SELECT MAX(synced_at) AS synced_at FROM kite_holdings_snapshot")
    if df.empty or pd.isna(df.iloc[0]['synced_at']):
        return None
    return df.iloc[0]['synced_at']


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
    print("\nPerformance summary:", performance_summary())
