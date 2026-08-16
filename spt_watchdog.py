#!/usr/bin/env python3
"""
spt_watchdog.py — alerts when the SPTulsian scraper has gone dark.

Why this exists: a dead scraper and a genuinely quiet trading day look
identical from the outside. main_recommend.py deliberately continues when a
scrape fails (a missing target must not block recording a recommendation),
so nothing else in the system would ever raise. Without this, the bot could
run blind for days while every log line still read "complete".

What it checks is the LIVENESS WATERMARK, not the calls table. The watermark
is written by spt_scraper only after a section was genuinely fetched AND
parsed — never on a mere attempt — so it is a signal a failed fetch cannot
fake. Judging freshness from the newest row in `trades` instead was rejected:
a real quiet week would then raise a false alarm.

Two independent rules:

  Absolute      watermark older than 30h        backstop across weekends and
                                                market holidays, when no
                                                scrape is expected at all
  Trading-day   on a weekday past 10:30 IST,    the absolute rule alone would
                the watermark must be from      not fire until ~19:00 the next
                today                           day — after a whole session
                                                had already run blind

Run directly (safe, read-only — sends mail only if something is wrong):
    python3 spt_watchdog.py
    python3 spt_watchdog.py --check-only   # never sends mail; exit 1 if stale
"""
import sys
import ssl
import smtplib
import argparse
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

from config import log, IST, GMAIL_USER, GMAIL_APP_PASSWORD
from spt_scraper import read_watermark

ABSOLUTE_STALE_HOURS = 30
TRADING_DAY_DEADLINE = (10, 30)  # 10:30 IST — after the 9:30 recommend run


def evaluate(now_ist=None, watermark=None):
    """Returns (is_stale, reason). Pure — no I/O beyond reading the watermark,
    so it can be unit-tested by passing both arguments."""
    now_ist = now_ist or datetime.now(IST)
    watermark = watermark if watermark is not None else read_watermark()

    if not watermark or not watermark.get('last_success'):
        return True, "No successful SPTulsian scrape has ever been recorded."

    try:
        last = datetime.fromisoformat(watermark['last_success'])
    except ValueError:
        return True, f"Watermark is unreadable: {watermark.get('last_success')!r}"
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    last_ist = last.astimezone(IST)

    age = now_ist - last_ist
    age_hours = age.total_seconds() / 3600

    # Rule 1 — absolute backstop
    if age_hours > ABSOLUTE_STALE_HOURS:
        return True, (f"Last successful scrape was {age_hours:.1f}h ago "
                      f"({last_ist:%Y-%m-%d %H:%M IST}), over the "
                      f"{ABSOLUTE_STALE_HOURS}h limit.")

    # Rule 2 — trading-day freshness
    is_weekday = now_ist.weekday() < 5
    past_deadline = (now_ist.hour, now_ist.minute) >= TRADING_DAY_DEADLINE
    if is_weekday and past_deadline and last_ist.date() != now_ist.date():
        return True, (f"It is a trading day past "
                      f"{TRADING_DAY_DEADLINE[0]:02d}:{TRADING_DAY_DEADLINE[1]:02d} IST "
                      f"but the last successful scrape was "
                      f"{last_ist:%Y-%m-%d %H:%M IST} ({age_hours:.1f}h ago) — "
                      f"today's 9:30 run did not read SPTulsian.")

    return False, (f"OK — last successful scrape {last_ist:%Y-%m-%d %H:%M IST} "
                   f"({age_hours:.1f}h ago), "
                   f"{len(watermark.get('sections_ok') or [])} section(s), "
                   f"{watermark.get('rows', 0)} row(s).")


def send_alert(reason):
    """Email the alert to the same mailbox the SPTulsian tips arrive in."""
    msg = EmailMessage()
    msg['Subject'] = 'Stockbot ALERT: SPTulsian scraper is not running'
    msg['From'] = GMAIL_USER
    msg['To'] = GMAIL_USER
    msg.set_content(
        f"{reason}\n\n"
        f"Targets and timeframes are NOT being set automatically right now.\n"
        f"Open trades will keep their existing targets; new ones will have none\n"
        f"until this is fixed, so no GTT will be placed for them.\n\n"
        f"Most likely causes, in order:\n"
        f"  1. WARP proxy down. Check:  sudo systemctl status warp-svc\n"
        f"     Then:  warp-cli status   (expect: Connected)\n"
        f"  2. Egress blocked again. Compare:\n"
        f"       curl -s ifconfig.me\n"
        f"       curl -s --socks5-hostname 127.0.0.1:40000 ifconfig.me\n"
        f"     The first MUST stay 140.245.226.35 (the broker binding).\n"
        f"  3. SPTulsian credentials changed — check SPT_USERNAME/SPT_PASSWORD.\n"
        f"  4. Portal markup changed — run:  python3 spt_capture.py --dry-run\n\n"
        f"Meanwhile you can still set targets by hand on the dashboard's\n"
        f"Set Targets page.\n"
    )
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)


def run(check_only=False):
    is_stale, reason = evaluate()
    if not is_stale:
        log(f"SPTulsian scraper healthy. {reason}")
        return 0

    log(f"SPTulsian scraper STALE: {reason}")
    if check_only:
        return 1
    try:
        send_alert(reason)
        log("Alert email sent.")
    except Exception as e:
        # Don't mask the original problem behind a mail failure.
        log(f"WARNING: could not send alert email: {e}")
    return 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--check-only', action='store_true',
                    help="Report status and exit non-zero if stale, but never send mail")
    args = ap.parse_args()
    sys.exit(run(check_only=args.check_only))
