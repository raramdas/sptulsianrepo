#!/usr/bin/env python3
"""
main_gtt_oracle.py — multi-tenant GTT (sell-side) bot.

Same two-phase logic as the single-tenant version:
  Phase 1: place GTTs for filled buy orders that have a target price set
  Phase 2: check existing GTTs for triggers, mark trades Closed (recycles budget)

Loops over every active tenant, using each tenant's own Kite login and own
isolated Oracle schema. One tenant's failure doesn't stop the others.

Run directly:
    python3 main_gtt_oracle.py
"""
from datetime import datetime, timedelta

from config import log, GTT_DRY_RUN, IST
from kite_client import get_enctoken_for, place_gtt, get_gtt_status
from order_status import get_order_status
from budget_manager import (
    get_open_trades_with_target, get_open_trades_with_gtt,
    set_gtt_placed_oracle, mark_trade_error_oracle, close_trade_in_oracle,
)
from tenant_manager import get_active_tenants, open_tenant_connection


def run_for_tenant(tenant, conn):
    log(f"Logging into Kite as {tenant.kite_user_id}...")
    enctoken = get_enctoken_for(tenant.kite_user_id, tenant.kite_password, tenant.kite_totp_secret)
    log("Kite login OK.")

    now = datetime.now(IST)
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    scan_dates = [today, yesterday]

    gtt_placed = gtt_skipped = gtt_closed = 0

    # ── Phase 1: place GTTs for filled buy orders with a target set ──
    candidates = get_open_trades_with_target(conn, scan_dates)
    log(f"Found {len(candidates)} candidate trade(s) with target set, no GTT yet")

    for t in candidates:
        trade_id  = t['trade_id']
        stock     = t['stock_name']
        symbol    = t['symbol']
        target    = float(t['target_price'])
        buy_oid   = t['buy_order_id']
        sheet_qty = t.get('my_buy_qty')

        log(f"Trade #{trade_id}: {stock} | buy_oid={buy_oid} | target={target}")
        order_info = get_order_status(buy_oid, enctoken, symbol_hint=symbol)

        if not order_info:
            log("  Could not fetch order status — skipping")
            gtt_skipped += 1
            continue

        kite_status = order_info['status']
        filled_qty  = order_info['filled_qty']
        kite_symbol = symbol or order_info['symbol']
        log(f"  Kite status: {kite_status} | filled_qty: {filled_qty}")

        if order_info.get('from_holdings') and sheet_qty and 0 < int(sheet_qty) < filled_qty:
            log(f"  Capping GTT qty from holdings {filled_qty} to recorded buy qty {sheet_qty}")
            filled_qty = int(sheet_qty)

        if kite_status == 'COMPLETE' and filled_qty > 0:
            if not kite_symbol:
                log("  ERROR: symbol is empty — skipping GTT")
                gtt_skipped += 1
                continue
            if GTT_DRY_RUN:
                log(f"  [DRY RUN] Would place GTT SELL {filled_qty} x {kite_symbol} @ {target}")
                set_gtt_placed_oracle(conn, trade_id, 'DRY_RUN', dry_run=True)
                gtt_placed += 1
            else:
                try:
                    gtt_id = place_gtt(kite_symbol, filled_qty, target, None, enctoken)
                    log(f"  GTT placed: {gtt_id}")
                    set_gtt_placed_oracle(conn, trade_id, gtt_id)
                    gtt_placed += 1
                except Exception as e:
                    log(f"  GTT error: {e}")
                    mark_trade_error_oracle(conn, trade_id, str(e))
                    gtt_skipped += 1

        elif kite_status in ('OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'):
            log("  Limit order still open — skipping GTT")
            gtt_skipped += 1

        elif kite_status in ('REJECTED', 'CANCELLED'):
            log(f"  Order {kite_status} — marking ERROR")
            mark_trade_error_oracle(conn, trade_id, f'Buy order {kite_status}')
            gtt_skipped += 1

        else:
            log(f"  Unknown status {kite_status} — skipping")
            gtt_skipped += 1

    # ── Phase 2: check existing GTTs for triggers -> mark Closed ──────
    active_gtts = get_open_trades_with_gtt(conn)
    log(f"Checking {len(active_gtts)} active GTT(s) for triggers")

    for t in active_gtts:
        stock   = t['stock_name']
        gtt_id  = t['gtt_id']
        buy_oid = t['buy_order_id']

        gtt_status = get_gtt_status(gtt_id, enctoken)
        log(f"{stock} | GTT {gtt_id} status: {gtt_status}")

        if gtt_status in ('TRIGGERED', 'EXECUTED'):
            if GTT_DRY_RUN:
                log(f"  [DRY RUN] Would mark {stock} as Closed")
            else:
                close_trade_in_oracle(conn, buy_oid, datetime.now(IST).date())
            gtt_closed += 1
            log(f"  {stock} marked Closed — GTT triggered, budget recycled")

    return {'placed': gtt_placed, 'skipped': gtt_skipped, 'closed': gtt_closed}


def run():
    log("=== Multi-Tenant GTT Automation starting ===")
    log(f"Mode: {'DRY RUN' if GTT_DRY_RUN else 'LIVE'}")

    tenants, admin_conn = get_active_tenants()
    log(f"Active tenants: {[t.tenant_name for t in tenants]}")

    results = {}
    try:
        for tenant in tenants:
            log(f"\n=== Tenant: {tenant.tenant_name} ({tenant.db_username}) ===")
            tenant_conn = None
            try:
                tenant_conn = open_tenant_connection(admin_conn, tenant)
                results[tenant.tenant_name] = run_for_tenant(tenant, tenant_conn)
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
    log("=== GTT Automation complete ===")


if __name__ == '__main__':
    run()
