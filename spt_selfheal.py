#!/usr/bin/env python3
"""
spt_selfheal.py — repair the scrape path and re-run the day's work, unattended.

WHY THIS EXISTS

Every outage this system has had was detected correctly and then lasted for
days anyway:

    2026-08-26  WARP leaked until the box OOMed          fixed by hand
    2026-09-01  a closed DB connection stopped buying    2 days, by hand
    2026-09-04  WARP tunnel died, scraper went dark      4 days, by hand
    2026-09-17  the same tunnel failure again            alerted, unattended

spt_watchdog.py did its job every time — it emailed on the morning of each
failure. The gap was never detection. It was that an email waits for a human,
and the 11:00 buy does not.

So this runs BETWEEN the 09:30 scrape and the 10:15 conviction job: if today's
scrape did not succeed, it repairs what is broken and RE-RUNS the pipeline,
while there is still time for the buy to go ahead normally. The watchdog at
10:45 then only ever fires for something this could not fix, which makes the
alert mean something again.

WHAT IT WILL AND WILL NOT DO

It will restart warp-svc, reconnect the tunnel, vacuum journald if the disk is
tight, and re-run main_recommend.py and main_conviction.py. None of those
place an order.

It will NOT touch the broker path, and it aborts every repair if the egress
invariant is violated — if direct traffic stops leaving from the registered
static IP, the correct response is to stop, not to keep fixing things, because
Zerodha binds to that IP and a proxy leak there is worse than a missed day.

It never places an order. main.py remains the only code path that spends money.

Run:
    python3 spt_selfheal.py              # repair + re-run if needed
    python3 spt_selfheal.py --check-only # report what it would do
    python3 spt_selfheal.py --force      # repair + re-run regardless
"""
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime

from lib.config import log, IST
from lib.spt_scraper import read_watermark

STATIC_IP = '140.245.226.35'      # Zerodha is bound to this; see ARCHITECTURE §3.2
PROXY = '127.0.0.1:40000'
REPO = os.path.dirname(os.path.abspath(__file__))
DISK_PCT_ALARM = 85
JOURNAL_VACUUM = '200M'


def _run(cmd, timeout=60):
    """Run a command, never raise. Returns (rc, combined output)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, ((p.stdout or '') + (p.stderr or '')).strip()
    except Exception as e:
        return 1, f'{type(e).__name__}: {e}'


def scrape_is_fresh(now_ist=None):
    """Did a scrape genuinely succeed today? The watermark is written only on a
    parsed scrape, so it is a signal a failed fetch cannot fake."""
    now_ist = now_ist or datetime.now(IST)
    wm = read_watermark()
    if not wm or not wm.get('last_success'):
        return False, 'no successful scrape has ever been recorded'
    try:
        last = datetime.fromisoformat(wm['last_success'])
    except ValueError:
        return False, f"watermark unreadable: {wm.get('last_success')!r}"
    if last.tzinfo is None:
        from datetime import timezone
        last = last.replace(tzinfo=timezone.utc)
    last_ist = last.astimezone(IST)
    if last_ist.date() == now_ist.date():
        return True, f"scrape succeeded today at {last_ist:%H:%M IST}"
    age = (now_ist - last_ist).total_seconds() / 3600
    return False, f"last success {last_ist:%Y-%m-%d %H:%M IST} ({age:.1f}h ago)"


def egress_is_safe():
    """Direct traffic must still leave from the registered static IP.

    Checked before any repair and never repaired automatically. If broker
    traffic has started leaving through the proxy, the safe action is to stop
    and shout — restarting things could just as easily entrench it, and an
    order placed from the wrong IP is a worse outcome than a missed scrape.
    """
    rc, out = _run(['curl', '-s', '--max-time', '15', 'ifconfig.me'], timeout=25)
    ip = out.strip()
    if rc != 0 or not ip:
        return None, 'could not determine direct egress IP'
    if ip != STATIC_IP:
        return False, f'direct egress is {ip}, expected {STATIC_IP}'
    return True, f'direct egress {ip} — correct'


def proxy_reaches_portal():
    rc, out = _run(['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}',
                    '--max-time', '25', '--socks5-hostname', PROXY,
                    'https://www.sptulsian.com/'], timeout=40)
    return out.strip() == '200', out.strip()


def warp_connected():
    rc, out = _run(['warp-cli', '--accept-tos', 'status'], timeout=20)
    return ('Connected' in out and 'Disconnected' not in out), out.splitlines()[0] if out else ''


def repair_warp(actions):
    """Escalating repair: reconnect, then restart the daemon, then give up.

    Ordered by blast radius. `warp-cli connect` needs no privileges and keeps
    the process; a restart drops it. Trying the cheap one first means the usual
    case — a tunnel that simply dropped — costs nothing.
    """
    ok, status = warp_connected()
    if not ok:
        actions.append(f'warp tunnel was down ({status}) — reconnecting')
        _run(['warp-cli', '--accept-tos', 'connect'], timeout=40)
        for _ in range(10):
            time.sleep(3)
            if warp_connected()[0]:
                actions.append('warp tunnel reconnected')
                break

    if proxy_reaches_portal()[0]:
        return True

    actions.append('proxy still cannot reach the portal — restarting warp-svc')
    _run(['sudo', 'systemctl', 'restart', 'warp-svc'], timeout=90)
    for _ in range(20):
        time.sleep(3)
        if proxy_reaches_portal()[0]:
            actions.append('portal reachable after warp-svc restart')
            return True
    actions.append('portal STILL unreachable after restart')
    return False


def reclaim_disk(actions):
    """Only acts when the disk is genuinely tight. A full / is what turned a
    warp leak into a dead box on 2026-08-26."""
    rc, out = _run(['df', '--output=pcent', '/'], timeout=20)
    try:
        pct = int(out.split()[-1].strip('%'))
    except Exception:
        return
    if pct < DISK_PCT_ALARM:
        return
    actions.append(f'disk at {pct}% — vacuuming journald to {JOURNAL_VACUUM}')
    _run(['sudo', 'journalctl', f'--vacuum-size={JOURNAL_VACUUM}'], timeout=120)


def rerun_pipeline(actions):
    """Re-run the two jobs that depend on the scrape. Neither places an order.

    main_recommend is safe to repeat: already_recommended_today() stops it
    duplicating rows, which it did not before 2026-09-08.
    """
    env = dict(os.environ)
    for script in ('main_recommend.py', 'main_conviction.py'):
        actions.append(f're-running {script}')
        rc, out = _run([sys.executable, os.path.join(REPO, script)], timeout=900)
        tail = out.strip().splitlines()[-1] if out.strip() else ''
        actions.append(f'  {script} exit={rc} {tail[:120]}')
        if rc != 0:
            return False
    return True


def run(check_only=False, force=False):
    now = datetime.now(IST)
    if now.weekday() >= 5 and not force:
        log('Weekend — nothing to heal.')
        return 0

    fresh, why = scrape_is_fresh(now)
    if fresh and not force:
        log(f'Scrape healthy — {why}. Nothing to do.')
        return 0

    log(f'SCRAPE NOT HEALTHY — {why}')
    actions = []

    safe, egress_why = egress_is_safe()
    if safe is False:
        # The one condition where doing nothing is correct.
        log(f'ABORTING REPAIRS: {egress_why}')
        log('Broker traffic must leave from the registered static IP. Fix the '
            'proxy scope by hand before anything else runs.')
        return 2
    log(f'  {egress_why}')

    if check_only:
        ok, status = warp_connected()
        reach, code = proxy_reaches_portal()
        log(f'  warp tunnel: {"connected" if ok else "DOWN"} ({status})')
        log(f'  portal via proxy: HTTP {code or "no response"}')
        log('  --check-only: would repair and re-run main_recommend + main_conviction')
        return 1

    reclaim_disk(actions)
    healed = repair_warp(actions)

    if healed:
        rerun_pipeline(actions)
    else:
        actions.append('skipped re-run: the portal is still unreachable')

    for a in actions:
        log(f'  {a}')

    fresh_now, why_now = scrape_is_fresh()
    if fresh_now:
        log(f'RECOVERED — {why_now}')
        return 0
    log(f'STILL BROKEN after self-heal — {why_now}')
    log('spt_watchdog.py at 10:45 will alert; this needs a human.')
    return 1


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Repair the scrape path and re-run the day.')
    ap.add_argument('--check-only', action='store_true',
                    help='Diagnose and report; change nothing')
    ap.add_argument('--force', action='store_true',
                    help='Repair and re-run even if the scrape looks healthy')
    args = ap.parse_args()
    sys.exit(run(check_only=args.check_only, force=args.force))
