#!/usr/bin/env python3
"""
budget_manager.py — Oracle connection management, AMFI stock-cap lookup,
category/stock-type budget checks, and trade logging to the TRADES table.

Test independently:
    python3 -c "from budget_manager import get_oracle_connection; print(get_oracle_connection())"
    python3 -c "from budget_manager import get_stock_cap_type; print(get_stock_cap_type('RELIANCE'))"
    python3 -c "from budget_manager import check_budget_available; print(check_budget_available('Big Gems', 'Large Cap', 5000))"
"""
from datetime import datetime
import oracledb

from config import (
    log, IST, ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN,
    ORACLE_WALLET_DIR, ORACLE_WALLET_PASSWORD
)

_oracle_conn = None


def get_oracle_connection():
    global _oracle_conn
    if _oracle_conn:
        return _oracle_conn
    try:
        _oracle_conn = oracledb.connect(
            user=ORACLE_USER,
            password=ORACLE_PASSWORD,
            dsn=ORACLE_DSN,
            config_dir=ORACLE_WALLET_DIR,
            wallet_location=ORACLE_WALLET_DIR,
            wallet_password=ORACLE_WALLET_PASSWORD,
        )
        return _oracle_conn
    except Exception as e:
        log(f"  Oracle connection error: {e}")
        return None


def close_oracle_connection():
    global _oracle_conn
    if _oracle_conn:
        try:
            _oracle_conn.close()
        except Exception:
            pass
        _oracle_conn = None


def get_stock_cap_type(symbol):
    """Look up Large/Mid/Small/Micro Cap classification from AMFI-derived table."""
    conn = get_oracle_connection()
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


def get_category_id(category_name):
    """Fetch the active category_id for a given category name."""
    conn = get_oracle_connection()
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


def check_budget_available(category_name, cap_type, invest_amt):
    """
    Check both category budget and stock-type-within-category budget.
    Returns (True, category_id) if invest_amt fits in BOTH, else (False, category_id/None).
    If cap_type is unknown, only the category budget is checked (fallback).
    If Oracle is unreachable, fails open (allows the trade) so the buy bot
    isn't blocked entirely by a DB outage.
    """
    conn = get_oracle_connection()
    if not conn:
        log("  Budget check: no Oracle connection — allowing trade (fail-open)")
        return True, None

    category_id = get_category_id(category_name)
    if not category_id:
        log(f"  Budget check: category '{category_name}' not found in Oracle — allowing trade (fail-open)")
        return True, None

    try:
        cursor = conn.cursor()

        # 1. Category-level available budget
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

        # 2. Stock-type-level available budget (only if cap_type is known)
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
                # No trades yet for this stock_type in this category — full budget available
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


def insert_trade_to_oracle(tip, category_id):
    """Insert the trade into the Oracle TRADES table (parallel to the Google Sheet)."""
    conn = get_oracle_connection()
    if not conn:
        log("  Oracle insert skipped — no connection")
        return
    try:
        cursor = conn.cursor()
        today = datetime.now(IST).date()
        buy_status = tip.get('buy_status')
        # Only trades that actually went through count as invested money.
        # SKIPPED/ERROR/NEEDS_REVIEW trades never spent anything, regardless
        # of the qty/price that was calculated for the budget check.
        if buy_status in ('ERROR', 'SKIPPED', 'NEEDS_REVIEW'):
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
            'status': tip.get('buy_status') if tip.get('buy_status') in ('ERROR', 'SKIPPED', 'NEEDS_REVIEW') else 'Open',
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
        log(f"  Trade inserted into Oracle: {tip.get('stock')}")
    except Exception as e:
        log(f"  Oracle insert error: {e}")


def get_open_trades_with_target(scan_dates):
    """Open trades with a target price set, no GTT placed yet, within the scan date window."""
    conn = get_oracle_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        placeholders = ",".join(f":d{i}" for i in range(len(scan_dates)))
        params = {f"d{i}": d for i, d in enumerate(scan_dates)}
        cursor.execute(f"""
            SELECT trade_id, category_name, stock_name, symbol, stock_type, buy_date,
                   target_price, buy_order_id, my_buy_qty
            FROM trades
            WHERE status = 'Open' AND target_price IS NOT NULL AND gtt_id IS NULL
              AND TO_CHAR(buy_date, 'YYYY-MM-DD') IN ({placeholders})
              AND buy_order_id IS NOT NULL
              AND buy_order_id NOT IN ('', 'DRY_RUN')
        """, params)
        cols = [d[0].lower() for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception as e:
        log(f"  get_open_trades_with_target error: {e}")
        return []


def get_open_trades_with_gtt():
    """Open trades that already have a GTT placed — for checking trigger status."""
    conn = get_oracle_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT trade_id, stock_name, symbol, gtt_id, buy_order_id
            FROM trades WHERE status = 'Open' AND gtt_id IS NOT NULL
              AND gtt_id NOT IN ('', 'DRY_RUN')
        """)
        cols = [d[0].lower() for d in cursor.description]
        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception as e:
        log(f"  get_open_trades_with_gtt error: {e}")
        return []


def set_gtt_placed_oracle(trade_id, gtt_id, dry_run=False):
    conn = get_oracle_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        status = 'DRY_RUN' if dry_run else 'PLACED'
        cursor.execute("""
            UPDATE trades SET gtt_id = :gtt_id, gtt_status = :status, updated_at = SYSTIMESTAMP
            WHERE trade_id = :id
        """, {'gtt_id': gtt_id, 'status': status, 'id': trade_id})
        conn.commit()
        cursor.close()
    except Exception as e:
        log(f"  set_gtt_placed_oracle error: {e}")


def mark_trade_error_oracle(trade_id, message):
    conn = get_oracle_connection()
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


def close_trade_in_oracle(buy_order_id, target_met_date):
    """Mark a trade as Closed in Oracle when its GTT triggers — this frees up
    the category/stock-type budget since the views only sum status='Open'."""
    conn = get_oracle_connection()
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
        log(f"  Oracle trade closed for buy_order_id={buy_order_id} (rows updated: {cursor.rowcount})")
        cursor.close()
    except Exception as e:
        log(f"  Oracle close error: {e}")


if __name__ == '__main__':
    conn = get_oracle_connection()
    print("Connection:", "OK" if conn else "FAILED")
    if conn:
        print("RELIANCE cap type:", get_stock_cap_type('RELIANCE'))
        print("Big Gems category_id:", get_category_id('Big Gems'))
        ok, cid = check_budget_available('Big Gems', 'Large Cap', 5000)
        print(f"Budget check (Big Gems, Large Cap, 5000): ok={ok}, category_id={cid}")
        close_oracle_connection()
