"""Self-heal must repair the right things, re-run the day, and know when to stop.

No network, no VM: every subprocess call is stubbed.
"""
import sys, pathlib
from datetime import datetime, timezone, timedelta
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import spt_selfheal as sh
from lib.config import IST

P = F = 0
def ok(c, l, e=''):
    global P, F
    if c: P += 1; print(f"  PASS  {l}")
    else: F += 1; print(f"  FAIL  {l}  {e}")

NOW = IST.localize(datetime(2026, 9, 18, 10, 0))          # a Friday, 10:00 IST
_REAL_DT = sh.datetime


def freeze(when):
    """Pin spt_selfheal's clock.

    run() reads datetime.now(IST) itself, and returns immediately on a weekend.
    Without pinning, every assertion below that expects a repair silently
    became 'weekend, nothing to heal' on a Saturday or Sunday -- the suite
    passed all week and failed every weekend, which is the worst shape a test
    can have: it fails when nobody is looking and passes when they are.
    """
    class FrozenDT:
        @staticmethod
        def now(tz=None): return when
        fromisoformat = staticmethod(_REAL_DT.fromisoformat)
    sh.datetime = FrozenDT


freeze(NOW)                                               # a weekday, always

def wm(dt):
    return {'last_success': dt.astimezone(timezone.utc).isoformat()}

print("=== freshness: only today's scrape counts ===")
sh.read_watermark = lambda: wm(IST.localize(datetime(2026, 9, 18, 9, 30)))
fresh, why = sh.scrape_is_fresh(NOW)
ok(fresh, "a scrape from this morning is fresh", why)

sh.read_watermark = lambda: wm(IST.localize(datetime(2026, 9, 17, 9, 30)))
fresh, why = sh.scrape_is_fresh(NOW)
ok(not fresh, "yesterday's scrape is NOT fresh — this is the outage shape", why)
ok('24.5h ago' in why or 'ago' in why, "reports the age", why)

sh.read_watermark = lambda: None
ok(not sh.scrape_is_fresh(NOW)[0], "no watermark at all -> not fresh")

print("\n=== the egress invariant is a stop condition, not a repair ===")
calls = []
def fake_run(cmd, timeout=60):
    calls.append(cmd)
    if cmd[:2] == ['curl', '-s'] and 'ifconfig.me' in cmd:
        return 0, fake_run.direct_ip
    return 0, ''
fake_run.direct_ip = sh.STATIC_IP
sh._run = fake_run

ok(sh.egress_is_safe()[0] is True, "correct static IP -> safe")
fake_run.direct_ip = '203.0.113.9'
safe, why = sh.egress_is_safe()
ok(safe is False, "a different IP -> NOT safe", why)
ok(sh.STATIC_IP in why and '203.0.113.9' in why, "names both IPs", why)
fake_run.direct_ip = ''
ok(sh.egress_is_safe()[0] is None, "unknown -> None, neither safe nor unsafe")

print("\n=== a leaked egress aborts everything ===")
sh.read_watermark = lambda: wm(IST.localize(datetime(2026, 9, 17, 9, 30)))
repaired = []
sh.repair_warp = lambda a: repaired.append('warp') or True
sh.rerun_pipeline = lambda a: repaired.append('rerun') or True
sh.reclaim_disk = lambda a: repaired.append('disk')
fake_run.direct_ip = '203.0.113.9'
rc = sh.run()
ok(rc == 2, "exits 2 (distinct from a normal failure)", rc)
ok(repaired == [], "NO repair was attempted while egress was wrong", repaired)

print("\n=== healthy scrape: does nothing, quietly ===")
fake_run.direct_ip = sh.STATIC_IP
sh.read_watermark = lambda: wm(NOW.replace(hour=9, minute=30))
repaired.clear()
rc = sh.run()
ok(rc == 0, "exit 0", rc)
ok(repaired == [], "no repairs when nothing is broken", repaired)

print("\n=== broken scrape: repairs, then re-runs the pipeline ===")
sh.read_watermark = lambda: wm(IST.localize(datetime(2026, 9, 17, 9, 30)))
repaired.clear()
rc = sh.run()
ok('disk' in repaired, "checked disk headroom")
ok('warp' in repaired, "repaired warp")
ok('rerun' in repaired, "re-ran the pipeline")
ok(repaired.index('warp') < repaired.index('rerun'), "repair BEFORE re-run", repaired)

print("\n=== if the portal stays unreachable, it does not re-run ===")
repaired.clear()
sh.repair_warp = lambda a: repaired.append('warp') or False
rc = sh.run()
ok('rerun' not in repaired, "no pointless re-run against a dead portal", repaired)
ok(rc == 1, "exit 1 so the watchdog still escalates", rc)

print("\n=== --check-only changes nothing ===")
repaired.clear()
sh.warp_connected = lambda: (False, 'Disconnected')
sh.proxy_reaches_portal = lambda: (False, '000')
rc = sh.run(check_only=True)
ok(repaired == [], "no repair attempted", repaired)
ok(rc == 1, "reports that work is needed", rc)

print("\n=== weekends are skipped ===")
sat = IST.localize(datetime(2026, 9, 19, 10, 0))          # a Saturday
freeze(sat)
repaired.clear()
ok(sh.run() == 0 and repaired == [], "Saturday: nothing to heal")
freeze(NOW)

print(f"\n{'='*56}\n  {P} passed, {F} failed\n{'='*56}")
sys.exit(1 if F else 0)
