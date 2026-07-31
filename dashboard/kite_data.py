#!/usr/bin/env python3
"""
kite_data.py — Kite/Zerodha data for the dashboard (holdings, GTT triggers,
today's order book).

IMPORTANT: this module does NOT call Kite's live API on every dashboard
view. It used to, but per explicit preference (avoid drawing Zerodha's
attention / rate limiting with frequent automated API calls from a
customer-facing web app), the only live Kite call left is sync_now() —
triggered exclusively by the "Sync Kite Data" button on Overview. Every
other function here reads back the last-synced snapshot from Oracle
(kite_holdings_snapshot / kite_gtt_snapshot / kite_orders_snapshot,
written by db.save_kite_snapshot()).

Same enctoken-based login as kite_client.py/kite_common.py (the bot side),
reused here rather than duplicated.
"""
import os
import requests
import pyotp
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import db

load_dotenv('/home/ubuntu/.env')

OMS_BASE = 'https://kite.zerodha.com/oms'

ZERODHA_USER_ID  = os.environ.get('ZERODHA_USER_ID')
ZERODHA_PASSWORD = os.environ.get('ZERODHA_PASSWORD')
TOTP_SECRET      = os.environ.get('ZERODHA_TOTP_SECRET')


def kite_headers(enctoken):
    return {'Authorization': f'enctoken {enctoken}'}


@st.cache_resource(ttl=4 * 3600)
def _get_enctoken():
    """Log into Kite via TOTP. Cached process-wide for 4 hours — mostly
    irrelevant now that syncing is manual/infrequent, but still avoids a
    double-login if Sync is clicked twice in quick succession."""
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
    Fetches holdings/GTTs/orders and persists them to Oracle; everything
    else here reads that snapshot back. Manually triggered only (the 'Sync
    Kite Data' button on Overview) — no scheduled polling."""
    holdings = _get('/portfolio/holdings')
    raw_gtts = _get('/gtt/triggers')
    orders = _get('/orders')
    flat_gtts = _flatten_raw_gtts(raw_gtts)
    synced_at = db.save_kite_snapshot(holdings, flat_gtts, orders)
    return {'holdings': len(holdings), 'gtts': len(flat_gtts), 'orders': len(orders), 'synced_at': synced_at}


def last_synced_at():
    """When the Oracle snapshot was last refreshed, or None if never synced."""
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


if __name__ == '__main__':
    # Read-only smoke test against the live account — run manually on the VM.
    # sync_now() is the only live call; everything else reads Oracle back.
    print("Sync result:", sync_now())
    print("Holdings summary:", holdings_summary())
    print("GTT summary:", {k: v for k, v in gtt_summary().items() if k != 'raw'})
    print("Orders today summary:", {k: v for k, v in orders_today_summary().items() if k != 'raw'})
