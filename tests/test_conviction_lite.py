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

print("\n1. Strong uptrend, liquid, 25% upside")
h = series(drift=0.0012, volume=8e5)
r = run('UPTREND', h, float(h['Close'].iloc[-1]) * 1.25)
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

print("\n3. No advisory target -> upside drops out, renormalises")
h = series(drift=0.0012, volume=8e5)
r = run('NOTARGET', h, None)
ok(abs(r['evidence_pct'] - 75) < 0.1, 'evidence = 75 (100 - upside budget 25)', r['evidence_pct'])
ok(r['score'] is not None, 'still produces a score', r['score'])
ok(r['layers']['upside']['checks'][0]['status'] == 'UNKNOWN', 'upside marked UNKNOWN')
with_t = run('NOTARGET', h, float(h['Close'].iloc[-1]) * 1.25)
print(f"   no target: {r['score']}  |  with 25% target: {with_t['score']}")

print("\n4. Thin liquidity -> hard gate")
h = series(drift=0.0012, volume=200)
r = run('THIN', h, float(h['Close'].iloc[-1]) * 1.25)
ok(r['verdict'] == 'RECOMMEND REJECT', 'gated on liquidity', r['verdict'])
ok(any('Liquidity below' in g for g in r['gates']), 'gate reason present', r['gates'])
ok(any('Thin liquidity' in w for w in r['warnings']), 'also flagged')

print("\n5. Short history (<147 bars) -> momentum UNKNOWN")
h = series(n=120, drift=0.0012, volume=8e5)
r = run('SHORT', h, float(h['Close'].iloc[-1]) * 1.25)
ok(r['layers']['momentum']['checks'][0]['status'] == 'UNKNOWN', 'momentum UNKNOWN')
ok(r['evidence_pct'] < 100, 'evidence reduced', r['evidence_pct'])
print(f"   evidence={r['evidence_pct']} score={r['score']}")

print("\n6. Price already above target -> 0 upside pts + flag, not negative")
h = series(drift=0.0005, volume=8e5, seed=5)
px = float(h['Close'].iloc[-1])
r = run('ABOVE', h, px * 0.80)
ok(r['layers']['upside']['checks'][0]['awarded'] == 0, 'upside awards 0', r['layers']['upside']['checks'][0]['awarded'])
ok(r['score'] >= 0, 'score not negative', r['score'])
ok(any('already above' in w for w in r['warnings']), 'flagged', r['warnings'])

print("\n7. Extended RSI is a flag, never points")
rise = series(n=400, drift=0.0005, volume=8e5, seed=7)
rise.iloc[-25:, rise.columns.get_loc('Close')] = rise['Close'].iloc[-25] * np.linspace(1.0, 1.9, 25)
r = run('EXTENDED', rise, float(rise['Close'].iloc[-1]) * 1.25)
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

print(f"\n{'='*46}\n  {P} passed, {F} failed\n{'='*46}")
sys.exit(1 if F else 0)
