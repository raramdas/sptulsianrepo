#!/usr/bin/env python3
"""
sheet_gtt_updater.py — reads sheet rows for the GTT bot and updates
GTT ID / GTT Status / Status / Target Met columns.

Test independently:
    python3 -c "from sheet_gtt_updater import get_sheet_rows; print(len(get_sheet_rows()))"
"""
import gspread
from lib.config import log, SHEET_ID, SHEET_TAB, GSHEET_CREDS_FILE

# Sheet column indices (0-based) — v3.1 with Symbol at col C
COL_CATEGORY    = 0   # A
COL_STOCK       = 1   # B
COL_SYMBOL      = 2   # C
COL_TYPE        = 3   # D
COL_BUY_DATE    = 4   # E
COL_REC_PRICE   = 5   # F
COL_TARGET      = 6   # G
COL_TIMEFRAME   = 7   # H
COL_INTEREST    = 8   # I
COL_STATUS      = 9   # J
COL_TARGET_MET  = 10  # K
COL_EXIT_DATE   = 11  # L
COL_GAIN        = 12  # M
COL_MY_BUY_DATE = 13  # N
COL_ORDER_TYPE  = 14  # O
COL_BUY_OID     = 15  # P
COL_MKT_PRICE   = 16  # Q
COL_MY_BUY_PX   = 17  # R
COL_MY_BUY_QTY  = 18  # S
COL_SELL_DATE   = 19  # T
COL_SELL_PRICE  = 20  # U
COL_SELL_QTY    = 21  # V
COL_GAIN_LOSS   = 22  # W
COL_GTT_ID      = 23  # X
COL_GTT_STATUS  = 24  # Y
COL_NOTES       = 25  # Z
COL_RETRY_CNT   = 26  # AA — NEW COLUMN, add header "Retry Count" to your sheet if not already there

_worksheet = None


def get_worksheet():
    global _worksheet
    if _worksheet:
        return _worksheet
    gc = gspread.service_account(filename=GSHEET_CREDS_FILE)
    _worksheet = gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)
    return _worksheet


def get_sheet_rows():
    """Return all rows (values only), each padded to full column width."""
    ws = get_worksheet()
    rows = ws.get_all_values()
    padded = []
    for row in rows:
        row = row + [''] * (COL_RETRY_CNT + 1 - len(row))
        padded.append(row)
    return padded


def set_gtt_placed(row_index, gtt_id):
    """row_index is 1-based sheet row number (2 = first data row)."""
    ws = get_worksheet()
    ws.update_cell(row_index, COL_GTT_ID + 1, gtt_id)
    ws.update_cell(row_index, COL_GTT_STATUS + 1, 'PLACED')


def set_gtt_dry_run(row_index):
    ws = get_worksheet()
    ws.update_cell(row_index, COL_GTT_ID + 1, 'DRY_RUN')
    ws.update_cell(row_index, COL_GTT_STATUS + 1, 'DRY_RUN')


def set_error(row_index, message):
    ws = get_worksheet()
    ws.update_cell(row_index, COL_STATUS + 1, 'ERROR')
    ws.update_cell(row_index, COL_NOTES + 1, message)


def set_closed(row_index, today_str):
    ws = get_worksheet()
    ws.update_cell(row_index, COL_STATUS + 1, 'Closed')
    ws.update_cell(row_index, COL_TARGET_MET + 1, 'Yes')
    ws.update_cell(row_index, COL_EXIT_DATE + 1, today_str)
    ws.update_cell(row_index, COL_GTT_STATUS + 1, 'TRIGGERED')


def set_gtt_recreated(row_index, new_gtt_id, retry_count, reason):
    """Record that a GTT was cancelled/triggered-without-fill and a fresh
    one was placed at the same target price."""
    ws = get_worksheet()
    ws.update_cell(row_index, COL_GTT_ID + 1, new_gtt_id)
    ws.update_cell(row_index, COL_GTT_STATUS + 1, 'RETRY')
    ws.update_cell(row_index, COL_RETRY_CNT + 1, retry_count)
    ws.update_cell(row_index, COL_NOTES + 1, f'{reason} — recreated (retry #{retry_count})')


if __name__ == '__main__':
    rows = get_sheet_rows()
    print(f"Total rows (incl header): {len(rows)}")
    if len(rows) > 1:
        print("Sample data row:", rows[1][:10])
