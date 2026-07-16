#!/usr/bin/env python3
"""
retry_failed_gtt_placements.py — one-off recovery script.

Finds rows from the last LOOKBACK_DAYS days where Status == 'ERROR' AND the
Notes field starts with "GTT failed" — the exact string set_error() records
when place_gtt() raises an exception in main_gtt.py's Phase 1. This
deliberately does NOT touch other ERROR rows (e.g. "Buy order REJECTED"),
since those failed for an unrelated reason and retrying a GTT placement for
them wouldn't make sense.

For each match, re-confirms the buy order is still COMPLETE, then retries
placing the GTT using the CURRENT (fixed) place_gtt() logic — this is the
whole point of running this now rather than waiting: recovering rows that
failed only because of the last_price/trigger_price bug, now that it's fixed.

On success: Status -> 'Open' (re-enters the normal Phase 2 GTT-monitoring
flow next time main_gtt.py runs), GTT ID/Status set, and the original error
is kept in Notes prefixed with "[RECOVERED ...]" for an audit trail rather
than being erased.

On failure again: Notes updated with the new error and a retry timestamp,
Status stays 'ERROR' for manual inspection.

Run directly:
    python3 retry_failed_gtt_placements.py
"""
from datetime import datetime, timedelta

from config import log, IST
from kite_client import get_enctoken, place_gtt, get_market_price, resolve_kite_symbol
from order_status import get_order_status
from sheet_gtt_updater import (
    get_sheet_rows, get_worksheet,
    COL_STOCK, COL_SYMBOL, COL_BUY_DATE, COL_MY_BUY_DATE, COL_STATUS, COL_TARGET,
    COL_BUY_OID, COL_GTT_ID, COL_GTT_STATUS, COL_MY_BUY_QTY, COL_NOTES,
)

DRY_RUN        = True   # Set to False for live — recommend running True first and reading the log
LOOKBACK_DAYS  = 3
FAILURE_MARKER = 'gtt failed'   # matches set_error()'s message for a place_gtt() exception, case-insensitive


def run():
    log("=== Retry Failed GTT Placements starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'} | Lookback: {LOOKBACK_DAYS} days")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    ws   = get_worksheet()
    rows = get_sheet_rows()
    now  = datetime.now(IST)
    cutoff_dates = {(now - timedelta(days=d)).strftime('%Y-%m-%d') for d in range(LOOKBACK_DAYS)}
    log(f"Scanning for buy dates in: {sorted(cutoff_dates)}")

    candidates = []
    for i, row in enumerate(rows[1:], start=2):
        status = row[COL_STATUS].strip()
        notes  = row[COL_NOTES].strip()
        buy_date = row[COL_MY_BUY_DATE].strip() or row[COL_BUY_DATE].strip()

        if status != 'ERROR':
            continue
        if not notes.lower().startswith(FAILURE_MARKER):
            continue
        if buy_date not in cutoff_dates:
            continue
        candidates.append(i)

    log(f"Found {len(candidates)} row(s) with a failed GTT placement in the last {LOOKBACK_DAYS} days")
    if not candidates:
        log("Nothing to retry.")
        return

    retried, succeeded, still_failed, skipped = 0, 0, 0, 0

    for i in candidates:
        row = rows[i - 1]
        stock      = row[COL_STOCK].strip()
        sheet_symbol = row[COL_SYMBOL].strip()
        target_raw = row[COL_TARGET].strip()
        buy_oid    = row[COL_BUY_OID].strip()
        qty_raw    = row[COL_MY_BUY_QTY].strip()
        old_notes  = row[COL_NOTES].strip()

        log(f"Row {i}: {stock}")

        try:
            target = float(target_raw.replace(',', ''))
        except ValueError:
            log(f"  Bad target '{target_raw}' — skipping")
            skipped += 1
            continue

        try:
            qty = int(float(qty_raw))
        except ValueError:
            log(f"  Bad qty '{qty_raw}' — skipping")
            skipped += 1
            continue

        if sheet_symbol:
            symbol = sheet_symbol
        else:
            symbol, sym_status = resolve_kite_symbol(stock, enctoken)
            if sym_status not in ('MANUAL', 'EXACT'):
                log(f"  Symbol resolution status={sym_status} — needs manual review, skipping")
                skipped += 1
                continue

        # Re-confirm the buy is still COMPLETE before retrying — belt and
        # braces, shouldn't have changed, but worth checking rather than
        # assuming the sheet is still accurate days later.
        order_info = get_order_status(buy_oid, enctoken, symbol_hint=symbol)
        if not order_info or order_info.get('status') != 'COMPLETE':
            log(f"  Buy order no longer confirms COMPLETE ({order_info}) — skipping")
            skipped += 1
            continue

        retried += 1
        ltp = get_market_price(stock, enctoken, kite_symbol=symbol)
        log(f"  symbol={symbol} | target={target} | qty={qty} | ltp={ltp}")

        if DRY_RUN:
            log(f"  [DRY RUN] Would retry GTT SELL {qty} x {symbol} @ target {target}")
            succeeded += 1
            continue

        try:
            new_gtt_id = place_gtt(symbol, qty, target, ltp, enctoken)
            ws.update_cell(i, COL_STATUS + 1, 'Open')
            ws.update_cell(i, COL_GTT_ID + 1, new_gtt_id)
            ws.update_cell(i, COL_GTT_STATUS + 1, 'PLACED')
            ws.update_cell(i, COL_NOTES + 1,
                f'[RECOVERED {now.strftime("%Y-%m-%d %H:%M")}] Previously: {old_notes}')
            log(f"  GTT placed successfully: {new_gtt_id}")
            succeeded += 1
        except Exception as e:
            log(f"  Retry failed again: {e}")
            ws.update_cell(i, COL_NOTES + 1,
                f'GTT failed (retry {now.strftime("%Y-%m-%d %H:%M")}): {e} | Previously: {old_notes}')
            still_failed += 1

    log(f"=== Retry complete | Candidates: {len(candidates)} | Retried: {retried} | "
        f"Succeeded: {succeeded} | Still failed: {still_failed} | Skipped: {skipped} ===")


if __name__ == '__main__':
    run()
