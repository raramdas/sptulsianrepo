#!/usr/bin/env python3
"""
budget_manager.py — AMFI stock-cap lookup, category/stock-type budget checks,
and trade read/write against a SINGLE TENANT's Oracle schema.

REFACTORED FOR MULTI-TENANCY: every function now takes an explicit `conn`
parameter (that tenant's own open connection) instead of caching one global
connection. This is required because the bot now loops over N tenants per
run, each with their own isolated schema/connection — a single cached
connection would silently mix data between tenants.

The SQL itself is UNCHANGED from the single-tenant v4 version; only the
connection-sourcing changed. Since each tenant's schema has its own
trades/portfolio_budget/category_allocation tables (no tenant_id column
anywhere), the queries don't need any tenant filtering — the schema
boundary IS the tenant boundary.

Test independently (uses tenant_manager to get a real tenant connection):
    python3 -c "
from tenant_manager import get_active_tenants, open_tenant_connection
from budget_manager import get_stock_cap_type, check_budget_available
tenants, admin_conn = get_active_tenants()
t = tenants[0]
conn = open_tenant_connection(admin_conn, t)
print(get_stock_cap_type(conn, 'RELIANCE'))
print(check_budget_available(conn, 'Big Gems', 'Large Cap', 5000))
conn.close(); admin_conn.close()
"
"""
from datetime import datetime
from config import log, IST


def get_stock_cap_type(conn, symbol):
    """Look up Large/Mid/Small/Micro Cap classification from the shared
    AMFI-derived table (reachable via the synonym created at provisioning)."""
    if not conn or not symbol:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT cap_type FROM stock_cap_classification WHERE symbol = :symbol",
            {'symbol': symbol.strip().upper()}
        )
        row = cursor.fetchone()
        cursor.close()
        return row[0] if row else None
    except Exception as e:
        log(f"  get_stock_cap_type error: {e}")
        return None


def get_category_id(conn, category_name):
    """Fetch the active category_id for a given category name, within
    this tenant's own category_allocation table."""
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ca.category_id
            FROM category_allocation ca
            JOIN portfolio_budget pb ON pb.budget_id = ca.budget_id AND pb.is_active = 'Y'
            WHERE ca.is_active = 'Y' AND UPPER(ca.category_name) = UPPER(:name)
        """, {'name': category_name})
        row = cursor.fetchone()
        cursor.close()
        return row[0] if row else None
    except Exception as e:
        log(f"  get_category_id error: {e}")
        return None


def check_budget_available(conn, category_name, cap_type, invest_amt):
    """
    Check both category budget and stock-type-within-category budget,
    within this tenant's own schema.
    Returns (True, category_id) if invest_amt fits in BOTH, else (False, category_id/None).
    If cap_type is unknown, only the category budget is checked (fallback).
    If Oracle is unreachable, fails open (allows the trade) so the buy bot
    isn't blocked entirely by a DB outage.
    """
    if not conn:
        log("  Budget check: no Oracle connection — allowing trade (fail-open)")
        return True, None

    category_id = get_category_id(conn, category_name)
    if not category_id:
        log(f"  Budget check: category '{category_name}' not found — allowing trade (fail-open)")
        return True, None

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT available FROM category_budget_status WHERE category_name = :name
        """, {'name': category_name})
        row = cursor.fetchone()
        category_available = float(row[0]) if row else 0

        if category_available < invest_amt:
            log(f"  Budget check: category '{category_name}' available Rs.{category_available:,.2f} "
                f"< needed Rs.{invest_amt:,.2f} — SKIP")
            cursor.close()
            return False, category_id

        if cap_type:
            cursor.execute("""
                SELECT stock_type_budget, invested
                FROM stock_type_budget_status
                WHERE category_name = :name AND stock_type = :cap_type
            """, {'name': category_name, 'cap_type': cap_type})
            row = cursor.fetchone()
            if row:
                stock_type_budget, invested = float(row[0]), float(row[1])
                stock_type_available = stock_type_budget - invested
            else:
                cursor.execute("""
                    SELECT CASE :cap_type
                        WHEN 'Large Cap' THEN large_cap_pct
                        WHEN 'Mid Cap'   THEN mid_cap_pct
                        WHEN 'Small Cap' THEN small_cap_pct
                        WHEN 'Micro Cap' THEN micro_cap_pct
                    END
                    FROM category_allocation WHERE category_id = :cid
                """, {'cap_type': cap_type, 'cid': category_id})
                pct_row = cursor.fetchone()
                pct = float(pct_row[0]) if pct_row and pct_row[0] else 0
                cursor.execute("SELECT total_budget FROM portfolio_budget WHERE is_active = 'Y'")
                total_budget = float(cursor.fetchone()[0])
                stock_type_available = total_budget * pct / 100

            if stock_type_available < invest_amt:
                log(f"  Budget check: {cap_type} in '{category_name}' available Rs.{stock_type_available:,.2f} "
                    f"< needed Rs.{invest_amt:,.2f} — SKIP")
                cursor.close()
                return False, category_id
        else:
            log(f"  Budget check: cap_type unknown — checking category budget only (fallback)")

        cursor.close()
        log(f"  Budget check PASSED: category available Rs.{category_available:,.2f}, proceeding with Rs.{invest_amt:,.2f}")
        return True, category_id

    except Exception as e:
        log(f"  Budget check error: {e} — allowing trade (fail-open)")
        return True, category_id


def insert_trade_to_oracle(conn, tip, category_id):
    """Insert the trade into THIS TENANT's own TRADES table."""
    if not conn:
        log("  Oracle insert skipped — no connection")
        return
    try:
        cursor = conn.cursor()
        today = datetime.now(IST).date()
        buy_status = tip.get('buy_status')
        if buy_status in ('ERROR', 'SKIPPED'):
            invested_amount = 0
        else:
            invested_amount = (tip.get('buy_price') or 0) * (tip.get('qty') or 0)
        cursor.execute("""
            INSERT INTO trades (
                category_id, category_name, stock_name, symbol, stock_type,
                buy_date, recommended_price, target_price, timeframe, have_interest,
                status, my_buy_date, order_type, buy_order_id, market_price_at_buy,
                my_buy_price, my_buy_qty, invested_amount, notes
            ) VALUES (
                :category_id, :category_name, :stock_name, :symbol, :stock_type,
                :buy_date, :recommended_price, :target_price, :timeframe, :have_interest,
                :status, :my_buy_date, :order_type, :buy_order_id, :market_price_at_buy,
                :my_buy_price, :my_buy_qty, :invested_amount, :notes
            )
        """, {
            'category_id': category_id,
            'category_name': tip.get('category', ''),
            'stock_name': tip.get('stock', ''),
            'symbol': tip.get('kite_symbol', ''),
            'stock_type': tip.get('cap_type', ''),
            'buy_date': today,
            'recommended_price': tip.get('email_price'),
            'target_price': tip.get('target') or None,
            'timeframe': tip.get('timeframe', ''),
            'have_interest': tip.get('have_interest', ''),
            'status': tip.get('buy_status') if tip.get('buy_status') in ('ERROR', 'SKIPPED') else 'Open',
            'my_buy_date': today,
            'order_type': tip.get('order_type', ''),
            'buy_order_id': tip.get('buy_order_id', ''),
            'market_price_at_buy': tip.get('mkt_price'),
            'my_buy_price': tip.get('buy_price'),
            'my_buy_qty': tip.get('qty'),
            'invested_amount': invested_amount,
            'notes': tip.get('note', ''),
        })
        conn.commit()
        cursor.close()
        log(f"  Trade inserted: {tip.get('stock')}")
    except Exception as e:
        log(f"  Oracle insert error: {e}")


def get_open_trades_with_target(conn, scan_dates):
    """Open trades with a target price set, no GTT placed yet, within the scan date window."""
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        placeholders = ",".join(f":d{i}" for i in range(len(scan_dates)))
        params = {f"d{i}": d for i, d in enumerate(scan_dates)}
        # Oracle treats '' as NULL, so `x NOT IN ('', 'DRY_RUN')` silently
        # becomes `x NOT IN (NULL, 'DRY_RUN')` — which is never true for any
        # x, matching zero rows always. buy_order_id IS NOT NULL above
        # already excludes NULL/empty, so only 'DRY_RUN' needs excluding here.
        cursor.execute(f"""
            SELECT trade_id, category_name, stock_name, symbol, stock_type, buy_date,
                   target_price, buy_order_id, my_buy_qty
            FROM trades
            WHERE status = 'Open' AND target_price IS NOT NULL AND gtt_id IS NULL
              AND TO_CHAR(buy_date, 'YYYY-MM-DD') IN ({placeholders})
              AND buy_order_id IS NOT NULL
              AND buy_order_id <> 'DRY_RUN'
        """, params)
        cols = [d[0].lower() for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception as e:
        log(f"  get_open_trades_with_target error: {e}")
        return []


def get_open_trades_with_gtt(conn):
    """Open trades that already have a GTT placed — for checking trigger status.
    Includes target_price/my_buy_qty/notes so a triggered-but-unfilled GTT
    can be recreated at the same target without a second lookup."""
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        # Same Oracle ''-is-NULL trap as get_open_trades_with_target() above —
        # gtt_id IS NOT NULL already excludes NULL/empty, only 'DRY_RUN' needs excluding.
        cursor.execute("""
            SELECT trade_id, stock_name, symbol, gtt_id, buy_order_id,
                   target_price, my_buy_qty, notes
            FROM trades WHERE status = 'Open' AND gtt_id IS NOT NULL
              AND gtt_id <> 'DRY_RUN'
        """)
        cols = [d[0].lower() for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception as e:
        log(f"  get_open_trades_with_gtt error: {e}")
        return []


def set_gtt_placed_oracle(conn, trade_id, gtt_id, dry_run=False, note=None):
    if not conn:
        return
    try:
        cursor = conn.cursor()
        status = 'DRY_RUN' if dry_run else 'PLACED'
        if note is not None:
            cursor.execute("""
                UPDATE trades SET gtt_id = :gtt_id, gtt_status = :status,
                    notes = :note, updated_at = SYSTIMESTAMP
                WHERE trade_id = :id
            """, {'gtt_id': gtt_id, 'status': status, 'note': note, 'id': trade_id})
        else:
            cursor.execute("""
                UPDATE trades SET gtt_id = :gtt_id, gtt_status = :status, updated_at = SYSTIMESTAMP
                WHERE trade_id = :id
            """, {'gtt_id': gtt_id, 'status': status, 'id': trade_id})
        conn.commit()
        cursor.close()
    except Exception as e:
        log(f"  set_gtt_placed_oracle error: {e}")


def mark_trade_error_oracle(conn, trade_id, message):
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades SET status = 'ERROR', notes = :msg, updated_at = SYSTIMESTAMP
            WHERE trade_id = :id
        """, {'msg': message, 'id': trade_id})
        conn.commit()
        cursor.close()
    except Exception as e:
        log(f"  mark_trade_error_oracle error: {e}")


def close_trade_in_oracle(conn, buy_order_id, target_met_date):
    """Mark a trade as Closed when its GTT triggers — this frees up the
    category/stock-type budget since the views only sum status='Open'."""
    if not conn:
        log("  Oracle close skipped — no connection")
        return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE trades
            SET status = 'Closed', target_met = 'Yes', target_met_date = :target_met_date,
                updated_at = SYSTIMESTAMP
            WHERE buy_order_id = :buy_order_id AND status = 'Open'
        """, {'buy_order_id': buy_order_id, 'target_met_date': target_met_date})
        conn.commit()
        log(f"  Trade closed for buy_order_id={buy_order_id} (rows updated: {cursor.rowcount})")
        cursor.close()
    except Exception as e:
        log(f"  Oracle close error: {e}")


if __name__ == '__main__':
    from tenant_manager import get_active_tenants, open_tenant_connection
    tenants, admin_conn = get_active_tenants()
    if not tenants:
        print("No active tenants found.")
    else:
        t = tenants[0]
        print(f"Testing against tenant: {t.tenant_name}")
        conn = open_tenant_connection(admin_conn, t)
        print("RELIANCE cap type:", get_stock_cap_type(conn, 'RELIANCE'))
        print("Big Gems category_id:", get_category_id(conn, 'Big Gems'))
        ok, cid = check_budget_available(conn, 'Big Gems', 'Large Cap', 5000)
        print(f"Budget check (Big Gems, Large Cap, 5000): ok={ok}, category_id={cid}")
        conn.close()
    admin_conn.close()
