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
# CONVICTION-BASED SIZING IS ON, recalibrated for the lite engine 2026-08-26.
#
# History matters here. It was switched on 2026-08-25 against the FULL engine
# (>85 -> Rs 25,000, 75-85 -> Rs 10,000, below 75 not bought) and reverted the
# next day: the first backtest found no detectable relationship between that
# score and subsequent excess return — symbol-level Spearman -0.127 across 37
# symbols (t=-0.76) — and the 75-85 band that would have taken most of the
# capital did WORST (-3.91% mean excess) while the sub-75 group we refused to
# buy did best (+1.01%).
#
# What changed is the engine, not that evidence. lib/conviction_lite.py scores
# a much wider distribution (median 56, range 6-100) than the full engine's
# compressed 50-87, so the OLD cutoffs would have silently become a different
# policy while wearing the same numbers: the same "75" that took 36.6% of
# names under the full engine takes only 13.9% under lite.
#
# The thresholds below are recalibrated on what was actually intended — the
# SHARE of recommended names in each band — over 43 distinct symbols:
#
#            band          intended   achieved
#            Rs 25,000         7.2%       9.3%   (4 names)
#            Rs 10,000        36.6%      34.9%  (15 names)
#            not bought       56.2%      55.8%  (24 names)
#
# CAVEAT ON THE UPPER BAND: 85 reproduces the 92.8th percentile, but at n=43
# that rests on about three names and is poorly determined. The lower cutoff
# sits near the median, where percentile estimates are most stable — so trust
# 63 considerably more than 85, and re-run the recalibration as n grows.
#
# Note this deploys MORE capital than flat sizing, not less: roughly
# Rs 581k per 100 recommendations against Rs 500k flat.
#
# The underlying caution has NOT been retired. There is still no evidence that
# any conviction score predicts returns, and the lite engine has zero closed
# trades behind it. Set this False to go back to flat INVEST_AMT.
CONVICTION_SIZING_ENABLED = True

CONVICTION_SIZING = [
    (85, 25000),   # score > 85        -> Rs 25,000
    (63, 10000),   # 63 <= score <= 85 -> Rs 10,000
]
# Equals the lower band, and sits above lib/conviction_lite.ACCEPT_FLOOR (50)
# so sizing never funds a name the engine's own verdict rejects.
CONVICTION_MIN_SCORE = 63

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
