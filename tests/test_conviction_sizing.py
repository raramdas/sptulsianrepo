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
# Deliberately derived, not hardcoded: these thresholds have been recalibrated
# three times as the engine's components changed, and a test that pins them to
# literals just breaks on every legitimate recalibration. What must hold is the
# STRUCTURE — two descending bands, a floor equal to the lower one.
HI, HI_AMT = CONVICTION_SIZING[0]
LO, LO_AMT = CONVICTION_SIZING[-1]
ok(len(CONVICTION_SIZING) == 2, "two bands", CONVICTION_SIZING)
ok(HI > LO, "bands descend", CONVICTION_SIZING)
ok(HI_AMT > LO_AMT, "higher band buys more", CONVICTION_SIZING)
ok(CONVICTION_MIN_SCORE == LO, "floor equals the lower band", 
   f"floor={CONVICTION_MIN_SCORE} lower={LO}")

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
    (100.0,     HI_AMT, f"100 -> top band"),
    (HI + 1,    HI_AMT, f"{HI+1} (just above {HI}) -> top band"),
    (HI,        LO_AMT, f"{HI} exactly -> mid band (rule is > {HI}, not >=)"),
    (LO + 5,    LO_AMT, f"{LO+5} -> mid band"),
    (LO,        LO_AMT, f"{LO} exactly (== floor) -> mid band"),
]
for score, expect, label in cases:
    amt, reason, retry = size(score)
    ok(amt == expect, label, f"got {amt} reason={reason}")

print("\n=== below the floor: not bought, and NOT retried ===")
for score, label in [(LO - 0.1, f"{LO-0.1} (just under the floor)"),
                     (LO - 10,  f"{LO-10}"),
                     (10.6,     "10.6"),
                     (6.7,      "6.7")]:
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
for sym, score in [('IDEA', 89.0), ('POLYCAB', 77.0), ('MEIL', 60.0), ('OMPOWER', 35.0)]:
    amt, reason, retry = size(score)
    print(f"  {sym:<9} {score:>5.1f}  ->  " +
          (f"Rs {amt:,}" if amt else f"not bought ({reason})"))

print(f"\n{'='*54}\n  {P} passed, {F} failed\n{'='*54}")
sys.exit(1 if F else 0)
