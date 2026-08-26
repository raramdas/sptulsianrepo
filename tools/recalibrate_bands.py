"""Find lite-engine thresholds that reproduce the ORIGINAL policy's band shares.

The 85/75 cutoffs were percentile statements about the full engine's score
distribution, not statements about the world. Ported to a differently-shaped
distribution the numbers survive but the policy does not, so recalibrate on
the quantity that was actually intended: what share of recommended names
lands in each band.
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, "/home/ubuntu/stockbot/dashboard")
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
import db, pandas as pd, numpy as np
from lib import conviction_lite as cl

# Original policy, as specified against the full engine.
FULL_HI, FULL_LO = 85, 75

full = db._df("SELECT score FROM conviction_scores WHERE model='full' AND score IS NOT NULL")['score']
share_hi = (full > FULL_HI).mean()
share_mid = ((full >= FULL_LO) & (full <= FULL_HI)).mean()
share_lo = (full < FULL_LO).mean()
print(f"=== ORIGINAL INTENT (full engine, n={len(full)}) ===")
print(f"  Rs 25,000 band : {share_hi*100:5.1f}% of names")
print(f"  Rs 10,000 band : {share_mid*100:5.1f}%")
print(f"  not bought     : {share_lo*100:5.1f}%")

# Population the policy actually applies to: every name the advisory has put
# up, not just currently-open ones.
syms = db._df("""SELECT symbol, MAX(target_price) target_price
                 FROM trades WHERE symbol IS NOT NULL
                 GROUP BY symbol""")
print(f"\n=== scoring {len(syms)} distinct recommended symbols with the lite engine ===")
rows = []
for _, r in syms.iterrows():
    try:
        t = float(r['target_price']) if r['target_price'] is not None else None
    except (TypeError, ValueError):
        t = None
    res = cl.score_symbol(r['symbol'], spt_target=t, log=lambda m: None)
    if res['score'] is not None:
        rows.append({'symbol': r['symbol'], 'score': res['score']})
lite = pd.DataFrame(rows)['score']
print(f"  scored: {len(lite)}   median={lite.median():.1f}  "
      f"p10={lite.quantile(.10):.1f}  p90={lite.quantile(.90):.1f}  "
      f"min={lite.min():.1f}  max={lite.max():.1f}")

# Match on share of names.
raw_hi = lite.quantile(1 - share_hi)
raw_lo = lite.quantile(share_lo)
print(f"\n=== matching percentiles on the lite distribution ===")
print(f"  {(1-share_hi)*100:.1f}th pct -> {raw_hi:.2f}")
print(f"  {share_lo*100:.1f}th pct -> {raw_lo:.2f}")

NEW_HI, NEW_LO = round(raw_hi), round(raw_lo)
print(f"  rounded: HI={NEW_HI}  LO={NEW_LO}")

if NEW_LO < cl.ACCEPT_FLOOR:
    print(f"\n  !! WARNING: LO={NEW_LO} is below the engine's ACCEPT_FLOOR "
          f"({cl.ACCEPT_FLOOR}) — that would buy names the engine rejects.")
    NEW_LO = cl.ACCEPT_FLOOR
    print(f"     raised LO to {NEW_LO} to keep sizing coherent with the verdict.")

print(f"\n=== RESULT: lite thresholds {NEW_HI} / {NEW_LO} ===")
got_hi = (lite > NEW_HI).mean()
got_mid = ((lite >= NEW_LO) & (lite <= NEW_HI)).mean()
got_lo = (lite < NEW_LO).mean()
print(f"{'band':<22}{'intended':>10}{'achieved':>10}{'n':>6}")
for lbl, want, got, n in (
    ('Rs 25,000', share_hi, got_hi, (lite > NEW_HI).sum()),
    ('Rs 10,000', share_mid, got_mid, ((lite >= NEW_LO) & (lite <= NEW_HI)).sum()),
    ('not bought', share_lo, got_lo, (lite < NEW_LO).sum()),
):
    print(f"{lbl:<22}{want*100:9.1f}%{got*100:9.1f}%{n:>6}")

print(f"\n=== capital per 100 recommendations ===")
for lbl, hi, lo, dist in (('OLD 85/75 on lite', 85, 75, lite),
                          (f'NEW {NEW_HI}/{NEW_LO} on lite', NEW_HI, NEW_LO, lite),
                          ('flat Rs 5,000 (today)', None, None, lite)):
    if hi is None:
        spend = 100 * 5000
        print(f"  {lbl:<24} Rs {spend:>10,}   (all 100 bought)")
    else:
        n_hi = (dist > hi).mean() * 100
        n_mid = ((dist >= lo) & (dist <= hi)).mean() * 100
        spend = n_hi * 25000 + n_mid * 10000
        print(f"  {lbl:<24} Rs {spend:>10,.0f}   "
              f"({n_hi:.0f} at 25K, {n_mid:.0f} at 10K, {100-n_hi-n_mid:.0f} skipped)")

print(f"\n=== today's four under the new bands ===")
today = db._df("""
  SELECT t.symbol, c.score FROM trades t
  JOIN (SELECT trade_id, MAX(score_id) sid FROM conviction_scores GROUP BY trade_id) l
    ON l.trade_id = t.trade_id
  JOIN conviction_scores c ON c.score_id = l.sid
  WHERE t.buy_date >= TRUNC(SYSDATE) ORDER BY c.score DESC
""")
for _, r in today.iterrows():
    s = r['score']
    amt = 'Rs 25,000' if s > NEW_HI else ('Rs 10,000' if s >= NEW_LO else 'not bought')
    print(f"  {r['symbol']:<10} {s:>6.1f}  ->  {amt}")

print(f"\nCONVICTION_SIZING = [({NEW_HI}, 25000), ({NEW_LO}, 10000)]")
print(f"CONVICTION_MIN_SCORE = {NEW_LO}")
