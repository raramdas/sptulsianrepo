#!/usr/bin/env python3
"""
sheet_logger.py — writes trade rows to the Google Sheet Master Database tab,
checks for duplicate trades, and reads manually-entered target prices.

Test independently:
    python3 -c "
from sheet_logger import log_to_sheet
log_to_sheet({'category':'Test','stock':'TESTSTOCK','kite_symbol':'TEST',
              'email_price':100,'buy_price':100,'qty':1,'order_type':'LIMIT',
              'buy_order_id':'TEST123','note':'module test'})
"
"""
import gspread
from datetime import datetime

from config import log, SHEET_ID, SHEET_TAB, GSHEET_CREDS_FILE, IST, clean_float


def log_to_sheet(tip):
    # Column layout (v3.1 — Symbol added at col C):
    # A:Category  B:Stock  C:Symbol  D:Type  E:Buy Date  F:Recommended Price
    # G:Target  H:Timeframe  I:Have Interest  J:Status  K:Target Met
    # L:Target Met/Exit Date  M:Gain  N:My Buy Date  O:Order Type  P:Buy Order ID
    # Q:Market Price at Buy  R:My Buy Price  S:My Buy Qty  T:My Sell Date
    # U:My Sell Price  V:My Sell Qty  W:My Gain or Loss  X:GTT ID  Y:GTT Status  Z:Notes
    gc = gspread.service_account(filename=GSHEET_CREDS_FILE)
    ws = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
    today_str = datetime.now(IST).strftime('%Y-%m-%d')
    ws.append_row([
        tip.get('category', ''),
        tip.get('stock', ''),
        tip.get('kite_symbol', ''),
        tip.get('type', '') or tip.get('cap_type', ''),
        today_str,
        tip.get('email_price', ''),
        tip.get('target', ''),
        tip.get('timeframe', ''),
        tip.get('have_interest', ''),
        'Open',
        '',
        '',
        '',
        today_str,
        tip.get('order_type', ''),
        tip.get('buy_order_id', ''),
        tip.get('mkt_price', ''),
        tip.get('buy_price', ''),
        tip.get('qty', 1),
        '',
        '',
        '',
        '',
        '',
        '',
        tip.get('note', ''),
    ])
    log(f"  Logged to sheet: {tip.get('stock', '')}")


def is_duplicate(stock, date):
    """Check sheet to prevent double orders. Col B=Stock(1), Col E=Buy Date(4), Col J=Status(9)."""
    try:
        gc   = gspread.service_account(filename=GSHEET_CREDS_FILE)
        ws   = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
        rows = ws.get_all_values()
        for r in rows[1:]:
            row_date   = r[4].strip() if len(r) > 4 else ''
            row_stock  = r[1].strip() if len(r) > 1 else ''
            row_status = r[9].strip() if len(r) > 9 else ''
            date_match = (date in row_date) or (row_date in date)
            if date_match and row_stock.lower() == stock.lower() and row_status not in ('ERROR', ''):
                log(f"  Duplicate found in sheet: {stock} on {date} — skipping")
                return True
    except Exception as e:
        log(f"  is_duplicate error: {e}")
    return False


def get_target_from_sheet(stock):
    """Col B (index 1) = Stock, Col G (index 6) = Target (manually filled)."""
    try:
        gc   = gspread.service_account(filename=GSHEET_CREDS_FILE)
        ws   = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
        rows = ws.get_all_values()
        for row in reversed(rows[1:]):
            row_stock  = row[1].strip() if len(row) > 1 else ''
            row_target = row[6].strip() if len(row) > 6 else ''
            target_val = clean_float(row_target)
            if (row_stock.lower() == stock.lower()
                    and target_val and target_val > 0):
                return target_val
    except Exception as e:
        log(f"get_target_from_sheet error: {e}")
    return None


if __name__ == '__main__':
    print("Testing is_duplicate('Zee Ent', '2026-06-30'):")
    print(is_duplicate('Zee Ent', '2026-06-30'))
