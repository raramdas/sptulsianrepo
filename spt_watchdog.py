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

Third check: did the buy run actually act? The first two run BEFORE the buy
and only verify its inputs. Neither can see a run that had good inputs and
then failed while executing — which is what happened on 2026-09-01 and 02,
when a closed database connection left main.py reporting "Buy Phase
complete" after buying nothing, twice, for two days, while this watchdog
reported healthy each morning. check_buy_outcome() runs after and asks the
ledger instead: a trade still PENDING_BUY past 11:15 means the run never
reached it, whatever the cause.

Hence two cron invocations, 10:45 and 11:15. The check is time-gated rather
than split into a second script, so the earlier run skips it and there is
one code path to maintain.

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

from lib.config import (log, IST, GMAIL_USER, GMAIL_APP_PASSWORD,
                        CONVICTION_SIZING_ENABLED, BUY_RETRY_DAYS)
from lib.spt_scraper import read_watermark
from lib.budget_manager import (unscored_pending_buys, pending_buys_missing_interest,
                                 advisory_sells_today, unacted_pending_buys,
                                 stale_pending_fills, close_oracle_connection)

ABSOLUTE_STALE_HOURS = 30
TRADING_DAY_DEADLINE = (10, 30)   # 10:30 IST — after the 9:30 recommend run
BUY_OUTCOME_DEADLINE = (11, 15)   # 11:15 IST — after the 11:00 buy run


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
    # Only a blocker while conviction actually gates buying. With flat sizing
    # a missing score costs visibility, not a trade, so it must not page.
    if CONVICTION_SIZING_ENABLED:
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

    # A SELL is the one advisory signal the system cannot act on by itself.
    # Everything else here reports something that stopped a buy; this reports
    # a position we may still be holding, with a GTT resting at a target the
    # advisory has just withdrawn. Nothing in the pipeline will close it, so
    # if this does not reach a human it reaches no one.
    try:
        for r in advisory_sells_today():
            held = r.get('held_qty') or 0
            problems.append(
                f"SPTulsian issued a SELL on {r['stock_name']} "
                f"({r.get('symbol') or 'unresolved'}). It was NOT bought. "
                + (f"You still hold {held} share(s), and the open position's GTT is "
                   f"still resting at the original target — this call withdraws that "
                   f"thesis. Decide whether to exit."
                   if held else
                   "No open position in this symbol, so nothing to unwind."))
    except Exception as e:
        log(f"WARNING: could not check advisory sells: {e}")
    return problems


def check_buy_outcome(now_ist=None):
    """Did the 11:00 buy run actually do anything? Returns a list of problems.

    Every other check here runs BEFORE the buy and asks whether its inputs are
    ready. None of them can see a run that had good inputs and then failed
    while executing — which is what happened on 2026-09-01 and 02, when a
    closed database connection left the job reporting "Buy Phase complete"
    after buying nothing, twice, unnoticed for two days.

    So this one runs after. It is time-gated rather than split into a separate
    script: the 10:45 invocation returns early (the buy has not run yet, so
    everything is legitimately still PENDING_BUY), and the 11:15 invocation
    does the work. One code path, one cron line added.
    """
    now_ist = now_ist or datetime.now(IST)
    if now_ist.weekday() >= 5:
        return []
    if (now_ist.hour, now_ist.minute) < BUY_OUTCOME_DEADLINE:
        return []            # the buy run has not happened yet

    problems = []
    stuck = unacted_pending_buys()
    if stuck:
        names = ', '.join(f"#{r['trade_id']} {r['stock_name']}" for r in stuck[:6])
        problems.append(
            f"{len(stuck)} trade(s) are STILL PENDING_BUY after the 11:00 run: "
            f"{names}{' ...' if len(stuck) > 6 else ''}. The buy job leaves nothing "
            f"in that state — every trade it considers ends up Open, PENDING_FILL, "
            f"SKIPPED or requeued — so it did not reach these. Check bot.log for an "
            f"error after 'Buy Phase starting'; a database or broker failure "
            f"mid-run still logs 'Buy Phase complete'.")

    stale = stale_pending_fills(max_days=BUY_RETRY_DAYS + 1)
    if stale:
        names = ', '.join(f"#{r['trade_id']} {r['stock_name']} ({int(r['age_days'])}d)"
                          for r in stale[:6])
        problems.append(
            f"{len(stale)} order(s) have awaited fill confirmation longer than the "
            f"{BUY_RETRY_DAYS}-day retry window: {names}"
            f"{' ...' if len(stale) > 6 else ''}. Reconciliation should promote or "
            f"requeue these every morning, so one this old means reconciliation is "
            f"not completing — and a stuck row re-triggers whatever stopped it on "
            f"every subsequent run.")
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
        # Runs only on the 11:15 invocation; the 10:45 one returns early.
        for p in check_buy_outcome():
            problems.append(f"BUY OUTCOME: {p}")
            log(f"BUY OUTCOME: {p}")
    except Exception as e:
        log(f"WARNING: could not check buy inputs: {e}")
    finally:
        close_oracle_connection()

    if not problems:
        log("Buy inputs healthy — every pending trade is scored and attributed.")
        if (datetime.now(IST).weekday() < 5
                and (datetime.now(IST).hour, datetime.now(IST).minute) >= BUY_OUTCOME_DEADLINE):
            log("Buy outcome healthy — the 11:00 run acted on everything queued.")
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
