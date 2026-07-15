#!/usr/bin/env python3
"""
main_gtt.py — sell-side automation. Runs a few hours after the buy bot
(e.g. 2:00 PM IST) to:
  Phase 1: For Open rows with a Target set and no GTT yet, check the buy
           order's fill status and place a GTT sell at target if filled.
  Phase 2: For rows with a GTT already placed, check if it has triggered;
           if so, mark the row Closed (and close the matching Oracle trade
           so category/stock-type budget recycles).

Run directly:
    python3 main_gtt.py
"""
from datetime import datetime, timedelta

from config import log, GTT_DRY_RUN, IST
from kite_client import get_enctoken, resolve_kite_symbol, place_gtt, get_gtt_status, get_gtt_detail
from order_status import get_order_status, find_sell_order_for_symbol
from budget_manager import close_trade_in_oracle, close_oracle_connection
from sheet_gtt_updater import (
    get_sheet_rows, set_gtt_placed, set_gtt_dry_run, set_error, set_closed, set_gtt_recreated,
    COL_STOCK, COL_SYMBOL, COL_BUY_DATE, COL_STATUS, COL_TARGET, COL_BUY_OID,
    COL_GTT_ID, COL_MY_BUY_QTY, COL_RETRY_CNT,
)


def resolve_symbol_hint(stock_name):
    """Best-effort resolve of a sheet stock name to a Kite symbol for holdings
    lookup only (not for placing trades) — a wrong hint here just means the
    holdings-fallback lookup misses, it doesn't cause a wrong buy/sell."""
    sym, _status = resolve_kite_symbol(stock_name)
    return sym


def run():
    log("=== GTT Automation starting ===")
    log(f"Mode: {'DRY RUN' if GTT_DRY_RUN else 'LIVE'}")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    rows = get_sheet_rows()

    now       = datetime.now(IST)
    today     = now.strftime('%Y-%m-%d')
    yesterday = (now - timedelta(days=1)).strftime('%Y-%m-%d')
    scan_dates = {today, yesterday}
    log(f"Scanning sheet for IST dates: {today} or {yesterday}")

    gtt_placed  = 0
    gtt_skipped = 0
    gtt_closed  = 0
    gtt_recreated = 0

    for i, row in enumerate(rows[1:], start=2):  # row 2 = first data row (1-indexed for gspread)
        buy_date   = row[COL_BUY_DATE].strip()
        stock      = row[COL_STOCK].strip()
        symbol     = row[COL_SYMBOL].strip()
        status     = row[COL_STATUS].strip()
        target_raw = row[COL_TARGET].strip()
        buy_oid    = row[COL_BUY_OID].strip()
        gtt_id     = row[COL_GTT_ID].strip()
        my_buy_qty = row[COL_MY_BUY_QTY].strip()

        try:
            target = float(target_raw.replace(',', '')) if target_raw else None
        except ValueError:
            target = None

        # ── Phase 1: Place GTT for completed buy orders ──────────────
        date_match = buy_date in scan_dates
        if (date_match and status == 'Open' and target and target > 0
                and buy_oid and buy_oid not in ('', 'DRY_RUN') and not gtt_id):

            log(f"Row {i}: {stock} | buy_oid={buy_oid} | target={target}")
            order_info = get_order_status(buy_oid, enctoken, symbol_hint=(symbol or resolve_symbol_hint(stock)))

            if not order_info:
                log("  Could not fetch order status — skipping")
                gtt_skipped += 1
                continue

            kite_status = order_info['status']
            filled_qty  = order_info['filled_qty']
            kite_symbol = symbol or order_info['symbol'] or stock.upper().replace(' ', '')
            log(f"  Kite status: {kite_status} | filled_qty: {filled_qty}")

            # Cap qty to sheet's recorded buy qty if fill was confirmed via holdings
            if order_info.get('from_holdings'):
                try:
                    sheet_qty = int(float(my_buy_qty)) if my_buy_qty else None
                except Exception:
                    sheet_qty = None
                if sheet_qty and 0 < sheet_qty < filled_qty:
                    log(f"  Capping GTT qty from holdings {filled_qty} to sheet buy qty {sheet_qty}")
                    filled_qty = sheet_qty

            if kite_status == 'COMPLETE' and filled_qty > 0:
                if not kite_symbol:
                    log("  ERROR: kite_symbol is empty — skipping GTT")
                    gtt_skipped += 1
                    continue
                if GTT_DRY_RUN:
                    log(f"  [DRY RUN] Would place GTT SELL {filled_qty} x {kite_symbol} @ {target}")
                    set_gtt_dry_run(i)
                    gtt_placed += 1
                else:
                    try:
                        gtt_trigger_id = place_gtt(kite_symbol, filled_qty, target, None, enctoken)
                        log(f"  GTT placed: {gtt_trigger_id}")
                        set_gtt_placed(i, gtt_trigger_id)
                        gtt_placed += 1
                    except Exception as gte:
                        log(f"  GTT error: {gte}")
                        set_error(i, str(gte))
                        gtt_skipped += 1

            elif kite_status in ('OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'):
                log("  Limit order still open — skipping GTT")
                gtt_skipped += 1

            elif kite_status in ('REJECTED', 'CANCELLED'):
                log(f"  Order {kite_status} — marking ERROR")
                set_error(i, f'Buy order {kite_status}')
                gtt_skipped += 1

            else:
                log(f"  Unknown status {kite_status} — skipping")
                gtt_skipped += 1

        # ── Phase 2: Check existing GTTs. A GTT can leave 'active' state
        # three ways: it triggered AND the sell filled (real close), it
        # triggered but the DAY-validity sell order never filled, or it was
        # cancelled outright. Only the first case should close the row —
        # the other two mean nothing was actually sold, and get a fresh GTT
        # at the same target instead of silently sitting there. ───────────
        elif status == 'Open' and gtt_id and gtt_id not in ('', 'DRY_RUN'):
            detail = get_gtt_detail(gtt_id, enctoken)
            if not detail:
                log(f"Row {i}: {stock} | GTT {gtt_id} — could not fetch detail, skipping")
                gtt_skipped += 1
                continue

            gtt_st = detail.get('status', '').upper()
            log(f"Row {i}: {stock} | GTT {gtt_id} status: {gtt_st}")

            if gtt_st == 'ACTIVE':
                continue  # still waiting, nothing to do

            cond = detail.get('condition', {})
            sell_symbol = symbol or cond.get('tradingsymbol') or stock.upper().replace(' ', '')
            try:
                qty = int(float(my_buy_qty)) if my_buy_qty else None
            except Exception:
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
                today_str = datetime.now(IST).strftime('%Y-%m-%d')
                if GTT_DRY_RUN:
                    log(f"  [DRY RUN] Would mark {stock} as Closed — sold "
                        f"{filled_order['filled_qty']} @ {filled_order.get('avg_price')}")
                else:
                    set_closed(i, today_str)
                    close_trade_in_oracle(buy_oid, datetime.now(IST).date())
                gtt_closed += 1
                log(f"  {stock} marked Closed — GTT {gtt_st}, sell CONFIRMED filled")
            else:
                # GTT ended (triggered without a fill, cancelled, deleted, or
                # expired) with nothing actually sold — recreate at the same
                # target price rather than leaving this to be redone by hand.
                if not qty or not target or not sell_symbol:
                    log(f"  Cannot recreate GTT for row {i} — missing qty/target/symbol")
                    gtt_skipped += 1
                    continue
                try:
                    retry_cnt = int(float(row[COL_RETRY_CNT])) if row[COL_RETRY_CNT] else 0
                except Exception:
                    retry_cnt = 0
                new_retry = retry_cnt + 1
                log(f"  GTT {gtt_st} with NO confirmed sell — recreating (retry #{new_retry})")
                if GTT_DRY_RUN:
                    log(f"  [DRY RUN] Would recreate GTT SELL {qty} x {sell_symbol} @ {target}")
                else:
                    try:
                        new_gtt_id = place_gtt(sell_symbol, qty, target, None, enctoken)
                        set_gtt_recreated(i, new_gtt_id, new_retry, f'GTT {gtt_st.lower()} without fill')
                        log(f"  New GTT placed: {new_gtt_id}")
                    except Exception as gte:
                        log(f"  GTT recreate error: {gte}")
                        set_error(i, str(gte))
                gtt_recreated += 1

    close_oracle_connection()
    log(f"=== GTT Automation complete | Placed: {gtt_placed} | Skipped: {gtt_skipped} | "
        f"Closed: {gtt_closed} | Recreated: {gtt_recreated} ===")


if __name__ == '__main__':
    run()
