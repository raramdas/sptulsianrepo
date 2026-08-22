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
    'apollo micro systems': 'APMOSYS',
    'polycab india':        'POLYCAB',
    'transrail lighting':   'TRANSRAIL',
    'mazagon dock':         'MAZDOCK',
    'atlanta electricals':  'ATLANTAELE',
    'td power':             'TDPOWERSYS',
    'solar industries':     'SOLARINDS',
    'zen tech':             'ZENTEC',
}

_instrument_cache = None  # normalized_name -> symbol, EQUITY ONLY


def _normalize_name(name):
    """Lowercase, strip punctuation and common corporate suffixes for matching."""
    name = name.lower()
    name = re.sub(r'[^\w\s]', ' ', name)
    name = re.sub(r'\b(ltd|limited|the|india)\b', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _load_instrument_cache():
    """Download and parse Kite's NSE instrument list using a proper CSV parser,
    keeping only EQ (equity) instruments to avoid matching against bonds/SDLs/ETFs."""
    global _instrument_cache
    if _instrument_cache is not None:
        return _instrument_cache
    _instrument_cache = {}
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
        log(f"  Loaded {len(_instrument_cache)} equity instruments")
    except Exception as e:
        log(f"  Instrument list error: {e}")
    return _instrument_cache


def resolve_kite_symbol(stock_name, enctoken=None):
    """Resolve stock name to NSE trading symbol.
    Order: manual map -> exact normalized match -> ranked fuzzy match -> fallback."""
    key = stock_name.strip().lower()
    if key in SYMBOL_MAP:
        log(f"  Symbol (manual map): {SYMBOL_MAP[key]}")
        return SYMBOL_MAP[key]

    cache = _load_instrument_cache()
    norm_key = _normalize_name(stock_name)

    if norm_key in cache:
        sym = cache[norm_key]
        log(f"  Symbol (exact match): {sym}")
        return sym

    if cache:
        close = difflib.get_close_matches(norm_key, cache.keys(), n=1, cutoff=0.6)
        if close:
            matched_name = close[0]
            sym = cache[matched_name]
            log(f"  Symbol (fuzzy match on '{matched_name}'): {sym}")
            return sym

    fallback = stock_name.upper().replace(' ', '')
    log(f"  Symbol (fallback, no match found): {fallback}")
    return fallback


def get_market_price(stock, enctoken=None):
    """Fetch LTP from Kite first, fallback to Google Finance."""
    kite_sym = resolve_kite_symbol(stock, enctoken)
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
        sym    = resolve_kite_symbol(stock, enctoken) or stock.upper().replace(' ', '')
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
    """Place a CNC buy order (LIMIT or MARKET) on Kite."""
    sym = tip.get('kite_symbol') or resolve_kite_symbol(tip['stock'], enctoken)
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
    trigger_price = 3% below target (fires when price rises to trigger level)
    limit sell price = target_price (the actual sell price)

    Kite requires that for a sell GTT, last_price must be BELOW the trigger_price.
    If we couldn't fetch a real market price, derive a safe last_price below trigger.

    NOTE: this is an OLDER implementation than lib/kite_client.place_gtt and has
    not been brought forward. It differs in two ways that matter:
      - trigger is a flat 3% below target, not target minus an offset rounded
        to the instrument's tick size. Kite rejects triggers that are not an
        exact multiple of tick size ("Trigger price should be a multiple of
        tick size X"), which the lib/ version handles and this one does not.
      - the synthetic last_price is 2% below trigger, which happens to clear
        Kite's ~Rs 0.09 absolute floor, so it does not hit the low-priced-stock
        rejection the lib/ version was fixed for.
    bot/ is not in the crontab. Port lib/kite_client.place_gtt before scheduling
    anything here.
    """
    trigger_price = round(target_price * 0.97, 2)
    if not last_price or last_price >= trigger_price:
        last_price = round(trigger_price * 0.98, 2)
    log(f"  GTT: trigger={trigger_price} limit={target_price} last_price={last_price}")
    orders = json.dumps([{
        'exchange': EXCHANGE, 'tradingsymbol': symbol,
        'transaction_type': 'SELL', 'quantity': qty,
        'order_type': 'LIMIT', 'product': 'CNC', 'price': target_price
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
        sym = resolve_kite_symbol(stock)
        price = get_market_price(stock)
        print(f"{stock} -> symbol={sym}, price={price}")
