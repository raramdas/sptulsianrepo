#!/usr/bin/env python3
# purchase_bot.py  (Bot B)
# Run every 30 min (e.g. 10:15 AM - 3 PM IST). It always re-reads the sheet
# fresh, so it can never miss a newly-filled-in row — but only pays the cost
# of a full Kite login when there's actually something actionable to buy.
# When there's nothing to do, it logs that and exits immediately instead of
# doing a pointless login every 30 minutes.
#
# For each Open row with no Buy Order ID yet:
#   - Skips if Have Interest (col I) is still blank — waiting on manual entry.
#   - Investment amount: Yes -> INVEST_INTEREST, No -> INVEST_NO_INTEREST.
#   - Resolves the trading symbol STRICTLY (Symbol column if filled, else
#     manual map or exact instrument match only). Ambiguous/unresolved names
#     are marked NEEDS_REVIEW and skipped — never auto-bought on a guess.
#
# Rows recommended on an EARLIER day that are still sitting with blank Have
# Interest get a one-time "stale" note added to Notes, so it's obvious on
# your next look at the sheet that something was left unbought.

import math
from datetime import datetime

from kite_common import (
    log, get_sheet, get_enctoken, get_ltp, resolve_symbol_strict, kite_headers,
    OMS_BASE, EXCHANGE, IST, pad_row,
    COL_STOCK, COL_SYMBOL, COL_TARGET, COL_INTEREST, COL_STATUS, COL_BUY_OID,
    COL_MY_BUY_DATE, COL_ORDER_TYPE, COL_MKT_PRICE, COL_MY_BUY_PX,
    COL_MY_BUY_QTY, COL_REC_PRICE, COL_NOTES, COL_BUY_DATE, NUM_COLS,
)
import requests

DRY_RUN             = True    # Set to False for live
INVEST_INTEREST     = 10000   # Have Interest = Yes
INVEST_NO_INTEREST  = 2500    # Have Interest = No
STALE_FLAG_MARKER   = 'STALE-UNBOUGHT'  # substring used to avoid re-flagging the same row every run


def kite_buy(symbol, qty, order_type, price, enctoken):
    payload = {
        'exchange': EXCHANGE, 'tradingsymbol': symbol,
        'transaction_type': 'BUY', 'quantity': qty,
        'order_type': order_type, 'product': 'CNC',
        'validity': 'DAY', 'tag': 'SPT'
    }
    if order_type == 'LIMIT':
        payload['price'] = price
    r = requests.post(f'{OMS_BASE}/orders/regular',
        headers=kite_headers(enctoken), data=payload)
    res = r.json()
    if res.get('status') != 'success':
        raise RuntimeError(f"Buy failed: {res.get('message')}")
    return res['data']


def run():
    log("=== Purchase Bot starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")

    ws   = get_sheet()
    rows = ws.get_all_values()
    today_str = datetime.now(IST).strftime('%Y-%m-%d')

    # ── Cheap first pass: no Kite login yet. Figure out what's actionable
    # (Open, no buy_oid, Have Interest filled) vs. what's stale (Open, no
    # buy_oid, blank Have Interest, recommended on an EARLIER day). ────────
    actionable_rows = []
    stale_flagged = 0

    for i, row in enumerate(rows[1:], start=2):
        row = pad_row(row)
        status    = row[COL_STATUS].strip()
        interest  = row[COL_INTEREST].strip().lower()
        buy_oid   = row[COL_BUY_OID].strip()
        buy_date  = row[COL_BUY_DATE].strip()
        notes     = row[COL_NOTES].strip()
        stock     = row[COL_STOCK].strip()

        if status != 'Open' or buy_oid:
            continue

        if interest in ('yes', 'no'):
            actionable_rows.append(i)
            continue

        # Still blank. If it's from an earlier day (not today's fresh batch),
        # flag it once so it's obvious something was left unbought.
        if buy_date and buy_date < today_str and STALE_FLAG_MARKER not in notes:
            note = f'[{STALE_FLAG_MARKER}] Have Interest never filled in (recommended {buy_date}) — flagged {today_str}'
            log(f"Row {i}: {stock} — stale, recommended {buy_date}, still no Have Interest. Flagging.")
            if not DRY_RUN:
                ws.update_cell(i, COL_NOTES + 1, (notes + ' | ' if notes else '') + note)
            stale_flagged += 1

    if not actionable_rows:
        log(f"=== Nothing actionable this run (no Kite login needed) | Stale flagged: {stale_flagged} ===")
        return

    log(f"Found {len(actionable_rows)} actionable row(s) — logging into Kite")
    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    bought, skipped, flagged = 0, 0, 0

    for i in actionable_rows:
        row = pad_row(rows[i - 1])

        stock     = row[COL_STOCK].strip()
        sheet_symbol = row[COL_SYMBOL].strip()
        interest  = row[COL_INTEREST].strip().lower()
        rec_price = row[COL_REC_PRICE].strip()

        try:
            email_price = float(str(rec_price).replace(',', ''))
        except ValueError:
            log(f"Row {i}: {stock} — bad recommended price '{rec_price}', skipping")
            skipped += 1
            continue

        invest_amt = INVEST_INTEREST if interest == 'yes' else INVEST_NO_INTEREST
        log(f"Row {i}: {stock} | interest={interest} | invest={invest_amt}")

        if sheet_symbol:
            symbol = sheet_symbol
            log(f"  Using Symbol column as-is: {symbol}  "
                f"(double-check this manually if you're not sure it's right)")
        else:
            symbol, match_status = resolve_symbol_strict(stock, enctoken)
            if match_status not in ('MANUAL', 'EXACT'):
                log(f"  Symbol resolution: {match_status} — flagging NEEDS_REVIEW, NOT buying")
                if not DRY_RUN:
                    ws.update_cell(i, COL_STATUS + 1, 'NEEDS_REVIEW')
                    ws.update_cell(i, COL_NOTES + 1,
                        f'Symbol {match_status} for "{stock}" — fill in Symbol column manually, '
                        f'or add to SYMBOL_MAP in kite_common.py')
                flagged += 1
                continue
            log(f"  Symbol resolved ({match_status}): {symbol}")
            if not DRY_RUN:
                ws.update_cell(i, COL_SYMBOL + 1, symbol)  # record it for next time / audit

        mkt_price = get_ltp(symbol, enctoken)
        if mkt_price and mkt_price < email_price:
            buy_price  = mkt_price
            order_type = 'MARKET'
        else:
            buy_price  = email_price
            order_type = 'LIMIT'

        qty = max(1, math.floor(invest_amt / buy_price))
        log(f"  Qty: {qty} x {symbol} @ {buy_price} ({order_type})")

        if DRY_RUN:
            log(f"  [DRY RUN] Would BUY {qty} x {symbol} @ {buy_price}")
        else:
            buy = kite_buy(symbol, qty, order_type, buy_price, enctoken)
            order_id = buy['order_id']
            log(f"  Buy placed: {order_id}")
            ws.update_cell(i, COL_MY_BUY_DATE + 1, today_str)
            ws.update_cell(i, COL_ORDER_TYPE + 1, order_type)
            ws.update_cell(i, COL_BUY_OID + 1,     order_id)
            ws.update_cell(i, COL_MKT_PRICE + 1,   mkt_price or '')
            ws.update_cell(i, COL_MY_BUY_PX + 1,   buy_price)
            ws.update_cell(i, COL_MY_BUY_QTY + 1,  qty)
        bought += 1

    log(f"=== Purchase complete | Bought: {bought} | Waiting/Skipped: {skipped} | "
        f"Flagged: {flagged} | Stale flagged: {stale_flagged} ===")


if __name__ == '__main__':
    run()
