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


if __name__ == '__main__':
    # Read-only smoke test against the live account — run manually on the VM
    # to sanity-check endpoint shapes before wiring into the dashboard.
    print("Holdings summary:", holdings_summary())
    print("GTT summary:", {k: v for k, v in gtt_summary().items() if k != 'raw'})
    print("Orders today summary:", {k: v for k, v in orders_today_summary().items() if k != 'raw'})
