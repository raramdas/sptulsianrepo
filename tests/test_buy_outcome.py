"""The post-buy check must catch a run that failed after reporting success."""
import sys
from datetime import datetime
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
import spt_watchdog as w
from lib.config import IST

P = F = 0
def ok(c, l, e=''):
    global P, F
    if c: P += 1; print(f"  PASS  {l}")
    else: F += 1; print(f"  FAIL  {l}  {e}")

def at(y, m, d, hh, mm):
    return IST.localize(datetime(y, m, d, hh, mm))

# Stub the ledger queries so this needs no database.
STUCK, STALE = [], []
w.unacted_pending_buys = lambda: STUCK
w.stale_pending_fills = lambda max_days=3: STALE

print("=== time gating: the 10:45 run must not fire ===")
STUCK = [{'trade_id': 601, 'stock_name': 'Atlanta Electricals',
          'symbol': 'ATLANTAELE', 'buy_date': '2026-09-01', 'buy_attempts': 0}]
p = w.check_buy_outcome(at(2026, 9, 2, 10, 45))
ok(p == [], "10:45 (before the buy) reports nothing — trades are legitimately queued", p)

print("\n=== 11:15: the same state IS a problem ===")
p = w.check_buy_outcome(at(2026, 9, 2, 11, 15))
ok(len(p) == 1, "one problem raised", p)
print(f"  {p[0][:150]}...")
ok('STILL PENDING_BUY' in p[0], "names the actual symptom")
ok('Atlanta Electricals' in p[0], "identifies the trade")
ok('Buy Phase complete' in p[0], "tells the reader the log will look healthy")

print("\n=== the real 2026-09-01 state would have fired ===")
STUCK = [{'trade_id': i, 'stock_name': n, 'symbol': s,
          'buy_date': '2026-09-01', 'buy_attempts': 0}
         for i, n, s in [(601, 'Atlanta Electricals', 'ATLANTAELE'),
                         (602, 'Zee Ent', 'ZEEL'),
                         (603, 'Shriram Finance', 'SHRIRAMFIN'),
                         (604, 'SBI', 'SBIN')]]
p = w.check_buy_outcome(at(2026, 9, 1, 11, 15))
ok(len(p) == 1 and '4 trade(s)' in p[0],
   "would have alarmed on Sep 1 at 11:15, not Sep 2 at lunchtime", p[0][:80] if p else p)

print("\n=== stale PENDING_FILL is caught too (#581 sat 2 days) ===")
STUCK = []
STALE = [{'trade_id': 581, 'stock_name': 'Atlanta Electricals', 'symbol': 'ATLANTAELE',
          'buy_order_id': '260831220569319', 'buy_attempts': 1, 'age_days': 2}]
p = w.check_buy_outcome(at(2026, 9, 2, 11, 15))
ok(len(p) == 1 and '#581' in p[0], "stuck fill raised", p[0][:80] if p else p)
ok('reconciliation' in p[0].lower(), "points at reconciliation, the actual cause")

print("\n=== healthy state stays silent ===")
STUCK, STALE = [], []
ok(w.check_buy_outcome(at(2026, 9, 2, 11, 15)) == [], "nothing queued -> no alarm")
ok(w.check_buy_outcome(at(2026, 9, 2, 16, 0)) == [], "later in the day, still quiet")

print("\n=== weekends are exempt ===")
STUCK = [{'trade_id': 601, 'stock_name': 'X', 'symbol': 'X',
          'buy_date': '2026-09-01', 'buy_attempts': 0}]
ok(w.check_buy_outcome(at(2026, 9, 5, 11, 15)) == [], "Saturday: no buy run expected")
ok(w.check_buy_outcome(at(2026, 9, 6, 11, 15)) == [], "Sunday: no buy run expected")

print(f"\n{'='*58}\n  {P} passed, {F} failed\n{'='*58}")
sys.exit(1 if F else 0)
