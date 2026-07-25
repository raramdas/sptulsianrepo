#!/usr/bin/env python3
# kite_common.py
# Shared helpers for sheet_ingest_bot.py, purchase_bot.py, gtt_lifecycle_bot.py
#
# All three bots load their secrets from this module so there's exactly one
# place that reads the .env file and exactly one login routine to maintain.

import os, json, re
from datetime import datetime
import pyotp, pytz, requests, gspread
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

# ── Config ────────────────────────────────────────────────────────────────
SHEET_ID  = '1QdOHb2xWuBmF_OF1cReOXa9pQKhFFX2u266JgvFpK3M'
SHEET_TAB = 'Master Database'
EXCHANGE  = 'NSE'

KITE_API_KEY       = os.environ['KITE_API_KEY']
KITE_API_SECRET    = os.environ['KITE_API_SECRET']
ZERODHA_USER_ID    = os.environ['ZERODHA_USER_ID']
ZERODHA_PASSWORD   = os.environ['ZERODHA_PASSWORD']
TOTP_SECRET        = os.environ['ZERODHA_TOTP_SECRET']
GSHEET_CREDS_FILE  = os.environ.get('GSHEET_CREDS_JSON', '/home/ubuntu/gsheet_creds.json')

IST      = pytz.timezone('Asia/Kolkata')
OMS_BASE = 'https://kite.zerodha.com/oms'

# ── Sheet column indices (0-based) ───────────────────────────────────────
# Matches: Category, Stock, Symbol, Type, Buy Date, Recommended Price,
# Target, Timeframe, Have Interest, Status, Target Met, Target Met/Exit
# Date, Gain, My Buy Date, Order Type, Buy Order ID, Market Price at Buy,
# My Buy Price, My Buy Qty, My Sell Date, My Sell Price, My Sell Qty,
# My Gain or Loss, GTT ID, GTT Status, Notes, [Retry Count - new, add this
# header yourself in column AA if it's not there yet]
COL_CATEGORY    = 0   # A
COL_STOCK       = 1   # B
COL_SYMBOL      = 2   # C  resolved trading symbol — if filled, purchase_bot trusts it as-is
COL_TYPE        = 3   # D
COL_BUY_DATE    = 4   # E  (recommendation date)
COL_REC_PRICE   = 5   # F
COL_TARGET      = 6   # G
COL_TIMEFRAME   = 7   # H
COL_INTEREST    = 8   # I  'Yes' / 'No' — filled manually until SPT scraping is re-enabled
COL_STATUS      = 9   # J  Open / Closed / ERROR / NEEDS_REVIEW
COL_TARGET_MET  = 10  # K
COL_EXIT_DATE   = 11  # L
COL_GAIN        = 12  # M
COL_MY_BUY_DATE = 13  # N
COL_ORDER_TYPE  = 14  # O
COL_BUY_OID     = 15  # P  blank = not yet bought (this is the signal purchase_bot uses)
COL_MKT_PRICE   = 16  # Q
COL_MY_BUY_PX   = 17  # R
COL_MY_BUY_QTY  = 18  # S
COL_SELL_DATE   = 19  # T
COL_SELL_PRICE  = 20  # U
COL_SELL_QTY    = 21  # V
COL_GAIN_LOSS   = 22  # W
COL_GTT_ID      = 23  # X
COL_GTT_STATUS  = 24  # Y  PLACED / RETRY / TRIGGERED / DRY_RUN
COL_NOTES       = 25  # Z
COL_RETRY_CNT   = 26  # AA  NEW COLUMN — add header "Retry Count" to your sheet

NUM_COLS = COL_RETRY_CNT + 1

# ── Manual symbol overrides ───────────────────────────────────────────────
# Populate this whenever the short form used in SPTulsian emails doesn't
# exactly match the Kite instrument name/symbol. This is the ONLY place
# fuzzy/short-form names get resolved — anything not listed here either
# matches an instrument name exactly, or gets flagged NEEDS_REVIEW.
SYMBOL_MAP = {
    'zee ent':              'ZEEL',
    'vodafone idea':        'IDEA',
    'apollo micro systems': 'APMOSYS',
    'polycab india':        'POLYCAB',
    'cg power':             'CGPOWER',
}


def log(msg):
    ts = datetime.now(IST).strftime('%H:%M:%S')
    print(f"[{ts} IST] {msg}", flush=True)


def clean_float(val):
    try:
        return float(str(val).replace(',', '').strip())
    except Exception:
        return None


def kite_headers(enctoken):
    return {'Authorization': f'enctoken {enctoken}'}


def get_enctoken():
    session = requests.Session()
    r = session.post('https://kite.zerodha.com/api/login', data={
        'user_id': ZERODHA_USER_ID, 'password': ZERODHA_PASSWORD
    })
    data = r.json()
    if data.get('status') != 'success':
        raise RuntimeError(f"Login failed: {data.get('message')}")
    request_id = data['data']['request_id']
    totp_code  = pyotp.TOTP(TOTP_SECRET).now()
    r2 = session.post('https://kite.zerodha.com/api/twofa', data={
        'user_id': ZERODHA_USER_ID, 'request_id': request_id,
        'twofa_value': totp_code, 'skip_session': ''
    })
    data2 = r2.json()
    if data2.get('status') != 'success':
        raise RuntimeError(f"2FA failed: {data2.get('message')}")
    enctoken = session.cookies.get('enctoken', '')
    if not enctoken:
        raise RuntimeError('enctoken not found in cookies after login')
    return enctoken


def get_sheet(gsheet_creds_file=None):
    gc = gspread.service_account(filename=gsheet_creds_file or GSHEET_CREDS_FILE)
    return gc.open_by_key(SHEET_ID).worksheet(SHEET_TAB)


def pad_row(row):
    return row + [''] * (NUM_COLS - len(row))


# ── Order / GTT helpers ───────────────────────────────────────────────────

def get_order_status(order_id, enctoken):
    """Fetch order status and filled qty from Kite."""
    try:
        r = requests.get(
            f'{OMS_BASE}/orders/{order_id}',
            headers=kite_headers(enctoken), timeout=10
        )
        data = r.json()
        if data.get('status') == 'success':
            orders = data.get('data', [])
            if orders:
                o = orders[-1]  # latest update
                return {
                    'status':      o.get('status', '').upper(),
                    'filled_qty':  int(o.get('filled_quantity', 0)),
                    'avg_price':   float(o.get('average_price', 0) or 0),
                    'symbol':      o.get('tradingsymbol', ''),
                }
    except Exception as e:
        log(f"  get_order_status error for {order_id}: {e}")
    return None


def get_orders_today(enctoken):
    """Fetch the full order book for the day (fallback for GTT->order mapping,
    since Kite does not reliably link a triggered GTT back to its order id)."""
    try:
        r = requests.get(f'{OMS_BASE}/orders', headers=kite_headers(enctoken), timeout=10)
        data = r.json()
        if data.get('status') == 'success':
            return data.get('data', [])
    except Exception as e:
        log(f"  get_orders_today error: {e}")
    return []


def find_sell_order_for_symbol(symbol, qty, enctoken, after_ts=None):
    """Fallback: scan today's order book for a SELL/CNC order matching the
    symbol (and ideally quantity), used when a GTT's `result` field doesn't
    carry an order_id we can query directly."""
    orders = get_orders_today(enctoken)
    candidates = [
        o for o in orders
        if o.get('tradingsymbol') == symbol
        and o.get('transaction_type') == 'SELL'
        and o.get('product') == 'CNC'
    ]
    if after_ts:
        candidates = [o for o in candidates if o.get('order_timestamp', '') >= after_ts]
    # Prefer an exact quantity match, else the most recent
    exact = [o for o in candidates if int(o.get('quantity', 0)) == qty]
    pool = exact or candidates
    if not pool:
        return None
    pool.sort(key=lambda o: o.get('order_timestamp', ''))
    return pool[-1]


def place_gtt(symbol, qty, trigger_price, last_price, enctoken):
    """Place a single-trigger GTT sell order."""
    orders = json.dumps([{
        'transaction_type': 'SELL',
        'quantity':         qty,
        'order_type':       'LIMIT',
        'product':          'CNC',
        'price':            trigger_price
    }])
    payload = {
        'type':             'single',
        'tradingsymbol':    symbol,
        'exchange':         EXCHANGE,
        'trigger_values':   json.dumps([trigger_price]),
        'last_price':       last_price,
        'orders':           orders
    }
    r   = requests.post(f'{OMS_BASE}/gtt/triggers',
                        headers=kite_headers(enctoken), data=payload)
    res = r.json()
    if res.get('status') != 'success':
        raise RuntimeError(f"GTT failed: {res.get('message')}")
    return str(res['data']['trigger_id'])


def get_gtt_detail(gtt_id, enctoken):
    """Full GTT detail — status, condition, and orders[].result (populated
    once triggered). Returns None on failure."""
    try:
        r = requests.get(
            f'{OMS_BASE}/gtt/triggers/{gtt_id}',
            headers=kite_headers(enctoken), timeout=10
        )
        data = r.json()
        if data.get('status') == 'success':
            return data['data']
    except Exception as e:
        log(f"  get_gtt_detail error for {gtt_id}: {e}")
    return None


def get_ltp(symbol, enctoken):
    """Get last traded price from Kite."""
    try:
        r = requests.get(
            f'{OMS_BASE}/quote',
            params={'i': f'NSE:{symbol}'},
            headers=kite_headers(enctoken), timeout=10
        )
        d = r.json()
        return d.get('data', {}).get(f'NSE:{symbol}', {}).get('last_price')
    except Exception:
        return None


# ── Symbol resolution (fixed) ────────────────────────────────────────────
# The old version did a bidirectional substring match and silently took the
# first hit — that's how "CG Power" bought the wrong scrip. This version
# NEVER guesses: exact match (manual map or instrument list) or NEEDS_REVIEW.

_instrument_cache = None  # name(lower) -> [symbols...]; used to detect ambiguity


def _load_instruments(enctoken):
    global _instrument_cache
    if _instrument_cache is not None:
        return
    _instrument_cache = {}
    try:
        r = requests.get(
            'https://api.kite.trade/instruments/NSE',
            headers=kite_headers(enctoken), timeout=15
        )
        lines = r.text.strip().split('\n')[1:]  # skip header
        for line in lines:
            parts = line.split(',')
            if len(parts) > 3:
                sym  = parts[2].strip().strip('"')
                name = parts[3].strip().strip('"').lower()
                _instrument_cache.setdefault(name, set()).add(sym)
                _instrument_cache.setdefault(sym.lower(), set()).add(sym)
    except Exception as e:
        log(f"  Instrument list error: {e}")


def resolve_symbol_strict(stock_name, enctoken):
    """
    Returns (symbol_or_None, status) where status is one of:
      'MANUAL'    - matched SYMBOL_MAP, trusted
      'EXACT'     - exactly one instrument matches the name/symbol
      'AMBIGUOUS' - more than one instrument matches -> DO NOT auto-buy
      'NOT_FOUND' - no match -> DO NOT auto-buy
    Callers must treat anything other than MANUAL/EXACT as "stop and flag
    for human review" rather than guessing.
    """
    key = stock_name.strip().lower()

    if key in SYMBOL_MAP:
        return SYMBOL_MAP[key], 'MANUAL'

    _load_instruments(enctoken)

    matches = _instrument_cache.get(key)
    if not matches:
        return None, 'NOT_FOUND'
    if len(matches) == 1:
        return next(iter(matches)), 'EXACT'
    return None, 'AMBIGUOUS'
