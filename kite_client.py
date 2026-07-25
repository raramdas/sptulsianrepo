#!/usr/bin/env python3
"""
kite_client.py — Kite/Zerodha authentication, symbol resolution, market price
fetching, and order placement (buy + GTT).

Test independently:
    python3 -c "from kite_client import get_enctoken; print(get_enctoken()[:10])"
    python3 -c "from kite_client import resolve_kite_symbol; print(resolve_kite_symbol('Zee Ent'))"
    python3 -c "from kite_client import get_market_price; print(get_market_price('Zee Ent'))"
"""
import json
import re
import csv
import io
import math
import difflib
import pyotp
import requests

from config import log, clean_float, OMS_BASE, EXCHANGE, ZERODHA_USER_ID, ZERODHA_PASSWORD, TOTP_SECRET


def get_enctoken():
    """Login to Kite via TOTP and return an enctoken for API calls."""
    return get_enctoken_for(ZERODHA_USER_ID, ZERODHA_PASSWORD, TOTP_SECRET)


def get_enctoken_for(user_id, password, totp_secret):
    """Same as get_enctoken() but for explicit credentials (used for
    multi-tenant testing, where each tenant has their own Kite login
    rather than the single global ZERODHA_* values in config.py)."""
    session = requests.Session()
    r = session.post('https://kite.zerodha.com/api/login', data={
        'user_id': user_id, 'password': password
    })
    data = r.json()
    if data.get('status') != 'success':
        raise RuntimeError(f"Login failed: {data.get('message')}")
    request_id = data['data']['request_id']
    totp_code = pyotp.TOTP(totp_secret).now()
    r2 = session.post('https://kite.zerodha.com/api/twofa', data={
        'user_id': user_id, 'request_id': request_id,
        'twofa_value': totp_code, 'skip_session': ''
    })
    data2 = r2.json()
    if data2.get('status') != 'success':
        raise RuntimeError(f"2FA failed: {data2.get('message')}")
    enctoken = session.cookies.get('enctoken', '')
    if not enctoken:
        raise RuntimeError('enctoken not found in cookies after login')
    return enctoken


def kite_headers(enctoken):
    return {'Authorization': f'enctoken {enctoken}'}


# ── Symbol resolution ─────────────────────────────────────────────
# Manual overrides for common name -> NSE symbol mismatches
SYMBOL_MAP = {
    'zee ent':              'ZEEL',
    'vodafone idea':        'IDEA',
    'apollo micro systems': 'APOLLO',       # FIXED — was APMOSYS (wrong symbol)
    'polycab india':        'POLYCAB',
    'transrail lighting':   'TRANSRAILL',   # FIXED — was TRANSRAIL (missing 2nd L)
    'mazagon dock':         'MAZDOCK',
    'atlanta electricals':  'ATLANTAELE',
    'td power':             'TDPOWERSYS',
    'solar industries':     'SOLARINDS',
    'zen tech':             'ZENTEC',
    'cg power':             'CGPOWER',      # ADDED — was missing entirely, fell through to fuzzy match
    'billionbrains':        'GROWW',        # ADDED — Billionbrains Garage Ventures Ltd (Groww's parent), verified via NSE India
}

_instrument_cache = None  # normalized_name -> symbol, EQUITY ONLY
_tick_size_cache = None   # SYMBOL (upper) -> tick_size, EQUITY ONLY


def _normalize_name(name):
    """Lowercase, strip punctuation and common corporate suffixes for matching."""
    name = name.lower()
    name = re.sub(r'[^\w\s]', ' ', name)
    name = re.sub(r'\b(ltd|limited|the|india)\b', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _load_instrument_cache():
    """Download and parse Kite's NSE instrument list using a proper CSV parser,
    keeping only EQ (equity) instruments to avoid matching against bonds/SDLs/ETFs.
    Indexed by BOTH normalized company name (e.g. "cdsl" from "Central
    Depository Services...") AND the raw ticker symbol itself (e.g. "cdsl"
    from tradingsymbol "CDSL") — so a tip that names the stock either way
    resolves via EXACT match instead of falling through to fuzzy matching.
    Also populates _tick_size_cache from the same download, since Kite
    requires GTT trigger prices to be an exact multiple of each instrument's
    own tick size (varies per stock — 0.05/0.10/1.00/5.00 seen in practice,
    not a flat NSE-wide constant)."""
    global _instrument_cache, _tick_size_cache
    if _instrument_cache is not None:
        return _instrument_cache
    _instrument_cache = {}
    _tick_size_cache = {}
    try:
        r = requests.get('https://api.kite.trade/instruments/NSE', timeout=15)
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            if row.get('instrument_type') != 'EQ':
                continue
            sym  = (row.get('tradingsymbol') or '').strip()
            name = (row.get('name') or '').strip()
            if not sym or not name:
                continue
            _instrument_cache[_normalize_name(name)] = sym
            _instrument_cache.setdefault(sym.lower(), sym)  # don't clobber a name-based match with the same key
            try:
                _tick_size_cache[sym.upper()] = float(row.get('tick_size') or 0.05)
            except (ValueError, TypeError):
                _tick_size_cache[sym.upper()] = 0.05
        log(f"  Loaded {len(_instrument_cache)} equity instruments (indexed by name + symbol, "
            f"tick sizes cached for {len(_tick_size_cache)})")
    except Exception as e:
        log(f"  Instrument list error: {e}")
    return _instrument_cache


def get_tick_size(symbol):
    """Returns the instrument's real tick size, falling back to NSE's common
    Rs 0.05 default only if the symbol isn't found (rather than guessing
    silently — this is logged so a fallback is visible, not invisible)."""
    _load_instrument_cache()
    tick = _tick_size_cache.get(symbol.strip().upper())
    if tick is None:
        log(f"  No tick size found for {symbol} — defaulting to Rs 0.05 (verify manually if this GTT fails)")
        return 0.05
    return tick


def _round_to_tick(price, tick_size, mode='nearest'):
    """Round price to a valid multiple of tick_size. mode='floor' rounds
    down (used for the trigger, so it never accidentally lands AT or ABOVE
    the target), mode='ceil' rounds up (used for the actual limit sell
    price, so it's never below the tick-floored trigger), 'nearest' for
    general use."""
    if not tick_size or tick_size <= 0:
        tick_size = 0.05
    n = price / tick_size
    if mode == 'floor':
        n = math.floor(n + 1e-9)   # epsilon guards against float imprecision
    elif mode == 'ceil':
        n = math.ceil(n - 1e-9)
    else:
        n = round(n)
    return round(n * tick_size, 2)


def resolve_kite_symbol(stock_name, enctoken=None):
    """
    Resolve stock name to NSE trading symbol.
    Returns (symbol_or_None, status) where status is one of:
      'MANUAL'    - matched SYMBOL_MAP, trusted
      'EXACT'     - exactly one normalized instrument name match
      'FUZZY'     - only a fuzzy match found — NOT auto-trusted, caller must
                    treat this as NEEDS_REVIEW (this is exactly the class of
                    guess that caused CG Power/Apollo Micro Systems to buy
                    the wrong scrip before, just with a different mechanism)
      'NOT_FOUND' - no match at all

    Only MANUAL and EXACT are safe to buy on automatically.
    """
    key = stock_name.strip().lower()
    if key in SYMBOL_MAP:
        log(f"  Symbol (manual map): {SYMBOL_MAP[key]}")
        return SYMBOL_MAP[key], 'MANUAL'

    cache = _load_instrument_cache()
    norm_key = _normalize_name(stock_name)

    if norm_key in cache:
        sym = cache[norm_key]
        log(f"  Symbol (exact match): {sym}")
        return sym, 'EXACT'

    if cache:
        close = difflib.get_close_matches(norm_key, cache.keys(), n=1, cutoff=0.6)
        if close:
            matched_name = close[0]
            sym = cache[matched_name]
            log(f"  Symbol (FUZZY match on '{matched_name}' -> {sym}) — "
                f"NOT auto-trusted, flagging for review instead of guessing")
            return sym, 'FUZZY'

    log(f"  Symbol: no match found for '{stock_name}'")
    return None, 'NOT_FOUND'


def get_market_price(stock, enctoken=None, kite_symbol=None):
    """Fetch LTP from Kite first, fallback to Google Finance.
    Pass kite_symbol explicitly if the caller has already resolved+validated
    it (e.g. main.py after checking resolve_kite_symbol's status) — this
    avoids re-resolving and re-triggering a FUZZY log message for a symbol
    that's already been flagged."""
    kite_sym = kite_symbol
    if not kite_sym:
        kite_sym, status = resolve_kite_symbol(stock, enctoken)
        if status not in ('MANUAL', 'EXACT'):
            log(f"  Skipping price lookup — symbol resolution status was {status}, not trusted")
            return None
    if kite_sym and enctoken:
        try:
            r = requests.get(
                f'{OMS_BASE}/quote',
                params={'i': f'NSE:{kite_sym}'},
                headers=kite_headers(enctoken), timeout=10
            )
            d = r.json()
            ltp = d.get('data', {}).get(f'NSE:{kite_sym}', {}).get('last_price')
            if ltp:
                log(f"  LTP from Kite: {ltp}")
                return ltp
        except Exception as e:
            log(f"  Kite LTP error: {e}")
    # Fallback: Google Finance via requests
    try:
        sym = kite_sym or stock.upper().replace(' ', '')
        gf_url = f'https://www.google.com/finance/quote/{sym}:NSE'
        r      = requests.get(gf_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
        for pattern in [
            r'data-last-price="([\d.]+)"',
            r'"price":"([\d.]+)"',
            r'<div[^>]*class="[^"]*YMlKec[^"]*"[^>]*>([\d,]+(?:\.[\d]+)?)<',
            r'([\d,]+\.\d+)\s*</div>',
        ]:
            match = re.search(pattern, r.text)
            if match:
                price = clean_float(match.group(1))
                if price and price > 0:
                    log(f"  LTP from Google Finance: {price}")
                    return price
        log(f"  Google Finance: price not found for {sym}")
    except Exception as e:
        log(f"  Google Finance error: {e}")
    return None


def kite_buy(tip, enctoken):
    """Place a CNC buy order (LIMIT or MARKET) on Kite.
    Requires tip['kite_symbol'] to already be set by the caller (main.py sets
    this only after confirming resolve_kite_symbol's status was MANUAL/EXACT)
    — this function does NOT re-resolve or fall back to a guessed symbol,
    since a wrong symbol here means real money on the wrong stock."""
    sym = tip.get('kite_symbol')
    if not sym:
        raise RuntimeError(
            f"kite_buy called without a resolved kite_symbol for '{tip.get('stock')}' — "
            f"refusing to guess. Resolve and validate the symbol before calling this."
        )
    payload = {
        'exchange': EXCHANGE, 'tradingsymbol': sym,
        'transaction_type': 'BUY', 'quantity': tip['qty'],
        'order_type': tip['order_type'], 'product': 'CNC',
        'validity': 'DAY', 'tag': 'SPT'
    }
    if tip['order_type'] == 'LIMIT':
        payload['price'] = tip['buy_price']
    r = requests.post(f'{OMS_BASE}/orders/regular',
        headers=kite_headers(enctoken), data=payload)
    res = r.json()
    if res.get('status') != 'success':
        raise RuntimeError(f"Buy failed: {res.get('message')}")
    return res['data']


def place_gtt(symbol, qty, target_price, last_price, enctoken):
    """Place a single-trigger GTT sell order.
    trigger_price = Rs 0.10 below target, ROUNDED DOWN to the instrument's
    own tick size (0.05/0.10/1.00/5.00 etc — varies per stock, NOT a flat
    NSE-wide constant). Kite requires the trigger to be an EXACT multiple
    of that tick size — this was previously missing entirely, which is
    exactly what caused "Trigger price should be a multiple of tick size
    X" errors across roughly half of a batch of reconciliation GTTs.
    limit sell price = target_price, rounded UP to the same tick size (so
    it's never below the tick-floored trigger).

    Kite ALSO requires last_price to differ from trigger_price by MORE
    THAN 0.25% (and, for a sell GTT, last_price must be below trigger_price).
    We use a 0.5% margin — safely above that 0.25% floor — rather than a
    flat rupee amount, since a flat Rs 0.10 gap is well under 0.25% for
    almost any stock priced above ~Rs 40.
    """
    tick_size = get_tick_size(symbol)
    GTT_OFFSET = 0.10
    trigger_price = _round_to_tick(target_price - GTT_OFFSET, tick_size, mode='floor')
    limit_price = _round_to_tick(target_price, tick_size, mode='ceil')

    min_gap = round(trigger_price * 0.005, 2)  # 0.5% margin, above Kite's 0.25% minimum
    if not last_price or (trigger_price - last_price) < min_gap:
        original = last_price
        last_price = round(trigger_price - min_gap, 2)
        log(f"  Adjusted last_price from {original} to {last_price} "
            f"(needed >0.25% gap from trigger {trigger_price}, using 0.5% margin)")
    log(f"  GTT: tick_size={tick_size} trigger={trigger_price} limit={limit_price} last_price={last_price}")
    orders = json.dumps([{
        'exchange': EXCHANGE, 'tradingsymbol': symbol,
        'transaction_type': 'SELL', 'quantity': qty,
        'order_type': 'LIMIT', 'product': 'CNC', 'price': limit_price
    }])
    condition = json.dumps({
        'exchange': EXCHANGE, 'tradingsymbol': symbol,
        'trigger_values': [trigger_price],
        'last_price': last_price,
    })
    payload = {'type': 'single', 'condition': condition, 'orders': orders}
    r = requests.post(f'{OMS_BASE}/gtt/triggers',
        headers=kite_headers(enctoken), data=payload)
    res = r.json()
    if res.get('status') != 'success':
        raise RuntimeError(f"GTT failed: {res.get('message')}")
    return str(res['data']['trigger_id'])


def get_gtt_status(gtt_id, enctoken):
    """Check if a GTT has triggered/been executed."""
    try:
        r = requests.get(
            f'{OMS_BASE}/gtt/triggers/{gtt_id}',
            headers=kite_headers(enctoken), timeout=10
        )
        data = r.json()
        if data.get('status') == 'success':
            return data['data'].get('status', '').upper()
    except Exception as e:
        log(f"  get_gtt_status error for {gtt_id}: {e}")
    return None


def get_gtt_detail(gtt_id, enctoken):
    """Full GTT detail — status, condition, and orders[].result (populated
    once triggered, with the resulting order's info). Returns None on failure.
    Needed to verify a GTT's sell order actually filled, rather than trusting
    the top-level status alone (a GTT can show 'triggered' even when the
    DAY-validity sell order it placed never filled and was cancelled at EOD)."""
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


if __name__ == '__main__':
    # Quick self-test: symbol resolution + market price (no login needed for these)
    for stock in ['Zee Ent', 'Vodafone Idea', 'Reliance Industries']:
        sym, status = resolve_kite_symbol(stock)
        price = get_market_price(stock, kite_symbol=sym if status in ('MANUAL', 'EXACT') else None)
        print(f"{stock} -> symbol={sym} (status={status}), price={price}")
