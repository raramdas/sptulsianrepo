#!/usr/bin/env python3
"""
create_missing_gtts.py — one-off reconciliation script, v3.

Works against a "Reconciliation_Report" CSV with columns:
  Symbol, Qty_Held, GTT_Qty_Placed, Uncovered_Qty, n_lots,
  Blended_Avg_Buy_Price, Suggested_Blended_Target, Implied_Gain_Pct,
  Trade_History_Complete

Unlike the earlier version, this recon ALREADY accounts for existing GTTs
(GTT_Qty_Placed) and gives us the exact residual needing a new GTT
(Uncovered_Qty) — so there's no more guessing which sheet row(s) a quantity
belongs to. We proved last run that most of these symbols (n_lots often in
the double digits) have little or no representation in the Master Database
sheet at all — most of this trading history lives only in Kite, not the
sheet.

WHAT THIS DOES
--------------
For each symbol with Uncovered_Qty > 0:
  1. Places ONE GTT for Uncovered_Qty @ Suggested_Blended_Target (via
     kite_client.place_gtt — same tested function used everywhere else in
     this codebase, trigger = target - Rs 0.10, with the 0.25%-gap fix).
  2. APPENDS ONE NEW row to the sheet recording this consolidated position
     — deliberately does NOT try to match/update any pre-existing rows for
     that symbol, to avoid the ambiguity that broke the previous approach.
     This is what lets main_gtt.py's regular Phase 2 monitoring (and its
     recreate-on-cancel logic) pick this GTT up going forward.

SAFETY
------
- Defaults to DRY RUN. --live requires typed confirmation first.
- Skips Uncovered_Qty <= 0 (already fully covered).
- Skips Trade_History_Complete=FALSE rows unless --include-incomplete.
- Logs GTT_Qty_Placed / n_lots for every symbol for visibility, but does
  NOT attempt to verify or touch whatever GTTs already exist — trusts the
  recon report's accounting of that, per your confirmation recon is done.

USAGE
-----
    python3 create_missing_gtts.py                       # dry run, all rows
    python3 create_missing_gtts.py --symbol IDEA          # dry run, one symbol
    python3 create_missing_gtts.py --live                 # place for real
    python3 create_missing_gtts.py --live --symbol IDEA   # one symbol, live
"""
import argparse
import csv
import time
from datetime import datetime

from config import log, IST
from kite_client import get_enctoken, place_gtt, get_market_price, kite_headers
from budget_manager import get_stock_cap_type, close_oracle_connection
from sheet_gtt_updater import get_worksheet
import requests

ORDERS_CSV = 'reconciliation_report.csv'
OMS_BASE = 'https://kite.zerodha.com/oms'
EXCHANGE = 'NSE'


def kite_sell_amo_limit(symbol, qty, limit_price, enctoken):
    """Places an AMO (After Market Order) LIMIT sell — used for positions
    already past target, where waiting on a GTT trigger doesn't make sense
    since the price condition is already met. AMO rather than a regular
    order since this script is often run outside market hours; AMO queues
    it for the next session instead of getting rejected outright."""
    payload = {
        'exchange': EXCHANGE, 'tradingsymbol': symbol,
        'transaction_type': 'SELL', 'quantity': qty,
        'order_type': 'LIMIT', 'product': 'CNC',
        'validity': 'DAY', 'price': limit_price, 'tag': 'RECON_SELL',
    }
    r = requests.post(f'{OMS_BASE}/orders/amo', headers=kite_headers(enctoken), data=payload)
    res = r.json()
    if res.get('status') != 'success':
        raise RuntimeError(f"AMO sell failed: {res.get('message')}")
    return res['data']['order_id']


def load_orders(csv_path, only_symbol=None, include_incomplete=False):
    orders = []
    with open(csv_path, newline='') as f:
        for row in csv.DictReader(f):
            symbol = row['Symbol'].strip().upper()
            if only_symbol and symbol != only_symbol.strip().upper():
                continue
            complete = row['Trade_History_Complete'].strip().upper() == 'TRUE'
            if not complete and not include_incomplete:
                log(f"SKIP {symbol}: Trade_History_Complete=FALSE "
                    f"(pass --include-incomplete to override)")
                continue
            uncovered = int(float(row['Uncovered_Qty']))
            if uncovered <= 0:
                log(f"SKIP {symbol}: Uncovered_Qty={uncovered} — already fully covered")
                continue
            orders.append({
                'symbol': symbol,
                'qty_held': int(float(row['Qty_Held'])),
                'gtt_qty_placed': int(float(row['GTT_Qty_Placed'])),
                'uncovered_qty': uncovered,
                'n_lots': int(float(row['n_lots'])),
                'blended_avg_buy_price': round(float(row['Blended_Avg_Buy_Price']), 2),
                'target_price': round(float(row['Suggested_Blended_Target']), 2),
                'implied_gain_pct': row['Implied_Gain_Pct'],
            })
    return orders


def append_pending_sell_row(ws, o, order_id, sell_price, cap_type, today_str):
    ws.append_row([
        'Reconciliation',                # A: Category
        o['symbol'],                     # B: Stock
        o['symbol'],                     # C: Symbol
        cap_type or '',                  # D: Type
        today_str,                       # E: Buy Date
        o['blended_avg_buy_price'],      # F: Recommended Price
        o['target_price'],               # G: Target
        '',                              # H: Timeframe
        '',                              # I: Have Interest
        'Open',                          # J: Status — stays Open until fill is confirmed
        '', '', '',                      # K,L,M: Target Met / Exit Date / Gain
        today_str,                       # N: My Buy Date
        'RECONCILED',                    # O: Order Type
        'RECONCILED',                    # P: Buy Order ID
        o['blended_avg_buy_price'],      # Q: Market Price at Buy
        o['blended_avg_buy_price'],      # R: My Buy Price
        o['uncovered_qty'],              # S: My Buy Qty
        '', '', '', '',                  # T,U,V,W: Sell Date/Price/Qty/Gain-Loss — blank until fill confirmed
        '', '',                          # X,Y: GTT ID / GTT Status — n/a, this was a direct sell not a GTT
        f"PENDING SELL: AMO LIMIT order_id={order_id} @ Rs.{sell_price} for "
        f"{o['uncovered_qty']} shares, placed {today_str} because LTP was already "
        f"past target ({o['target_price']}). NOT yet confirmed filled — "
        f"check Kite app / order book and update Status/Sell fields manually once confirmed.",
        '',                              # AA: Retry Count
    ])


def append_reconciliation_row(ws, o, gtt_id, gtt_status, cap_type, today_str):
    ws.append_row([
        'Reconciliation',                # A: Category
        o['symbol'],                     # B: Stock
        o['symbol'],                     # C: Symbol
        cap_type or '',                  # D: Type
        today_str,                       # E: Buy Date
        o['blended_avg_buy_price'],      # F: Recommended Price
        o['target_price'],               # G: Target
        '',                              # H: Timeframe
        '',                              # I: Have Interest
        'Open',                          # J: Status
        '', '', '',                      # K,L,M: Target Met / Exit Date / Gain
        today_str,                       # N: My Buy Date
        'RECONCILED',                    # O: Order Type
        'RECONCILED',                    # P: Buy Order ID
        o['blended_avg_buy_price'],      # Q: Market Price at Buy
        o['blended_avg_buy_price'],       # R: My Buy Price
        o['uncovered_qty'],              # S: My Buy Qty
        '', '', '', '',                  # T,U,V,W: Sell Date/Price/Qty/Gain-Loss
        gtt_id,                          # X: GTT ID
        gtt_status,                      # Y: GTT Status
        f"Reconciliation: {o['n_lots']} lots held, "
        f"{o['qty_held']} total qty, {o['gtt_qty_placed']} already GTT-covered, "
        f"{o['uncovered_qty']} newly covered here (implied gain {o['implied_gain_pct']}%)",
        '',                              # AA: Retry Count
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live', action='store_true', help='Actually place GTTs. Default is dry run.')
    ap.add_argument('--symbol', help='Restrict to a single symbol.')
    ap.add_argument('--include-incomplete', action='store_true',
                     help='Also process symbols flagged Trade_History_Complete=FALSE.')
    ap.add_argument('--orders-csv', default=ORDERS_CSV)
    args = ap.parse_args()

    orders = load_orders(args.orders_csv, args.symbol, args.include_incomplete)
    if not orders:
        log('Nothing to place.')
        return

    print(f"\n{'LIVE' if args.live else 'DRY RUN'} — {len(orders)} symbol(s) queued:")
    for o in orders:
        print(f"  {o['symbol']:<12} uncovered={o['uncovered_qty']:<7} "
              f"(held={o['qty_held']}, already_gtt={o['gtt_qty_placed']}, "
              f"lots={o['n_lots']}) target={o['target_price']}")

    if args.live:
        confirm = input(f"\nType 'CONFIRM' to place real GTTs (or immediate sells, for symbols "
                        f"already past target) for these {len(orders)} symbol(s): ")
        if confirm.strip() != 'CONFIRM':
            print('Aborted — no orders placed.')
            return

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")
    ws = get_worksheet()
    today_str = datetime.now(IST).strftime('%Y-%m-%d')

    placed, failed, sold_past_target = 0, 0, 0

    for o in orders:
        symbol = o['symbol']
        ltp = get_market_price(symbol.title(), enctoken, kite_symbol=symbol) or o['blended_avg_buy_price']
        cap_type = get_stock_cap_type(symbol)

        log(f"{symbol}: uncovered_qty={o['uncovered_qty']} target={o['target_price']} "
            f"ltp={ltp} cap_type={cap_type or 'UNKNOWN'}")

        # If the current price is already at/above target, this isn't a
        # future condition to wait for — the position's already there (or
        # past it). A GTT waiting to trigger doesn't make sense here, so
        # place an immediate LIMIT sell (via AMO, since this script often
        # runs outside market hours) locking in at least the current price,
        # instead of a GTT that relies on our own last_price safety-margin
        # logic silently masking the fact the price condition is already met.
        if ltp and ltp >= o['target_price']:
            pct_past = ((ltp - o['target_price']) / o['target_price']) * 100
            log(f"  LTP already {pct_past:.1f}% past target — placing an immediate "
                f"LIMIT sell @ {ltp} instead of a GTT")

            if not args.live:
                log(f"  [DRY RUN] Would place AMO LIMIT SELL {o['uncovered_qty']} x {symbol} "
                    f"@ {ltp}, then append a pending sheet row (Status stays Open until confirmed filled)")
                sold_past_target += 1
                continue

            try:
                order_id = kite_sell_amo_limit(symbol, o['uncovered_qty'], ltp, enctoken)
                append_pending_sell_row(ws, o, order_id, ltp, cap_type, today_str)
                log(f"  SELL PLACED (AMO): {symbol} order_id={order_id} @ {ltp} — "
                    f"pending fill, sheet row added with Status=Open, verify manually")
                sold_past_target += 1
                time.sleep(0.5)
            except Exception as e:
                log(f"  ERROR placing sell for {symbol}: {e}")
                failed += 1
            continue

        if not args.live:
            log(f"  [DRY RUN] Would place GTT SELL {o['uncovered_qty']} x {symbol} "
                f"@ target {o['target_price']}, then append a new tracked sheet row")
            placed += 1
            continue

        try:
            gtt_id = place_gtt(symbol, o['uncovered_qty'], o['target_price'], ltp, enctoken)
            append_reconciliation_row(ws, o, gtt_id, 'PLACED', cap_type, today_str)
            log(f"  PLACED: {symbol} gtt_id={gtt_id} — new sheet row appended")
            placed += 1
            time.sleep(0.5)
        except Exception as e:
            log(f"  ERROR {symbol}: {e}")
            failed += 1

    close_oracle_connection()
    print(f"\nDone. placed={placed} failed={failed} sold_past_target={sold_past_target} "
          f"mode={'LIVE' if args.live else 'DRY RUN'}")


if __name__ == '__main__':
    main()
