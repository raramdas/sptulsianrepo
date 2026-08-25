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
INVEST_AMT = 5000   # flat position size for every buy

# ── Buy policy ───────────────────────────────────────────────────────────
# CONVICTION-BASED SIZING AND GATING ARE OFF (reverted 2026-08-26).
#
# They were switched on 2026-08-25 (>85 -> Rs 25,000, 75-85 -> Rs 10,000,
# below 75 not bought). The first backtest, the next day, found no
# detectable relationship between the score and subsequent excess return:
# symbol-level Spearman -0.127 across 37 symbols (t=-0.76), and dropping the
# two worst symbols left -0.081. By band, the 75-85 group that would have
# received most of the capital did WORST (-3.91% mean excess) while the
# sub-75 group we refused to buy did best (+1.01%).
#
# None of that is conclusive — two months, one regime, 12 realised closes —
# but it is the absence of evidence for a rule that was spending real money,
# so sizing is flat again at INVEST_AMT and the score is informational only.
# Re-run `python3 backtest_conviction.py` as more trades close; flip this to
# True to restore banded sizing once there is something to justify it.
CONVICTION_SIZING_ENABLED = False

# Retained so the rule is ready to re-enable, and so the dashboard can keep
# colouring scores by the bands that would apply.
CONVICTION_SIZING = [
    (85, 25000),   # score > 85        -> Rs 25,000
    (75, 10000),   # 75 <= score <= 85 -> Rs 10,000
]
CONVICTION_MIN_SCORE = 75    # below this: would not be bought, when enabled

# Independent of conviction, and still ON: this is SPTulsian's own
# disclosure, not our model's opinion, and was never part of the backtest.
REQUIRE_HAVE_INTEREST = True

BUY_RETRY_DAYS = 2

# Skips caused by OUR pipeline having no data — a scrape that found no live
# call (blank have_interest) or a conviction run that never scored the trade —
# are NOT a judgement on the recommendation. They are retried on later days
# within BUY_RETRY_DAYS, so an outage costs a day rather than the opportunity.
#
# This matters: over one 10-day sample, 8 of 29 recommendations were skipped
# for infrastructure reasons, including the only two that scored high enough
# for the top position size. It also makes the watchdog's alert actionable —
# alerting on a trade that has already been permanently skipped is useless.
# Set False to go back to skipping immediately on unknown.
RETRY_ON_UNKNOWN = True
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
