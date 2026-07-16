#!/usr/bin/env python3
"""
retry_needs_review_buys.py — one-off recovery script.

main.py only ever looks at TODAY's fresh emails — it never re-scans old
sheet rows. So a row flagged Status='NEEDS_REVIEW' (symbol resolution was
FUZZY or NOT_FOUND) sits there indefinitely until something explicitly goes
back and retries it. This is that "something" — run it any time after
adding a new entry to SYMBOL_MAP in kite_client.py, to clear the backlog of
rows that are now resolvable.

For each NEEDS_REVIEW row: re-attempts resolve_kite_symbol. If it now
resolves MANUAL/EXACT, runs the same buy flow as main.py's process_tip
(cap type, market price, qty, budget check, place order) and updates the
row IN PLACE — this does NOT append a new row, unlike main.py.

Note on Oracle: this inserts a NEW trades row via insert_trade_to_oracle
for the successful buy, same as main.py does. The original NEEDS_REVIEW
row already in Oracle from the first pass is left as-is (a stale historical
record) rather than updated in place — budget views only sum status='Open'
trades, so this doesn't affect budget calculations, but it does mean you'll
see two Oracle rows for the same tip (one NEEDS_REVIEW, one Open) if you
ever query the raw trades table directly.

Run directly:
    python3 retry_needs_review_buys.py
"""
import math

from config import log, DRY_RUN, INVEST_AMT
from kite_client import get_enctoken, resolve_kite_symbol, get_market_price, kite_buy
from budget_manager import get_stock_cap_type, check_budget_available, insert_trade_to_oracle, close_oracle_connection
from sheet_gtt_updater import (
    get_sheet_rows, get_worksheet,
    COL_CATEGORY, COL_STOCK, COL_SYMBOL, COL_TYPE, COL_REC_PRICE, COL_STATUS,
    COL_MY_BUY_DATE, COL_ORDER_TYPE, COL_BUY_OID, COL_MKT_PRICE, COL_MY_BUY_PX,
    COL_MY_BUY_QTY, COL_NOTES,
)
from config import IST
from datetime import datetime


def run():
    log("=== Retry NEEDS_REVIEW Buys starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'} (uses config.py's DRY_RUN, same as main.py)")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    ws   = get_worksheet()
    rows = get_sheet_rows()

    candidates = [i for i, row in enumerate(rows[1:], start=2) if row[COL_STATUS].strip() == 'NEEDS_REVIEW']
    log(f"Found {len(candidates)} row(s) with Status=NEEDS_REVIEW")
    if not candidates:
        log("Nothing to retry.")
        return

    resolved, still_stuck, skipped_budget, errored = 0, 0, 0, 0

    for i in candidates:
        row = rows[i - 1]
        stock     = row[COL_STOCK].strip()
        category  = row[COL_CATEGORY].strip()
        rec_price_raw = row[COL_REC_PRICE].strip()

        log(f"Row {i}: {stock}")

        try:
            email_price = float(rec_price_raw.replace(',', ''))
        except ValueError:
            log(f"  Bad recommended price '{rec_price_raw}' — skipping")
            continue

        kite_symbol, symbol_status = resolve_kite_symbol(stock, enctoken)
        if symbol_status not in ('MANUAL', 'EXACT'):
            log(f"  Still {symbol_status} — needs a SYMBOL_MAP entry (or is genuinely not findable), leaving as NEEDS_REVIEW")
            still_stuck += 1
            continue

        log(f"  Symbol now resolves ({symbol_status}): {kite_symbol}")

        cap_type = get_stock_cap_type(kite_symbol)
        log(f"  Cap type: {cap_type or 'UNKNOWN'}")

        mkt_price = get_market_price(stock, enctoken, kite_symbol=kite_symbol)
        if mkt_price and mkt_price < email_price:
            buy_price, order_type = mkt_price, 'MARKET'
        else:
            buy_price, order_type = email_price, 'LIMIT'
        qty = max(1, math.floor(INVEST_AMT / buy_price))
        actual_cost = qty * buy_price
        log(f"  Qty: {qty} x {kite_symbol} @ {buy_price} ({order_type}) | cost: Rs.{actual_cost:,.2f}")

        budget_ok, category_id = check_budget_available(category, cap_type, actual_cost)
        if not budget_ok:
            log(f"  Insufficient budget — leaving row as-is, not marking SKIPPED "
                f"(unlike a fresh tip, we don't want to silently give up on a known-good symbol)")
            skipped_budget += 1
            continue

        today_str = datetime.now(IST).strftime('%Y-%m-%d')
        tip_for_oracle = {
            'category': category, 'stock': stock, 'kite_symbol': kite_symbol,
            'cap_type': cap_type, 'email_price': email_price, 'buy_price': buy_price,
            'qty': qty, 'order_type': order_type, 'mkt_price': mkt_price,
            'timeframe': '', 'target': None, 'have_interest': '',
        }

        if DRY_RUN:
            log(f"  [DRY RUN] Would BUY {qty} x {kite_symbol} @ {buy_price}")
            resolved += 1
            continue

        try:
            buy = kite_buy({**tip_for_oracle, 'kite_symbol': kite_symbol}, enctoken)
            order_id = buy['order_id']
            log(f"  Buy placed: {order_id}")

            ws.update_cell(i, COL_SYMBOL + 1, kite_symbol)
            ws.update_cell(i, COL_TYPE + 1, cap_type or '')
            ws.update_cell(i, COL_STATUS + 1, 'Open')
            ws.update_cell(i, COL_MY_BUY_DATE + 1, today_str)
            ws.update_cell(i, COL_ORDER_TYPE + 1, order_type)
            ws.update_cell(i, COL_BUY_OID + 1, order_id)
            ws.update_cell(i, COL_MKT_PRICE + 1, mkt_price or '')
            ws.update_cell(i, COL_MY_BUY_PX + 1, buy_price)
            ws.update_cell(i, COL_MY_BUY_QTY + 1, qty)
            ws.update_cell(i, COL_NOTES + 1, f'[RECOVERED {today_str}] Symbol resolved to {kite_symbol}, buy placed')

            tip_for_oracle['buy_order_id'] = order_id
            tip_for_oracle['buy_status'] = 'PLACED'
            insert_trade_to_oracle(tip_for_oracle, category_id)
            resolved += 1
        except Exception as e:
            log(f"  Buy failed: {e}")
            ws.update_cell(i, COL_NOTES + 1, f'Retry buy failed ({today_str}): {e}')
            errored += 1

    close_oracle_connection()
    log(f"=== Retry complete | Candidates: {len(candidates)} | Resolved/bought: {resolved} | "
        f"Still stuck (no symbol): {still_stuck} | Skipped (budget): {skipped_budget} | Errored: {errored} ===")


if __name__ == '__main__':
    run()
