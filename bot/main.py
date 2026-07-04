#!/usr/bin/env python3
"""
main.py — multi-tenant buy bot.

Behavior is UNCHANGED from single-tenant v4: parse today's advisory emails,
and for each tip, check budget and buy immediately if it fits (no human
approval gate — that's a deliberate choice for now, see project notes).

What's NEW: this now loops over every active tenant (from tenant_config),
using each tenant's own Kite login and their own isolated Oracle schema.
Emails are parsed ONCE per run (single shared mailbox) and the same parsed
tips are then evaluated independently against each tenant's own budget.

One tenant's failure (bad Kite login, Oracle issue, etc.) is logged and
skipped — it never stops the other tenants' runs.

Run directly:
    python3 main.py
"""
import math

from config import log, DRY_RUN, TEST_DATE, INVEST_AMT
from kite_client import get_enctoken_for, resolve_kite_symbol, get_market_price, kite_buy
from email_reader import parse_todays_emails
from spt_scraper import scrape_spt_stock
from budget_manager import get_stock_cap_type, check_budget_available, insert_trade_to_oracle
from tenant_manager import get_active_tenants, open_tenant_connection


def process_tip_for_tenant(tip, enctoken, conn):
    """Same per-tip logic as single-tenant v4, but scoped to one tenant's
    own connection and enctoken. Mutates a COPY of tip so multiple tenants
    evaluating the same tip don't clobber each other's state."""
    tip = dict(tip)  # each tenant gets an independent copy

    spt = scrape_spt_stock(tip['stock'], tip.get('category', ''))
    tip['type']          = spt['type']
    tip['target']        = spt['target']
    tip['timeframe']     = spt['timeframe']
    tip['have_interest'] = spt['have_interest']
    tip['kite_symbol']   = resolve_kite_symbol(tip['stock'], enctoken)

    tip['cap_type'] = get_stock_cap_type(conn, tip['kite_symbol'])
    log(f"  Cap type for {tip['stock']} ({tip['kite_symbol']}): {tip['cap_type'] or 'UNKNOWN'}")

    # Price/qty/actual cost computed BEFORE the budget check, since a stock
    # priced above INVEST_AMT still buys a minimum of 1 share.
    tip['mkt_price'] = get_market_price(tip['stock'], enctoken)
    log(f"{tip['stock']} email:{tip['email_price']} market:{tip['mkt_price']}")
    if tip['mkt_price'] and tip['mkt_price'] < tip['email_price']:
        tip['buy_price']  = tip['mkt_price']
        tip['order_type'] = 'MARKET'
    else:
        tip['buy_price']  = tip['email_price']
        tip['order_type'] = 'LIMIT'
    tip['qty'] = max(1, math.floor(INVEST_AMT / tip['buy_price']))
    actual_cost = tip['qty'] * tip['buy_price']
    log(f"Qty: {tip['qty']} x {tip['stock']} @ {tip['buy_price']} ({tip['order_type']}) "
        f"| actual cost: Rs.{actual_cost:,.2f}")

    budget_ok, category_id = check_budget_available(conn, tip.get('category', ''), tip['cap_type'], actual_cost)
    if not budget_ok:
        tip['buy_status'] = 'SKIPPED'
        tip['note']       = 'Insufficient category/stock-type budget'
        log(f"SKIPPING {tip['stock']} — insufficient budget for actual cost Rs.{actual_cost:,.2f}")
        insert_trade_to_oracle(conn, tip, category_id)
        return

    if DRY_RUN:
        tip['buy_order_id'] = 'DRY_RUN'
        tip['buy_status']   = 'DRY_RUN'
        tip['note']         = 'DRY RUN'
        log(f"[DRY RUN] Would BUY {tip['qty']} x {tip['stock']} @ {tip['buy_price']}")
    else:
        buy = kite_buy(tip, enctoken)
        tip['buy_order_id'] = buy['order_id']
        tip['buy_status']   = 'PLACED'
        log(f"Buy placed: {tip['buy_order_id']}")

    insert_trade_to_oracle(conn, tip, category_id)


def run_for_tenant(tenant, conn, tips):
    log(f"Logging into Kite as {tenant.kite_user_id}...")
    enctoken = get_enctoken_for(tenant.kite_user_id, tenant.kite_password, tenant.kite_totp_secret)
    log("Kite login OK.")

    for tip in tips:
        try:
            process_tip_for_tenant(tip, enctoken, conn)
        except Exception as e:
            log(f"  ERROR processing {tip['stock']} for {tenant.tenant_name}: {e}")
            error_tip = dict(tip)
            error_tip['buy_status'] = 'ERROR'
            error_tip['note'] = str(e)
            insert_trade_to_oracle(conn, error_tip, None)

    return {'tips_processed': len(tips)}


def run():
    log("=== Multi-Tenant Stock Tip Bot starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    if TEST_DATE:
        log(f"TEST DATE override: {TEST_DATE}")

    # Parse the single shared advisory mailbox ONCE for this run
    tips = parse_todays_emails()
    if not tips:
        log("No tips found today. Nothing to do for any tenant.")
        return
    log(f"Tips found (shared across all tenants): {[t['stock'] for t in tips]}")

    tenants, admin_conn = get_active_tenants()
    log(f"Active tenants: {[t.tenant_name for t in tenants]}")

    results = {}
    try:
        for tenant in tenants:
            log(f"\n=== Tenant: {tenant.tenant_name} ({tenant.db_username}) ===")
            tenant_conn = None
            try:
                tenant_conn = open_tenant_connection(admin_conn, tenant)
                results[tenant.tenant_name] = run_for_tenant(tenant, tenant_conn, tips)
            except Exception as e:
                log(f"  ERROR for tenant {tenant.tenant_name}: {e}")
                results[tenant.tenant_name] = {'error': str(e)}
            finally:
                if tenant_conn:
                    tenant_conn.close()
    finally:
        admin_conn.close()

    log("\n=== Run summary ===")
    for name, result in results.items():
        log(f"  {name}: {result}")
    log("=== Automation complete ===")


if __name__ == '__main__':
    run()
