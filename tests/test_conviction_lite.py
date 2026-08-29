"""Offline tests for conviction_lite — synthetic price series, no network."""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import numpy as np, pandas as pd
from lib import conviction_lite as cl

def series(n=500, start=100.0, drift=0.0, vol=0.01, volume=5e5, seed=1):
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(steps))
    idx = pd.bdate_range('2024-01-01', periods=n)
    return pd.DataFrame({'Close': close, 'Volume': np.full(n, volume)}, index=idx)

def run(name, hist, target, patch=True):
    cl.fetch_history = lambda s, log=None: (hist, '.NS')
    return cl.score_symbol(name, spt_target=target, log=lambda m: None)

P = F = 0
def ok(cond, label, extra=''):
    global P, F
    if cond: P += 1; print(f"  PASS  {label}")
    else:    F += 1; print(f"  FAIL  {label}  {extra}")

# SPTulsian sets targets at a near-constant ~6% (5.82-7.69% across 16 closed
# trades), so 6% is the realistic case. A 25% target is not a better trade
# under this engine, it is a less reachable one — which is the whole point of
# replacing 'upside' with 'reachability'.
print("\n1. Strong uptrend, liquid, realistic 6% target")
h = series(drift=0.0012, volume=8e5)
r = run('UPTREND', h, float(h['Close'].iloc[-1]) * 1.06)
print(cl.format_report(r))
ok(r['score'] is not None and r['score'] > 65, 'scores high', r['score'])
ok(r['verdict'] == 'ACCEPT', 'accepted', r['verdict'])
ok(abs(r['evidence_pct'] - 100) < 0.1, 'full evidence', r['evidence_pct'])

print("\n2. Downtrend")
h = series(drift=-0.0012, volume=8e5, seed=2)
r = run('DOWNTREND', h, float(h['Close'].iloc[-1]) * 1.05)
ok(r['score'] is not None and r['score'] < 40, 'scores low', r['score'])
ok(r['verdict'] == 'RECOMMEND REJECT', 'rejected', r['verdict'])
print(f"   score={r['score']} verdict={r['verdict']}")

print("\n3. No advisory target -> reachability drops out, renormalises")
h = series(drift=0.0012, volume=8e5)
r = run('NOTARGET', h, None)
ok(abs(r['evidence_pct'] - 60) < 0.1, 'evidence = 60 (100 - reachability budget 40)', r['evidence_pct'])
ok(r['score'] is not None, 'still produces a score', r['score'])
ok(r['layers']['reachability']['checks'][0]['status'] == 'UNKNOWN', 'reachability marked UNKNOWN')
with_t = run('NOTARGET', h, float(h['Close'].iloc[-1]) * 1.06)
print(f"   no target: {r['score']}  |  with 6% target: {with_t['score']}")

print("\n4. Thin liquidity -> hard gate")
h = series(drift=0.0012, volume=200)
r = run('THIN', h, float(h['Close'].iloc[-1]) * 1.06)
ok(r['verdict'] == 'RECOMMEND REJECT', 'gated on liquidity', r['verdict'])
ok(any('Liquidity below' in g for g in r['gates']), 'gate reason present', r['gates'])
ok(any('Thin liquidity' in w for w in r['warnings']), 'also flagged')

print("\n5. Short history (<147 bars) -> momentum UNKNOWN")
h = series(n=120, drift=0.0012, volume=8e5)
r = run('SHORT', h, float(h['Close'].iloc[-1]) * 1.06)
ok(r['layers']['momentum']['checks'][0]['status'] == 'UNKNOWN', 'momentum UNKNOWN')
ok(r['evidence_pct'] < 100, 'evidence reduced', r['evidence_pct'])
print(f"   evidence={r['evidence_pct']} score={r['score']}")

print("\n6. Price already above target -> 0 reachability pts + flag, not negative")
h = series(drift=0.0005, volume=8e5, seed=5)
px = float(h['Close'].iloc[-1])
r = run('ABOVE', h, px * 0.80)
ok(r['layers']['reachability']['checks'][0]['awarded'] == 0, 'reachability awards 0', r['layers']['reachability']['checks'][0]['awarded'])
ok(r['score'] >= 0, 'score not negative', r['score'])
ok(any('already above' in w for w in r['warnings']), 'flagged', r['warnings'])

print("\n7. Extended RSI is a flag, never points")
rise = series(n=400, drift=0.0005, volume=8e5, seed=7)
rise.iloc[-25:, rise.columns.get_loc('Close')] = rise['Close'].iloc[-25] * np.linspace(1.0, 1.9, 25)
r = run('EXTENDED', rise, float(rise['Close'].iloc[-1]) * 1.06)
has_rsi_flag = any('RSI14' in w for w in r['warnings'])
ok(has_rsi_flag, 'RSI flagged as warning', r['warnings'])
names = [c['name'] for L in r['layers'].values() for c in L['checks']]
ok(not any('RSI' in n or 'Overbought' in n for n in names), 'no RSI scoring component', names)

print("\n8. Contract matches full engine (dashboard renders it unchanged)")
r = run('UPTREND', series(drift=0.001, volume=8e5), 200.0)
required = {'symbol','score','evidence_pct','tier','tier_label','verdict','reasons',
            'gates','warnings','is_financial','sector','layers','errors'}
ok(required <= set(r), 'all keys present', required - set(r))
for nm, L in r['layers'].items():
    ok({'budget','awarded','attempted','pct','coverage','checks'} <= set(L), f'{nm} layer shape')
    for c in L['checks']:
        ok({'name','awarded','attempted','status','detail','potential'} <= set(c), f'{nm} check shape')


# ── reachability: the component that replaced 'upside' ─────────────────────
print("\n9. Reachability: same gap, different volatility -> different score")
calm = series(n=400, drift=0.0004, vol=0.004, volume=8e5, seed=11)
wild = series(n=400, drift=0.0004, vol=0.030, volume=8e5, seed=11)
r_calm = run('CALM', calm, float(calm['Close'].iloc[-1]) * 1.06)
r_wild = run('WILD', wild, float(wild['Close'].iloc[-1]) * 1.06)
zc = r_calm['layers']['reachability']['checks'][0]['awarded']
zw = r_wild['layers']['reachability']['checks'][0]['awarded']
print(f"   calm vol -> reachability {zc:.1f}/40   wild vol -> {zw:.1f}/40")
ok(zw > zc, "a 6% target is scored more reachable when the stock moves more",
   f"calm={zc} wild={zw}")

print("\n10. reach_z is exposed for storage")
ok(r_calm.get('reach_z') is not None, "reach_z present on the result", r_calm.get('reach_z'))
ok(r_calm['reach_z'] > r_wild['reach_z'],
   "calm stock needs MORE sigmas to reach the same target",
   f"calm z={r_calm['reach_z']:.3f} wild z={r_wild['reach_z']:.3f}")
r_not = run('NOTGT', calm, None)
ok(r_not.get('reach_z') is None, "reach_z is None when there is no target", r_not.get('reach_z'))

print("\n11. gap is what varies least, so vol must do the work")
near = run('NEAR', wild, float(wild['Close'].iloc[-1]) * 1.02)
far = run('FAR', wild, float(wild['Close'].iloc[-1]) * 1.20)
ok(near['reach_z'] < far['reach_z'], "a nearer target needs fewer sigmas",
   f"near={near['reach_z']:.3f} far={far['reach_z']:.3f}")

print(f"\n{'='*46}\n  {P} passed, {F} failed  (with reachability)\n{'='*46}")
sys.exit(1 if F else 0)
