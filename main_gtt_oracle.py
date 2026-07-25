#!/usr/bin/env python3
"""
main_gtt_oracle.py — Oracle-only sell-side automation. This is a PARALLEL,
independent alternative to main_gtt.py (which reads targets from the Google
Sheet). This version reads and writes everything in Oracle, with targets set
via the dashboard's "Set Targets" page instead of the sheet.

Run BOTH main_gtt.py and main_gtt_oracle.py during the comparison period —
they operate on the same underlying Kite orders but track state in different
places (Sheet vs Oracle), so they won't double-place GTTs for the same trade
as long as targets are only entered in ONE of the two systems per trade.

Once you're confident in the Oracle-based flow, retire main_gtt.py and switch
your cron job to this script only.

Run directly:
    python3 main_gtt_oracle.py
"""
from datetime import datetime, timedelta

from config import log, GTT_DRY_RUN, IST
from kite_client import get_enctoken, place_gtt, get_gtt_status
from order_status import get_order_status
from budget_manager import (
    get_open_trades_with_target, get_open_trades_with_gtt,
    set_gtt_placed_oracle, mark_trade_error_oracle, close_trade_in_oracle,
    close_oracle_connection,
)


def run():
    log("=== GTT Automation (Oracle) starting ===")
    log(f"Mode: {'DRY RUN' if GTT_DRY_RUN else 'LIVE'}")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    now = datetime.now(IST)
    today = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    scan_dates = [today, yesterday]
    log(f"Scanning Oracle trades for dates: {scan_dates}")

    gtt_placed = gtt_skipped = gtt_closed = 0

    # ── Phase 1: place GTTs for filled buy orders with a target set ──
    candidates = get_open_trades_with_target(scan_dates)
    log(f"Found {len(candidates)} candidate trade(s) with target set, no GTT yet")

    for t in candidates:
        trade_id   = t['trade_id']
        stock      = t['stock_name']
        symbol     = t['symbol']
        target     = float(t['target_price'])
        buy_oid    = t['buy_order_id']
        sheet_qty  = t.get('my_buy_qty')

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
                set_gtt_placed_oracle(trade_id, 'DRY_RUN', dry_run=True)
                gtt_placed += 1
            else:
                try:
                    gtt_id = place_gtt(kite_symbol, filled_qty, target, None, enctoken)
                    log(f"  GTT placed: {gtt_id}")
                    set_gtt_placed_oracle(trade_id, gtt_id)
                    gtt_placed += 1
                except Exception as e:
                    log(f"  GTT error: {e}")
                    mark_trade_error_oracle(trade_id, str(e))
                    gtt_skipped += 1

        elif kite_status in ('OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'):
            log("  Limit order still open — skipping GTT")
            gtt_skipped += 1

        elif kite_status in ('REJECTED', 'CANCELLED'):
            log(f"  Order {kite_status} — marking ERROR")
            mark_trade_error_oracle(trade_id, f'Buy order {kite_status}')
            gtt_skipped += 1

        else:
            log(f"  Unknown status {kite_status} — skipping")
            gtt_skipped += 1

    # ── Phase 2: check existing GTTs for triggers -> mark Closed ──────
    active_gtts = get_open_trades_with_gtt()
    log(f"Checking {len(active_gtts)} active GTT(s) for triggers")

    for t in active_gtts:
        trade_id = t['trade_id']
        stock    = t['stock_name']
        gtt_id   = t['gtt_id']
        buy_oid  = t['buy_order_id']

        gtt_status = get_gtt_status(gtt_id, enctoken)
        log(f"Trade #{trade_id}: {stock} | GTT {gtt_id} status: {gtt_status}")

        if gtt_status in ('TRIGGERED', 'EXECUTED'):
            if GTT_DRY_RUN:
                log(f"  [DRY RUN] Would mark {stock} as Closed")
            else:
                close_trade_in_oracle(buy_oid, datetime.now(IST).date())
            gtt_closed += 1
            log(f"  {stock} marked Closed — GTT triggered, budget recycled")

    close_oracle_connection()
    log(f"=== GTT Automation (Oracle) complete | Placed: {gtt_placed} | "
        f"Skipped: {gtt_skipped} | Closed: {gtt_closed} ===")


if __name__ == '__main__':
    run()
