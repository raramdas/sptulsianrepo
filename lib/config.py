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
INVEST_AMT = 5000   # legacy default; the buy path now sizes by conviction

# ── Buy policy ───────────────────────────────────────────────────────────
# Position size is set by the conviction score (see lib/conviction.py), which
# is written by main_conviction.py at 10:15, before the 11:00 buy run.
#
# NOTE: this deliberately reverses the engine's original display-only status.
# Conviction now gates and sizes real orders, so a scoring bug is a money bug.
# The score has not been validated against realised outcomes.
#
# Read as: the first band whose floor the score EXCEEDS wins. A score of
# exactly 85 falls in the 75-85 band; 85.1 is in the top band.
CONVICTION_SIZING = [
    (85, 25000),   # score > 85        -> Rs 25,000
    (75, 10000),   # 75 <= score <= 85 -> Rs 10,000
]
CONVICTION_MIN_SCORE = 75    # below this: do not buy at all
REQUIRE_HAVE_INTEREST = True # skip unless SPTulsian discloses "Have Interest"

# A buy whose LIMIT order does not fill is re-attempted on later trading days,
# so a call is not lost just because the price never came back that session.
# Total attempts = 1 initial + BUY_RETRY_DAYS retries.
BUY_RETRY_DAYS = 2
DRY_RUN    = False
GTT_DRY_RUN = False   # Independent DRY_RUN flag for the GTT/sell-side bot
TEST_DATE  = None   # Set to None for live email date

IST      = pytz.timezone('Asia/Kolkata')
OMS_BASE = 'https://kite.zerodha.com/oms'

# ── Credentials (from .env) ──────────────────────────────────────
KITE_API_KEY       = os.environ['KITE_API_KEY']
KITE_API_SECRET    = os.environ['KITE_API_SECRET']

# KITE_ACCOUNT lets a one-off manual run target the OLD (personal, pre-cutover)
# Zerodha account instead of the NEW (dedicated automation) account, without
# touching .env or affecting the cron job's default. Cron never sets this, so
# it always resolves to the NEW account. Usage: KITE_ACCOUNT=OLD python3 main_gtt_oracle.py
KITE_ACCOUNT = os.environ.get('KITE_ACCOUNT', 'NEW').upper()
if KITE_ACCOUNT == 'OLD':
    ZERODHA_USER_ID  = os.environ['ZERODHA_OLD_USER_ID']
    ZERODHA_PASSWORD = os.environ['ZERODHA_OLD_PASSWORD']
    TOTP_SECRET      = os.environ['ZERODHA_OLD_TOTP_SECRET']
else:
    ZERODHA_USER_ID  = os.environ['ZERODHA_USER_ID']
    ZERODHA_PASSWORD = os.environ['ZERODHA_PASSWORD']
    TOTP_SECRET      = os.environ['ZERODHA_TOTP_SECRET']

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
