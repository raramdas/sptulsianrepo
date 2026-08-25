#!/usr/bin/env python3
"""
spt_watchdog.py — alerts when the buy pipeline has gone dark.

Covers the two upstream jobs the 11:00 buy run now depends on. Since the buy
gates were tightened, a failure in either does not raise anything — it just
quietly stops buying, and the trade log reads the same as a day when nothing
was worth buying.

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

Second check: are the trades awaiting purchase actually scored and
attributed? The buy run refuses to size a trade with no conviction score,
and holds one whose have_interest the scrape never filled in. Both are
checked directly against the trades table rather than through a "the job
ran" proxy, which would miss a run that succeeded overall but failed on
these particular symbols.

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

from lib.config import log, IST, GMAIL_USER, GMAIL_APP_PASSWORD
from lib.spt_scraper import read_watermark
from lib.budget_manager import (unscored_pending_buys, pending_buys_missing_interest,
                                 close_oracle_connection)

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


def check_buy_inputs(now_ist=None):
    """Are today's pending buys ready to be sized? Returns a list of problems.

    Deliberately runs after the 9:30 recommend and 10:15 conviction jobs, so
    anything still missing here is a real gap that will block the 11:00 buy.
    """
    now_ist = now_ist or datetime.now(IST)
    if now_ist.weekday() >= 5:
        return []            # no recommendations expected at the weekend

    problems = []
    unscored = unscored_pending_buys()
    if unscored:
        names = ', '.join(f"#{r['trade_id']} {r['stock_name']}" for r in unscored[:6])
        problems.append(
            f"{len(unscored)} trade(s) awaiting purchase have NO conviction score, so "
            f"the buy run cannot size them: {names}"
            f"{' ...' if len(unscored) > 6 else ''}. "
            f"Re-run: python3 main_conviction.py")

    blank = pending_buys_missing_interest()
    if blank:
        names = ', '.join(f"#{r['trade_id']} {r['stock_name']}" for r in blank[:6])
        problems.append(
            f"{len(blank)} trade(s) awaiting purchase have no SPTulsian "
            f"'Have Interest' value — the scrape found no matching live call, so the "
            f"buy is held: {names}{' ...' if len(blank) > 6 else ''}. "
            f"Re-run: python3 main_recommend.py")
    return problems


def send_alert(reason):
    """Email the alert to the same mailbox the SPTulsian tips arrive in."""
    msg = EmailMessage()
    msg['Subject'] = 'Stockbot ALERT: the buy pipeline is degraded'
    msg['From'] = GMAIL_USER
    msg['To'] = GMAIL_USER
    msg.set_content(
        f"{reason}\n\n"
        f"WHY THIS MATTERS: the buy run only buys a stock when SPTulsian discloses\n"
        f"'Have Interest' AND the conviction engine has scored it. If either input\n"
        f"is missing, the trade is HELD and retried for up to 2 more days, then\n"
        f"dropped. Fixing this today means the opportunity is not lost.\n\n"
        f"Targets and timeframes may also not be set, so no GTT would be placed.\n\n"
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
    problems = []

    is_stale, reason = evaluate()
    if is_stale:
        problems.append(f"SCRAPER: {reason}")
        log(f"SPTulsian scraper STALE: {reason}")
    else:
        log(f"SPTulsian scraper healthy. {reason}")

    # Checked even when the scrape is healthy: the conviction job can fail on
    # its own, and that blocks buying just as effectively.
    try:
        for p in check_buy_inputs():
            problems.append(f"BUY INPUTS: {p}")
            log(f"BUY INPUTS: {p}")
    except Exception as e:
        log(f"WARNING: could not check buy inputs: {e}")
    finally:
        close_oracle_connection()

    if not problems:
        log("Buy inputs healthy — every pending trade is scored and attributed.")
        return 0

    if check_only:
        return 1
    try:
        send_alert('\n\n'.join(problems))
        log(f"Alert email sent ({len(problems)} problem(s)).")
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
