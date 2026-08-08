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
import re
from datetime import datetime, timedelta

from config import log, GTT_DRY_RUN, IST
from kite_client import get_enctoken, place_gtt, get_gtt_detail
from order_status import get_order_status, find_sell_order_for_symbol
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
            if GTT_DRY_RUN:
                log(f"  [DRY RUN] Order {kite_status} — would mark ERROR")
            else:
                log(f"  Order {kite_status} — marking ERROR")
                mark_trade_error_oracle(trade_id, f'Buy order {kite_status}')
            gtt_skipped += 1

        else:
            log(f"  Unknown status {kite_status} — skipping")
            gtt_skipped += 1

    # ── Phase 2: check existing GTTs. A GTT can leave 'active' state three
    # ways: it triggered AND the sell filled (real close), it triggered but
    # the DAY-validity sell order never filled, or it was cancelled outright.
    # Only the first case should close the row — the other two mean nothing
    # was actually sold, and get a fresh GTT at the same target instead of
    # silently sitting there. (Ported from main_gtt.py's Phase 2, which had
    # this check; this script originally didn't and would have wrongly
    # closed+recycled the budget for any GTT that triggered without a fill.)
    active_gtts = get_open_trades_with_gtt()
    log(f"Checking {len(active_gtts)} active GTT(s) for triggers")

    gtt_recreated = 0
    for t in active_gtts:
        trade_id = t['trade_id']
        stock    = t['stock_name']
        symbol   = t['symbol']
        gtt_id   = t['gtt_id']
        buy_oid  = t['buy_order_id']
        target   = t.get('target_price')
        my_qty   = t.get('my_buy_qty')
        notes    = t.get('notes') or ''

        detail = get_gtt_detail(gtt_id, enctoken)
        if not detail:
            log(f"Trade #{trade_id}: {stock} | GTT {gtt_id} — could not fetch detail, skipping")
            gtt_skipped += 1
            continue

        gtt_st = (detail.get('status') or '').upper()
        log(f"Trade #{trade_id}: {stock} | GTT {gtt_id} status: {gtt_st}")

        if gtt_st == 'ACTIVE':
            continue  # still waiting, nothing to do

        cond = detail.get('condition', {})
        sell_symbol = symbol or cond.get('tradingsymbol') or stock.upper().replace(' ', '')
        try:
            qty = int(float(my_qty)) if my_qty else None
        except (TypeError, ValueError):
            qty = None

        # Look for the actual resulting sell order, rather than trusting
        # the GTT's own status alone.
        filled_order = None
        for o in detail.get('orders', []):
            result = o.get('result')
            if result and result.get('order_id'):
                filled_order = get_order_status(result['order_id'], enctoken)
                break
        if not filled_order and qty and sell_symbol:
            filled_order = find_sell_order_for_symbol(sell_symbol, qty, enctoken)

        sold_ok = bool(filled_order and filled_order['status'] == 'COMPLETE'
                        and filled_order['filled_qty'] > 0)

        if sold_ok:
            if GTT_DRY_RUN:
                log(f"  [DRY RUN] Would mark {stock} as Closed — sold "
                    f"{filled_order['filled_qty']} @ {filled_order.get('avg_price')}")
            else:
                close_trade_in_oracle(buy_oid, datetime.now(IST).date(),
                                      sell_price=filled_order.get('avg_price'),
                                      sell_qty=filled_order['filled_qty'])
            gtt_closed += 1
            log(f"  {stock} marked Closed — GTT {gtt_st}, sell CONFIRMED filled "
                f"{filled_order['filled_qty']} @ {filled_order.get('avg_price')}")
        else:
            # GTT ended (triggered without a fill, cancelled, deleted, or
            # expired) with nothing actually sold — recreate at the same
            # target price rather than leaving this to be redone by hand.
            if not qty or not target or not sell_symbol:
                log(f"  Cannot recreate GTT for trade #{trade_id} — missing qty/target/symbol")
                gtt_skipped += 1
                continue
            m = re.search(r'Retry #(\d+)', notes)
            new_retry = int(m.group(1)) + 1 if m else 1
            note = f'Retry #{new_retry}: GTT {gtt_st.lower()} without a confirmed fill'
            log(f"  GTT {gtt_st} with NO confirmed sell — recreating ({note})")
            if GTT_DRY_RUN:
                log(f"  [DRY RUN] Would recreate GTT SELL {qty} x {sell_symbol} @ {target}")
            else:
                try:
                    new_gtt_id = place_gtt(sell_symbol, qty, float(target), None, enctoken)
                    set_gtt_placed_oracle(trade_id, new_gtt_id, note=note)
                    log(f"  New GTT placed: {new_gtt_id}")
                except Exception as e:
                    log(f"  GTT recreate error: {e}")
                    mark_trade_error_oracle(trade_id, str(e))
            gtt_recreated += 1

    close_oracle_connection()
    log(f"=== GTT Automation (Oracle) complete | Placed: {gtt_placed} | "
        f"Skipped: {gtt_skipped} | Closed: {gtt_closed} | Recreated: {gtt_recreated} ===")


if __name__ == '__main__':
    run()
