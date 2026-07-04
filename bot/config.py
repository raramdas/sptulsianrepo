#!/usr/bin/env python3
"""
config.py — shared configuration, constants, and small utilities used across
all modules of the stock tip automation system.

Test independently:
    python3 -c "from config import log; log('test message')"
"""
import os
import pytz
from datetime import datetime
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

# ── General settings ─────────────────────────────────────────────
SHEET_ID   = '1QdOHb2xWuBmF_OF1cReOXa9pQKhFFX2u266JgvFpK3M'
SHEET_TAB  = 'Master Database'
EXCHANGE   = 'NSE'
INVEST_AMT = 5000
DRY_RUN    = True
GTT_DRY_RUN = False   # Independent DRY_RUN flag for the GTT/sell-side bot
TEST_DATE  = '30-Jun-2026'   # Set to None for live email date

IST      = pytz.timezone('Asia/Kolkata')
OMS_BASE = 'https://kite.zerodha.com/oms'

# ── Credentials (from .env) ──────────────────────────────────────
KITE_API_KEY       = os.environ['KITE_API_KEY']
KITE_API_SECRET    = os.environ['KITE_API_SECRET']
ZERODHA_USER_ID    = os.environ['ZERODHA_USER_ID']
ZERODHA_PASSWORD   = os.environ['ZERODHA_PASSWORD']
TOTP_SECRET        = os.environ['ZERODHA_TOTP_SECRET']
GMAIL_USER         = os.environ['GMAIL_USER']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
GSHEET_CREDS_FILE  = os.environ.get('GSHEET_CREDS_JSON', '/home/ubuntu/gsheet_creds.json')
SPT_USERNAME       = os.environ.get('SPT_USERNAME', '')
SPT_PASSWORD       = os.environ.get('SPT_PASSWORD', '')

ORACLE_USER            = os.environ['ORACLE_USER']
ORACLE_PASSWORD        = os.environ['ORACLE_PASSWORD']
ORACLE_DSN             = os.environ['ORACLE_DSN']
ORACLE_WALLET_DIR      = os.environ['ORACLE_WALLET_DIR']
ORACLE_WALLET_PASSWORD = os.environ['ORACLE_WALLET_PASSWORD']


def log(msg):
    """Timestamped print, used by every module for consistent log formatting."""
    ts = datetime.now(IST).strftime('%H:%M:%S')
    print(f"[{ts} IST] {msg}", flush=True)


def clean_float(val):
    """Convert string like '8,416.00' or '408' to float safely."""
    try:
        return float(str(val).replace(',', '').strip())
    except Exception:
        return None


if __name__ == '__main__':
    # Quick self-test: confirm all env vars loaded and log() works
    log("config.py self-test OK")
    print("SHEET_ID:", SHEET_ID)
    print("INVEST_AMT:", INVEST_AMT)
    print("DRY_RUN:", DRY_RUN)
    print("TEST_DATE:", TEST_DATE)
    print("clean_float('8,416.00') =", clean_float('8,416.00'))
