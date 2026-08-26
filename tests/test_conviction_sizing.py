"""Verify recalibrated conviction sizing. Mocks the DB; places no orders."""
import sys
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
sys.path.insert(0, "/home/ubuntu/stockbot/dashboard")

import main
from lib.config import (CONVICTION_SIZING_ENABLED, CONVICTION_SIZING,
                        CONVICTION_MIN_SCORE, INVEST_AMT, REQUIRE_HAVE_INTEREST)

P = F = 0
def ok(cond, label, extra=''):
    global P, F
    if cond:
        P += 1; print(f"  PASS  {label}")
    else:
        F += 1; print(f"  FAIL  {label}  {extra}")

print("=== effective policy ===")
print(f"  CONVICTION_SIZING_ENABLED = {CONVICTION_SIZING_ENABLED}")
print(f"  CONVICTION_SIZING         = {CONVICTION_SIZING}")
print(f"  CONVICTION_MIN_SCORE      = {CONVICTION_MIN_SCORE}")
print(f"  REQUIRE_HAVE_INTEREST     = {REQUIRE_HAVE_INTEREST}")
print(f"  INVEST_AMT (flat fallback)= {INVEST_AMT}")

ok(CONVICTION_SIZING_ENABLED is True, "sizing is enabled")
ok(CONVICTION_SIZING == [(85, 25000), (63, 10000)], "bands are 85/25k, 63/10k", CONVICTION_SIZING)
ok(CONVICTION_MIN_SCORE == 63, "floor is 63", CONVICTION_MIN_SCORE)

# Mock the conviction lookup so nothing touches Oracle.
_scores = {}
main.get_latest_conviction = lambda tid: _scores.get(tid)

def size(score, have_interest='Have Interest', verdict='ACCEPT', evidence=100):
    tid = 9000 + len(_scores)
    _scores[tid] = (None if score == 'MISSING'
                    else {'score': score, 'verdict': verdict, 'evidence_pct': evidence})
    return main.decide_position_size({'trade_id': tid, 'have_interest': have_interest})

print("\n=== band boundaries ===")
cases = [
    (100.0, 25000, "100 -> 25k"),
    (86.0,  25000, "86 (just above 85) -> 25k"),
    (85.0,  10000, "85 exactly -> 10k (band is > 85, not >=)"),
    (81.1,  10000, "81.1 (today's IDEA) -> 10k"),
    (70.0,  10000, "70 -> 10k"),
    (63.0,  10000, "63 exactly (== floor) -> 10k"),
]
for score, expect, label in cases:
    amt, reason, retry = size(score)
    ok(amt == expect, label, f"got {amt} reason={reason}")

print("\n=== below the floor: not bought, and NOT retried ===")
for score, label in [(62.7, "62.7 (today's POLYCAB, just under)"),
                     (50.0, "50 (engine ACCEPT_FLOOR)"),
                     (10.6, "10.6 (today's MEIL)"),
                     (6.7,  "6.7 (today's OMPOWER)")]:
    amt, reason, retry = size(score)
    ok(amt is None, f"{label} -> not bought", f"got {amt}")
    ok(retry is False, f"{label} -> not retryable (a judgement, not a data gap)", retry)

print("\n=== floor is coherent with the engine's own verdict ===")
from lib import conviction_lite as cl
ok(CONVICTION_MIN_SCORE >= cl.ACCEPT_FLOOR,
   f"floor {CONVICTION_MIN_SCORE} >= engine ACCEPT_FLOOR {cl.ACCEPT_FLOOR} "
   f"(never funds a name the engine rejects)")

print("\n=== unknowns are retried, not treated as zero ===")
amt, reason, retry = size('MISSING')
ok(amt is None and retry is True, "no score on file -> skip AND retry", f"{amt} {retry}")

amt, reason, retry = size(None, verdict='INSUFFICIENT EVIDENCE', evidence=25)
ok(amt is None and retry is False,
   "score withheld for lack of evidence -> skip, NOT retried (re-running won't help)",
   f"{amt} {retry}")

print("\n=== Have Interest gate still independent of conviction ===")
amt, reason, retry = size(95.0, have_interest='No Interest')
ok(amt is None and retry is False, "No Interest blocks even a 95", f"{amt} {reason}")
amt, reason, retry = size(95.0, have_interest='')
ok(amt is None and retry is True, "blank interest -> skip but retry (unknown, not refused)", f"{amt} {retry}")

print("\n=== today's four, as tomorrow's run would size them ===")
for sym, score in [('IDEA', 81.1), ('POLYCAB', 62.7), ('MEIL', 10.6), ('OMPOWER', 6.7)]:
    amt, reason, retry = size(score)
    print(f"  {sym:<9} {score:>5.1f}  ->  " +
          (f"Rs {amt:,}" if amt else f"not bought ({reason})"))

print(f"\n{'='*54}\n  {P} passed, {F} failed\n{'='*54}")
sys.exit(1 if F else 0)
