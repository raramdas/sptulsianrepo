#!/usr/bin/env python3
"""
kite_data.py — read-only Kite/Zerodha live data for the dashboard (holdings,
GTT triggers, today's order book). Same enctoken-based login as
kite_client.py/kite_common.py (the bot side), reused here rather than
duplicated, and reimplemented as read-only for the dashboard.

The dashboard's Oracle `trades` table reflects what the bots *recorded*, not
necessarily today's broker truth (a GTT can trigger before the reconciliation
script runs, an order can get rejected, etc). This module fetches live state
directly from Kite so the Overview page can show and cross-check against it.

Login is cached process-wide via st.cache_resource so a page view doesn't
trigger a fresh Kite login (password + TOTP) every time — that would risk
tripping Zerodha's login rate limiting across every dashboard viewer/rerun.
Data calls are cached briefly (60s) so navigating between pages doesn't
refetch on every rerun either.
"""
import os
import requests
import pyotp
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

OMS_BASE = 'https://kite.zerodha.com/oms'

ZERODHA_USER_ID  = os.environ.get('ZERODHA_USER_ID')
ZERODHA_PASSWORD = os.environ.get('ZERODHA_PASSWORD')
TOTP_SECRET      = os.environ.get('ZERODHA_TOTP_SECRET')


def kite_headers(enctoken):
    return {'Authorization': f'enctoken {enctoken}'}


@st.cache_resource(ttl=4 * 3600)
def _get_enctoken():
    """Log into Kite via TOTP. Cached process-wide for 4 hours so this runs
    at most a handful of times a day, not on every dashboard view."""
    if not (ZERODHA_USER_ID and ZERODHA_PASSWORD and TOTP_SECRET):
        raise RuntimeError("Kite credentials not found in /home/ubuntu/.env "
                            "(ZERODHA_USER_ID / ZERODHA_PASSWORD / ZERODHA_TOTP_SECRET)")
    session = requests.Session()
    r = session.post('https://kite.zerodha.com/api/login', data={
        'user_id': ZERODHA_USER_ID, 'password': ZERODHA_PASSWORD
    }, timeout=15)
    data = r.json()
    if data.get('status') != 'success':
        raise RuntimeError(f"Kite login failed: {data.get('message')}")
    request_id = data['data']['request_id']
    totp_code = pyotp.TOTP(TOTP_SECRET).now()
    r2 = session.post('https://kite.zerodha.com/api/twofa', data={
        'user_id': ZERODHA_USER_ID, 'request_id': request_id,
        'twofa_value': totp_code, 'skip_session': ''
    }, timeout=15)
    data2 = r2.json()
    if data2.get('status') != 'success':
        raise RuntimeError(f"Kite 2FA failed: {data2.get('message')}")
    enctoken = session.cookies.get('enctoken', '')
    if not enctoken:
        raise RuntimeError('enctoken not found in cookies after Kite login')
    return enctoken


def _get(path):
    enctoken = _get_enctoken()
    r = requests.get(f'{OMS_BASE}{path}', headers=kite_headers(enctoken), timeout=15)
    data = r.json()
    if data.get('status') != 'success':
        raise RuntimeError(f"Kite API {path} failed: {data.get('message')}")
    return data.get('data', [])


@st.cache_data(ttl=60)
def get_holdings():
    """Live equity holdings: quantity, average_price, last_price, pnl, etc."""
    return _get('/portfolio/holdings')


@st.cache_data(ttl=60)
def get_gtts():
    """All GTT triggers (any status) currently on the account."""
    return _get('/gtt/triggers')


@st.cache_data(ttl=60)
def get_orders():
    """Full order book for the trading day."""
    return _get('/orders')


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
    """Return every live Kite holding tagged with the Oracle category it
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
    cols = ['symbol', 'category_name', 'quantity', 'average_price', 'last_price', 'invested', 'current_value', 'pnl']
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
        invested = qty * avg
        current = qty * ltp
        pnl = current - invested

        shares = oracle_by_symbol.get(sym)
        if not shares:
            rows.append({'symbol': sym, 'category_name': None, 'quantity': qty,
                          'average_price': avg, 'last_price': ltp,
                          'invested': invested, 'current_value': current, 'pnl': pnl})
            continue
        for cat_name, weight in shares:
            rows.append({'symbol': sym, 'category_name': cat_name, 'quantity': qty,
                         'average_price': avg, 'last_price': ltp,
                         'invested': invested * weight, 'current_value': current * weight,
                         'pnl': pnl * weight})
    return pd.DataFrame(rows, columns=cols)


def live_category_breakdown(open_trades_df):
    """Per-category live invested/current_value/pnl, plus the unmapped
    holdings (Kite holdings with no matching open Oracle trade) — the
    actionable reconciliation list for the Oracle/Kite gap.

    Returns (category_df, unmapped_df).
    """
    tagged = tag_holdings_with_category(open_trades_df)
    empty_cat = pd.DataFrame(columns=['category_name', 'invested', 'current_value', 'pnl'])
    if tagged.empty:
        return empty_cat, pd.DataFrame()

    mapped = tagged[tagged['category_name'].notna()]
    unmapped_df = tagged[tagged['category_name'].isna()].drop(columns=['category_name']).reset_index(drop=True)
    if mapped.empty:
        return empty_cat, unmapped_df
    category_df = mapped.groupby('category_name').agg(
        invested=('invested', 'sum'), current_value=('current_value', 'sum'), pnl=('pnl', 'sum')
    ).reset_index().sort_values('invested', ascending=False)
    return category_df, unmapped_df


def open_orders_count():
    """Orders currently OPEN at the broker — triggered GTT sells and pending
    buy orders not yet filled."""
    return sum(1 for o in get_orders() if str(o.get('status', '')).upper() == 'OPEN')


def flatten_gtts(gtts=None):
    """Flatten Kite's nested GTT trigger objects (condition/orders) into flat
    rows suitable for a table."""
    gtts = gtts if gtts is not None else get_gtts()
    rows = []
    for g in gtts:
        cond = g.get('condition') or {}
        orders = g.get('orders') or [{}]
        o = orders[0] if orders else {}
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


if __name__ == '__main__':
    # Read-only smoke test against the live account — run manually on the VM
    # to sanity-check endpoint shapes before wiring into the dashboard.
    print("Holdings summary:", holdings_summary())
    print("GTT summary:", {k: v for k, v in gtt_summary().items() if k != 'raw'})
    print("Orders today summary:", {k: v for k, v in orders_today_summary().items() if k != 'raw'})
