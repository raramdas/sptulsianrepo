#!/usr/bin/env python3
"""
create_missing_gtts.py — one-off reconciliation script: places GTTs for
symbols where holdings/trades show a completed buy with no GTT yet placed.

REBUILT to work with THIS codebase's actual Kite auth (kite_client.py's
enctoken-based requests, not the kiteconnect SDK the original version used
— that version referenced a config.get_kite_client() that doesn't exist
here) and to fix a real correctness gap in the original's row-matching.

WHY THE ORIGINAL VERSION WAS UNSAFE
------------------------------------
gtt_orders_to_place.csv's quantities are AGGREGATE TOTALS per symbol (e.g.
IDEA: 16065 shares) — but your sheet routinely has MULTIPLE open rows for
the same symbol from separate buys (confirmed in your own gtt.log: three
separate "Vodafone Idea" rows, three "Zee Ent" rows, each with their own
GTT). The original script grabbed the FIRST matching open row and attached
the FULL aggregate quantity to it — which could double-count against GTTs
that already exist on the OTHER rows for that same symbol, or attempt to
sell more shares than that specific row's own position represents.

HOW THIS VERSION HANDLES IT
----------------------------
For each CSV symbol: find every open sheet row for that symbol that doesn't
already have a PLACED/RETRY GTT, and sum their My Buy Qty. Only proceed if
that sum EXACTLY matches the CSV's quantity — then place one correctly
sized GTT PER ROW (using that row's own qty), not one oversized GTT dumped
on a single row. If the sum doesn't match, the symbol is skipped and
flagged for manual reconciliation rather than guessed at.

This also automatically protects the known CDSL/ICDSLTD mixup: no sheet
row currently has Symbol='CDSL' exactly (it's sitting under the wrong
symbol ICDSLTD pending your manual fix), so CDSL's row-sum will be 0 versus
the CSV's 72 — a mismatch, and it gets skipped and flagged, not guessed at.

TARGET / TRIGGER CONVENTION
----------------------------
The CSV's "trigger_price" column is treated as the TARGET (limit sell
price) — consistent with the rest of this codebase, where the real Kite
trigger is always target - Rs 0.10 (kite_client.py's place_gtt, GTT_OFFSET).
This reuses that same, already-tested function, including its fix for
Kite's "trigger too close to last price" 0.25%-minimum-gap rule.

For last_price, this tries a live LTP first (get_market_price), falling
back to the CSV's ref_buy_price only if a live quote can't be fetched —
using a live price is safer than a potentially-stale historical buy price
for satisfying Kite's own last_price validation.

SAFETY
------
- Defaults to DRY RUN. Nothing reaches the broker without --live, and
  --live still requires typed confirmation before the first order goes out.
- Skips (and logs) any symbol whose sheet-row quantity sum doesn't exactly
  match the CSV quantity, rather than guessing which rows are covered.
- Skips rows flagged trade_history_complete=False unless --include-incomplete.
- Never touches a row that already has GTT_STATUS in {PLACED, RETRY}.

USAGE
-----
    python3 create_missing_gtts.py                     # dry run, all rows
    python3 create_missing_gtts.py --symbol APOLLO      # dry run, one symbol
    python3 create_missing_gtts.py --live               # place for real
    python3 create_missing_gtts.py --live --symbol IDEA # one symbol, live
"""
import argparse
import csv
import time

from config import log
from kite_client import get_enctoken, place_gtt, get_market_price
from sheet_gtt_updater import (
    get_worksheet,
    get_sheet_rows,
    set_gtt_placed,
    set_gtt_dry_run,
    set_error,
    COL_STOCK,
    COL_SYMBOL,
    COL_STATUS,
    COL_GTT_STATUS,
    COL_MY_BUY_QTY,
)

ORDERS_CSV = 'gtt_orders_to_place.csv'
ALREADY_HANDLED = {'PLACED', 'RETRY'}


def load_orders(csv_path, only_symbol=None, include_incomplete=False):
    orders = []
    with open(csv_path, newline='') as f:
        for row in csv.DictReader(f):
            if only_symbol and row['symbol'] != only_symbol:
                continue
            complete = row['trade_history_complete'].strip().lower() == 'true'
            if not complete and not include_incomplete:
                log(f"SKIP {row['symbol']}: trade_history_complete=False "
                    f"(pass --include-incomplete to override)")
                continue
            orders.append({
                'symbol': row['symbol'].strip().upper(),
                'quantity': int(float(row['quantity'])),
                'target_price': round(float(row['trigger_price']), 2),  # treated as TARGET, see header
                'ref_buy_price': round(float(row['ref_buy_price']), 2),
            })
    return orders


def find_matching_rows(rows, symbol):
    """Return 1-based row indices of ALL open rows for this symbol that
    don't already have a GTT placed — NOT just the first one, since a
    symbol can legitimately have several separate open positions."""
    matches = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) <= max(COL_SYMBOL, COL_STATUS, COL_GTT_STATUS, COL_MY_BUY_QTY):
            continue
        if row[COL_SYMBOL].strip().upper() != symbol:
            continue
        if row[COL_STATUS].strip().lower() != 'open':
            continue
        if row[COL_GTT_STATUS].strip().upper() in ALREADY_HANDLED:
            continue
        matches.append(i)
    return matches


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', action='store_true',
                     help='Actually place GTTs. Default is dry run.')
    ap.add_argument('--symbol', help='Restrict to a single symbol.')
    ap.add_argument('--include-incomplete', action='store_true',
                     help='Also process symbols flagged trade_history_complete=False.')
    ap.add_argument('--orders-csv', default=ORDERS_CSV)
    args = ap.parse_args()

    orders = load_orders(args.orders_csv, args.symbol, args.include_incomplete)
    if not orders:
        log('Nothing to place — no matching rows in orders CSV.')
        return

    print(f"\n{'LIVE' if args.live else 'DRY RUN'} — {len(orders)} symbol(s) queued for reconciliation:")
    for o in orders:
        print(f"  {o['symbol']:<12} total_qty={o['quantity']:<7} target={o['target_price']}")

    enctoken = None
    if args.live:
        confirm = input(f"\nType 'CONFIRM' to place real GTTs for these {len(orders)} symbol(s): ")
        if confirm.strip() != 'CONFIRM':
            print('Aborted — no orders placed.')
            return
        enctoken = get_enctoken()
        log("Kite enctoken obtained OK.")
    else:
        # Still need a real enctoken to fetch live LTPs even in dry run,
        # so the dry-run log shows realistic numbers.
        enctoken = get_enctoken()

    ws   = get_worksheet()
    rows = get_sheet_rows()

    placed, skipped, failed, mismatched = 0, 0, 0, 0

    for o in orders:
        symbol = o['symbol']
        row_indices = find_matching_rows(rows, symbol)
        sheet_qty_sum = 0
        for i in row_indices:
            try:
                sheet_qty_sum += int(float(rows[i - 1][COL_MY_BUY_QTY]))
            except (ValueError, IndexError):
                pass

        log(f"{symbol}: CSV qty={o['quantity']} | sheet rows found={len(row_indices)} "
            f"(sum={sheet_qty_sum})")

        if sheet_qty_sum != o['quantity']:
            log(f"  ⚠ MISMATCH — sheet quantity sum ({sheet_qty_sum}) does not exactly match "
                f"CSV quantity ({o['quantity']}). Skipping {symbol} entirely rather than "
                f"guessing which rows this covers. Reconcile manually.")
            mismatched += 1
            continue

        if not row_indices:
            log(f"  No open, un-GTT'd rows found for {symbol} — skipping")
            skipped += 1
            continue

        ltp = get_market_price(symbol.title(), enctoken, kite_symbol=symbol) or o['ref_buy_price']

        for i in row_indices:
            stock_name = rows[i - 1][COL_STOCK] if len(rows[i - 1]) > COL_STOCK else symbol
            qty = int(float(rows[i - 1][COL_MY_BUY_QTY]))

            if not args.live:
                log(f"  [DRY RUN] Row {i} ({stock_name}): would place GTT SELL "
                    f"{qty} x {symbol} @ target {o['target_price']} (ltp={ltp})")
                set_gtt_dry_run(i)
                placed += 1
                continue

            try:
                gtt_id = place_gtt(symbol, qty, o['target_price'], ltp, enctoken)
                set_gtt_placed(i, gtt_id)
                log(f"  PLACED row {i} ({stock_name}): {qty} x {symbol} @ target "
                    f"{o['target_price']} -> gtt_id={gtt_id}")
                placed += 1
                time.sleep(0.5)  # be polite to the API
            except Exception as e:
                set_error(i, f'GTT placement failed (reconciliation): {e}')
                log(f"  ERROR row {i} ({stock_name}): {e}")
                failed += 1

    print(f"\nDone. placed={placed} skipped={skipped} failed={failed} mismatched={mismatched} "
          f"mode={'LIVE' if args.live else 'DRY RUN'}")


if __name__ == '__main__':
    main()
