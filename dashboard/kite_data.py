#!/usr/bin/env python3
"""
kite_data.py — Kite/Zerodha data for the dashboard (holdings, GTT triggers,
today's order book).

IMPORTANT: this module does NOT call Kite's live API on every dashboard
view. It used to, but per explicit preference (avoid drawing Zerodha's
attention / rate limiting with frequent automated API calls from a
customer-facing web app), the only routine live Kite call is sync_now() —
triggered exclusively by the "Sync Kite Data" button on Overview. Every
other read function here reads back the last-synced snapshot from Oracle
(kite_holdings_snapshot / kite_gtt_snapshot / kite_orders_snapshot,
written by db.save_kite_snapshot()).

The one deliberate exception is the Needs Review retry-buy flow
(preview_retry_buy / confirm_retry_buy) — a manually-triggered, one-off
live quote + real order placement for a single trade the buy bot refused
to guess a symbol for. preview_retry_buy() places nothing; confirm_retry_buy()
is the only function in this entire module that trades.

Same enctoken-based login as kite_client.py/kite_common.py (the bot side),
reused here rather than duplicated.
"""
import math
import os
import re
import requests
import pyotp
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import db

INVEST_AMT = 5000  # mirrors config.py's INVEST_AMT — keep in sync if that ever changes

load_dotenv('/home/ubuntu/.env')

OMS_BASE = 'https://kite.zerodha.com/oms'

# Accounts to sync. NEW is the dedicated automation account and is always
# expected. OLD is the personal/pre-cutover account — kept here only until
# its remaining GTTs/holdings from before the cutover are wound down; once
# there's nothing left to track, remove the ZERODHA_OLD_* lines from .env
# and it drops out of _ACCOUNTS automatically (no code change needed).
def _account_creds(prefix):
    user_id  = os.environ.get(f'ZERODHA_{prefix}USER_ID' if prefix else 'ZERODHA_USER_ID')
    password = os.environ.get(f'ZERODHA_{prefix}PASSWORD' if prefix else 'ZERODHA_PASSWORD')
    totp     = os.environ.get(f'ZERODHA_{prefix}TOTP_SECRET' if prefix else 'ZERODHA_TOTP_SECRET')
    if user_id and password and totp:
        return {'user_id': user_id, 'password': password, 'totp_secret': totp}
    return None


_ACCOUNTS = {}
for _label, _prefix in [('NEW', ''), ('OLD', 'OLD_')]:
    _creds = _account_creds(_prefix)
    if _creds:
        _ACCOUNTS[_label] = _creds


def kite_headers(enctoken):
    return {'Authorization': f'enctoken {enctoken}'}


@st.cache_resource(ttl=4 * 3600)
def _get_enctoken(account_label):
    """Log into Kite via TOTP for the given account. Cached process-wide per
    account for 4 hours — mostly irrelevant now that syncing is
    manual/infrequent, but still avoids a double-login if Sync is clicked
    twice in quick succession."""
    creds = _ACCOUNTS.get(account_label)
    if not creds:
        raise RuntimeError(f"No Kite credentials configured for account '{account_label}' in /home/ubuntu/.env")
    session = requests.Session()
    r = session.post('https://kite.zerodha.com/api/login', data={
        'user_id': creds['user_id'], 'password': creds['password']
    }, timeout=15)
    data = r.json()
    if data.get('status') != 'success':
        raise RuntimeError(f"Kite login failed: {data.get('message')}")
    request_id = data['data']['request_id']
    totp_code = pyotp.TOTP(creds['totp_secret']).now()
    r2 = session.post('https://kite.zerodha.com/api/twofa', data={
        'user_id': creds['user_id'], 'request_id': request_id,
        'twofa_value': totp_code, 'skip_session': ''
    }, timeout=15)
    data2 = r2.json()
    if data2.get('status') != 'success':
        raise RuntimeError(f"Kite 2FA failed: {data2.get('message')}")
    enctoken = session.cookies.get('enctoken', '')
    if not enctoken:
        raise RuntimeError('enctoken not found in cookies after Kite login')
    return enctoken


def _get(path, account_label):
    enctoken = _get_enctoken(account_label)
    r = requests.get(f'{OMS_BASE}{path}', headers=kite_headers(enctoken), timeout=15)
    data = r.json()
    if data.get('status') != 'success':
        raise RuntimeError(f"Kite API {path} failed: {data.get('message')}")
    return data.get('data', [])


def _flatten_raw_gtts(raw_gtts):
    """Flatten Kite's nested GTT trigger objects (condition/orders) into flat
    rows — done once at sync time so the Oracle table can just be a normal
    flat table."""
    rows = []
    for g in raw_gtts:
        cond = g.get('condition') or {}
        gtt_orders = g.get('orders') or [{}]
        o = gtt_orders[0] if gtt_orders else {}
        rows.append({
            'id': g.get('id'),
            'symbol': cond.get('tradingsymbol'),
            'status': g.get('status'),
            'trigger_price': (cond.get('trigger_values') or [None])[0],
            'last_price': cond.get('last_price'),
            'quantity': o.get('quantity'),
            'sell_price': o.get('price'),
            'created_at': g.get('created_at'),
            'expires_at': g.get('expires_at'),
        })
    return rows


def sync_now():
    """The ONLY code path in this module that calls Kite's live API.
    Logs into every configured account (NEW always; OLD too, until its
    remaining positions are wound down and removed from .env) and syncs
    each independently — one account's login failure doesn't block the
    other's sync. Manually triggered only (the 'Sync Kite Data' button on
    Overview) — no scheduled polling."""
    results = {}
    for account_label in _ACCOUNTS:
        try:
            holdings = _get('/portfolio/holdings', account_label)
            raw_gtts = _get('/gtt/triggers', account_label)
            orders = _get('/orders', account_label)
            flat_gtts = _flatten_raw_gtts(raw_gtts)
            synced_at = db.save_kite_snapshot(account_label, holdings, flat_gtts, orders)
            results[account_label] = {'holdings': len(holdings), 'gtts': len(flat_gtts),
                                       'orders': len(orders), 'synced_at': synced_at}
        except Exception as e:
            results[account_label] = {'error': str(e)}
    return results


def sync_status():
    """Per-account last-synced info: [{account_label, synced_at,
    holdings_count, gtt_count, order_count}, ...]."""
    return db.get_kite_sync_status()


def last_synced_at():
    """Most recent sync time across all accounts, or None if never synced."""
    return db.get_kite_last_synced()


def get_holdings():
    """Last-synced holdings snapshot from Oracle — NOT a live call."""
    return db.get_kite_holdings()


def get_gtts():
    """Last-synced, already-flattened GTT snapshot from Oracle."""
    return db.get_kite_gtts()


def get_orders():
    """Last-synced order book snapshot from Oracle."""
    return db.get_kite_orders()


def holdings_summary():
    holdings = get_holdings()
    invested = sum(float(h.get('average_price') or 0) * float(h.get('quantity') or 0) for h in holdings)
    current = sum(float(h.get('last_price') or 0) * float(h.get('quantity') or 0) for h in holdings)
    return {
        'count': len(holdings),
        'invested': invested,
        'current_value': current,
        'pnl': current - invested,
    }


def gtt_summary():
    gtts = get_gtts()
    active = [g for g in gtts if str(g.get('status', '')).lower() == 'active']
    return {'total': len(gtts), 'active': len(active), 'raw': gtts}


def orders_today_summary():
    orders = get_orders()
    counts = {}
    for o in orders:
        s = o.get('status', 'UNKNOWN')
        counts[s] = counts.get(s, 0) + 1
    return {'total': len(orders), 'by_status': counts, 'raw': orders}


def tag_holdings_with_category(open_trades_df):
    """Return every synced Kite holding tagged with the Oracle category it
    belongs to (by trading symbol), or category_name=None if the symbol has
    no matching 'Open' trade in Oracle at all.

    open_trades_df: Oracle's open trades (needs symbol, category_name,
    my_buy_qty columns — e.g. db.trades(status='Open')).

    A symbol can span multiple categories (separate lots bought under
    different categories) — in that case it produces one row per category,
    with invested/current_value/pnl split in proportion to each category's
    share of Oracle-recorded quantity for that symbol.

    This is the single source of truth for both the Holdings detail view
    (grouped mapped-vs-unmapped) and the Category Performance rollup —
    keeping them in one place so the two views can't drift apart.
    """
    holdings = get_holdings()
    cols = ['symbol', 'category_name', 'quantity', 'average_price', 'last_price', 'invested', 'current_value', 'pnl', 'account_label']
    if not holdings:
        return pd.DataFrame(columns=cols)

    oracle_by_symbol = {}
    if open_trades_df is not None and not open_trades_df.empty:
        by_sym_cat = open_trades_df.assign(
            symbol_upper=open_trades_df['symbol'].str.upper()
        ).groupby(['symbol_upper', 'category_name'])['my_buy_qty'].sum().reset_index()
        for sym, grp in by_sym_cat.groupby('symbol_upper'):
            # One weight per DISTINCT category for this symbol (lots already summed
            # within each category above) — not one per raw Oracle trade row, or a
            # symbol with several same-category lots would get split into duplicate
            # fractional rows instead of one full-value row.
            total_qty = grp['my_buy_qty'].sum()
            if total_qty:
                oracle_by_symbol[sym] = [
                    (row['category_name'], float(row['my_buy_qty']) / total_qty)
                    for _, row in grp.iterrows()
                ]

    rows = []
    for h in holdings:
        sym = (h.get('tradingsymbol') or '').upper()
        qty = float(h.get('quantity') or 0)
        avg = float(h.get('average_price') or 0)
        ltp = float(h.get('last_price') or 0)
        acct = h.get('account_label')
        invested = qty * avg
        current = qty * ltp
        pnl = current - invested

        shares = oracle_by_symbol.get(sym)
        if not shares:
            rows.append({'symbol': sym, 'category_name': None, 'quantity': qty,
                          'average_price': avg, 'last_price': ltp,
                          'invested': invested, 'current_value': current, 'pnl': pnl,
                          'account_label': acct})
            continue
        for cat_name, weight in shares:
            rows.append({'symbol': sym, 'category_name': cat_name, 'quantity': qty,
                         'average_price': avg, 'last_price': ltp,
                         'invested': invested * weight, 'current_value': current * weight,
                         'pnl': pnl * weight, 'account_label': acct})
    return pd.DataFrame(rows, columns=cols)


def unrealized_pnl_by_category(open_trades_df):
    """Unrealized P&L for Oracle's own recorded Open trades, grouped by
    category — priced against the last-synced Kite last_price (matched by
    symbol). Deliberately NOT the full holdings-reconciliation view (that's
    what tag_holdings_with_category is for) — this stays scoped to exactly
    what Oracle's Budget Tracking already claims is open per category, just
    marked to market price instead of buy price.

    Returns a DataFrame: category_name, unrealized_pnl, unpriced_count
    (unpriced_count = open trades in that category with no matching synced
    quote — sold outside Oracle, delisted, symbol renamed, or nothing
    synced yet).
    """
    cols = ['category_name', 'unrealized_pnl', 'unpriced_count']
    if open_trades_df is None or open_trades_df.empty:
        return pd.DataFrame(columns=cols)
    price_map = {(h.get('tradingsymbol') or '').upper(): float(h.get('last_price') or 0)
                 for h in get_holdings()}
    rows = []
    for cat_name, grp in open_trades_df.groupby('category_name'):
        total = 0.0
        unpriced = 0
        for _, r in grp.iterrows():
            ltp = price_map.get((r['symbol'] or '').upper())
            if ltp:
                total += (ltp - float(r['my_buy_price'])) * float(r['my_buy_qty'])
            else:
                unpriced += 1
        rows.append({'category_name': cat_name, 'unrealized_pnl': total, 'unpriced_count': unpriced})
    return pd.DataFrame(rows, columns=cols)


def unrealized_pnl_for_oracle_trades(open_trades_df):
    """Portfolio-level total — sum of unrealized_pnl_by_category(), so the
    two views can never drift apart.

    Returns (total_pnl, unpriced_count).
    """
    by_cat = unrealized_pnl_by_category(open_trades_df)
    if by_cat.empty:
        return 0.0, 0
    return float(by_cat['unrealized_pnl'].sum()), int(by_cat['unpriced_count'].sum())


def open_orders_count():
    """Orders currently OPEN at the broker (as of last sync) — triggered
    GTT sells and pending buy orders not yet filled."""
    return sum(1 for o in get_orders() if str(o.get('status', '')).upper() == 'OPEN')


# ── Needs Review: manual retry-buy for a symbol the bot refused to guess ──
# Always against the NEW account — the buy bot only ever runs on NEW going
# forward, so a manual retry belongs there too.

def get_quote(symbol, account_label='NEW'):
    """Live last-traded price for a symbol. Only used by the retry-buy
    preview, to price a manually-corrected symbol before anyone commits to
    buying it. Kite's OMS /quote endpoint 400s on this web session
    regardless of symbol (same failure kite_client.py's get_market_price()
    silently falls back from) — so this mirrors that same Kite-then-Google-
    Finance fallback rather than surfacing a false "invalid symbol" error."""
    try:
        enctoken = _get_enctoken(account_label)
        r = requests.get(f'{OMS_BASE}/quote', params={'i': f'NSE:{symbol}'},
                          headers=kite_headers(enctoken), timeout=15)
        data = r.json()
        ltp = data.get('data', {}).get(f'NSE:{symbol}', {}).get('last_price')
        if ltp:
            return float(ltp)
    except Exception:
        pass

    gf_url = f'https://www.google.com/finance/quote/{symbol}:NSE'
    r = requests.get(gf_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
    for pattern in [
        r'data-last-price="([\d.]+)"',
        r'"price":"([\d.]+)"',
        r'<div[^>]*class="[^"]*YMlKec[^"]*"[^>]*>([\d,]+(?:\.[\d]+)?)<',
        r'([\d,]+\.\d+)\s*</div>',
    ]:
        match = re.search(pattern, r.text)
        if match:
            try:
                price = float(match.group(1).replace(',', ''))
                if price > 0:
                    return price
            except ValueError:
                pass
    raise RuntimeError(f"Could not get a live price for {symbol} from Kite or Google Finance — "
                       f"double check the symbol is correct")


def preview_retry_buy(trade_id, symbol):
    """Compute what a retry-buy WOULD do for a NEEDS_REVIEW trade, given a
    manually-corrected symbol — live quote, cap-type lookup, the same
    price/order-type/qty decision and budget check main.py's process_tip()
    uses. Places NO order. Always call this before confirm_retry_buy() and
    show the result to a human — never skip straight to confirming."""
    rows = db.needs_review_trades()
    match = rows[rows['trade_id'] == trade_id]
    if match.empty:
        raise RuntimeError(f"Trade #{trade_id} not found (or no longer NEEDS_REVIEW)")
    row = match.iloc[0]

    symbol = symbol.strip().upper()
    if not symbol:
        raise RuntimeError("Enter a symbol first")

    category = row['category_name']
    email_price = float(row['recommended_price'])
    mkt_price = get_quote(symbol, 'NEW')

    if mkt_price < email_price:
        buy_price, order_type = mkt_price, 'MARKET'
    else:
        buy_price, order_type = email_price, 'LIMIT'

    qty = max(1, math.floor(INVEST_AMT / buy_price))
    actual_cost = qty * buy_price

    cap_type = db.get_stock_cap_type(symbol)
    budget_ok, category_id = db.check_budget_available(category, cap_type, actual_cost)

    return {
        'trade_id': int(trade_id), 'category': category, 'category_id': category_id,
        'stock_name': row['stock_name'], 'symbol': symbol, 'cap_type': cap_type,
        'email_price': email_price, 'mkt_price': mkt_price,
        'buy_price': buy_price, 'order_type': order_type,
        'qty': qty, 'actual_cost': actual_cost, 'budget_ok': budget_ok,
    }


def confirm_retry_buy(preview):
    """Place the real buy order for a previewed retry-buy, then update
    Oracle. The ONLY function in this module that trades — takes exactly
    the dict preview_retry_buy() returned, never raw user input, so what
    gets bought is always what was already shown and reviewed."""
    if not preview.get('budget_ok'):
        raise RuntimeError("Budget check failed for this category/stock-type — "
                            "the live bot would SKIP this trade, not buy it")
    enctoken = _get_enctoken('NEW')
    payload = {
        'exchange': 'NSE', 'tradingsymbol': preview['symbol'],
        'transaction_type': 'BUY', 'quantity': preview['qty'],
        'order_type': preview['order_type'], 'product': 'CNC',
        'validity': 'DAY', 'tag': 'SPT',
    }
    if preview['order_type'] == 'LIMIT':
        payload['price'] = preview['buy_price']
    r = requests.post(f'{OMS_BASE}/orders/regular', headers=kite_headers(enctoken), data=payload, timeout=15)
    res = r.json()
    if res.get('status') != 'success':
        raise RuntimeError(f"Buy failed: {res.get('message')}")
    buy_order_id = res['data']['order_id']

    db.apply_retry_buy(
        trade_id=preview['trade_id'], category_id=preview['category_id'],
        symbol=preview['symbol'], stock_type=preview['cap_type'],
        order_type=preview['order_type'], buy_order_id=buy_order_id,
        market_price=preview['mkt_price'], buy_price=preview['buy_price'],
        qty=preview['qty'], invested_amount=preview['actual_cost'],
    )
    return buy_order_id


if __name__ == '__main__':
    # Read-only smoke test against the live account — run manually on the VM.
    # sync_now() is the only live call; everything else reads Oracle back.
    print("Sync result:", sync_now())
    print("Holdings summary:", holdings_summary())
    print("GTT summary:", {k: v for k, v in gtt_summary().items() if k != 'raw'})
    print("Orders today summary:", {k: v for k, v in orders_today_summary().items() if k != 'raw'})
