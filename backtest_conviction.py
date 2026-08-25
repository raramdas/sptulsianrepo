#!/usr/bin/env python3
"""
backtest_conviction.py — does the conviction score actually relate to what
the trades went on to do?

READ-ONLY. Touches no table, places no order.

Why this exists: the score now decides whether a stock is bought and for how
much, and it had never been checked against a single outcome. Adding more
factors to an unvalidated model only makes it more elaborately unvalidated.

METHOD, and its limits — read these before trusting any number below.

  Point-in-time.  The live engine reads TODAY's fundamentals, analyst
  consensus and surveillance status. Scoring a July trade with August data
  measures hindsight, not skill. So the score is rebuilt as of each trade's
  buy date:
      price history   sliced to the buy date          (fully reconstructable)
      financials      only statements published before it (ditto)
      consensus       NOT reconstructable -> UNKNOWN
      governance      holdings are current-only       -> UNKNOWN
  The fail-soft design does the rest: unavailable layers leave the
  denominator instead of scoring zero. The result is a REDUCED score — the
  fundamentals and technical layers only, roughly 60 of the 100 points — so
  this validates the engine's core, not the exact composite used for sizing.

  Benchmark.  Raw return over a rising market says little, so every return is
  reported net of NIFTY 50 over the identical holding window.

  Sample, and why the trade count flatters it.  ~113 trades but only ~37
  symbols: 26 of them are held in several lots bought days apart, whose
  returns are near-identical. Counting those as independent observations
  roughly triples the apparent sample and makes a weak correlation look
  significant. The symbol-level figure is the honest one, and it is what
  should be quoted.

  FIRST RESULT (2026-08-26). No detectable relationship between score and
  subsequent excess return:
      trade level    n=113  rho=-0.235  t=-2.55   <- overstated by clustering
      symbol level   n= 37  rho=-0.127  t=-0.76   <- not distinguishable from 0
  Dropping the two worst symbols moves it to -0.081, so the weak negative is
  not robust either. By sizing band, symbol level: >85 mean -0.37%, 75-85
  mean -3.91%, <75 mean +1.01% — the band that would receive most of the
  capital did worst, and the band we refuse to buy did best. With 7-8
  symbols per band none of that is conclusive; the conclusion is the absence
  of evidence, not evidence of inversion.

  So: this does NOT support sizing on the score. It is also under two months
  of one regime, with 12 realised closes and the rest marked to market.

Run:
    python3 backtest_conviction.py
    python3 backtest_conviction.py --csv /tmp/backtest.csv
"""
import os
import sys
import argparse
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

import pandas as pd

from lib import conviction
from lib.config import CONVICTION_SIZING, CONVICTION_MIN_SCORE

_HERE = os.path.dirname(os.path.abspath(__file__))
for _c in (os.path.join(_HERE, 'dashboard'), '/home/ubuntu/stockbot/dashboard'):
    if os.path.isdir(_c):
        sys.path.insert(0, _c)
        break
import db  # noqa: E402

BENCHMARK = '^NSEI'          # NIFTY 50
_hist_cache = {}


def history(symbol, log=print):
    """Daily bars for one symbol, cached. Returns None if unavailable."""
    if symbol in _hist_cache:
        return _hist_cache[symbol]
    import yfinance as yf
    tick = symbol if symbol.startswith('^') else f'{symbol}.NS'
    try:
        h = yf.Ticker(tick).history(period='3y')
        h = None if h is None or h.empty else h
    except Exception as e:
        log(f"    history failed for {symbol}: {type(e).__name__}")
        h = None
    _hist_cache[symbol] = h
    return h


def _statements(symbol):
    """Annual statements for one symbol, cached alongside history."""
    key = f'__stmt__{symbol}'
    if key in _hist_cache:
        return _hist_cache[key]
    import yfinance as yf
    t = yf.Ticker(f'{symbol}.NS')
    out = {}
    for attr, name in [('financials', 'financials'), ('balance_sheet', 'balance'),
                       ('cashflow', 'cashflow')]:
        try:
            df = getattr(t, attr)
            out[name] = df if df is not None and not df.empty else None
        except Exception:
            out[name] = None
    _hist_cache[key] = out
    return out


def _as_of(df, when):
    """Keep only statement columns published on or before `when`.

    Without this the score would read results the market had not seen yet on
    the buy date, which is the classic way a backtest flatters itself.
    """
    if df is None or df.empty:
        return None
    try:
        cols = [c for c in df.columns if pd.Timestamp(c).tz_localize(None) <= when]
    except (TypeError, ValueError):
        return df
    if not cols:
        return None
    cols = sorted(cols, key=lambda c: pd.Timestamp(c), reverse=True)
    return df[cols]


def score_as_of(symbol, when, spt_target=None):
    """Rebuild the reduced score for `symbol` as it would have read on `when`."""
    hist = history(symbol)
    if hist is None:
        return None
    idx = hist.index
    cutoff = pd.Timestamp(when)
    if idx.tz is not None:
        cutoff = cutoff.tz_localize(idx.tz) if cutoff.tz is None else cutoff.tz_convert(idx.tz)
    past = hist[idx <= cutoff]
    if len(past) < 60:
        return None

    stmts = _statements(symbol)
    naive = pd.Timestamp(when).tz_localize(None)
    ev = {
        'symbol': symbol, 'spt_target': spt_target,
        'info': {},                      # not reconstructable -> those checks UNKNOWN
        'financials': _as_of(stmts.get('financials'), naive),
        'balance': _as_of(stmts.get('balance'), naive),
        'cashflow': _as_of(stmts.get('cashflow'), naive),
        'history': past,
        'asm_stage': None, 'asm_checked': False,   # current-only -> UNKNOWN
        'errors': [],
    }
    layers = {
        'fundamentals': conviction.score_fundamentals(ev),
        'consensus': conviction.score_consensus(ev),
        'technical': conviction.score_technical(ev),
        'governance': conviction.score_governance(ev),
    }
    awarded = attempted = 0.0
    for name, checks in layers.items():
        budget = conviction.LAYER_POINTS[name]
        got = sum(c['awarded'] for c in checks)
        att = sum(c['attempted'] for c in checks)
        pot = sum(c['potential'] for c in checks)
        if not att or not pot:
            continue
        weight = budget * (att / pot)
        awarded += (got / att) * weight
        attempted += weight
    if not attempted:
        return None
    return {'score': round(awarded / attempted * 100, 1),
            'evidence': round(attempted, 1)}


def price_on(symbol, when):
    """Last real close on or before `when`, or None.

    yfinance emits a trailing bar for the current session with a NaN close on
    individual stocks (though not on the index). Returning that NaN silently
    poisoned every open trade's return: `not float("nan")` is False, so the
    caller's guard let it through and the row landed in the frame with a NaN
    return. Drop NaNs before taking the last bar, and never return one.
    """
    h = history(symbol)
    if h is None:
        return None
    cutoff = pd.Timestamp(when)
    if h.index.tz is not None:
        cutoff = cutoff.tz_localize(h.index.tz) if cutoff.tz is None else cutoff.tz_convert(h.index.tz)
    past = h[h.index <= cutoff]['Close'].dropna()
    if past.empty:
        return None
    val = float(past.iloc[-1])
    return None if val != val or val <= 0 else val


def build(log=print):
    trades = db._df("""
        SELECT t.trade_id, t.symbol, t.stock_name, t.category_name, t.status,
               t.buy_date, t.my_buy_price, t.my_sell_price, t.my_sell_date,
               t.target_price, c.score AS stored_score
        FROM trades t
        LEFT JOIN (SELECT trade_id, MAX(score_id) AS score_id
                     FROM conviction_scores GROUP BY trade_id) l ON l.trade_id = t.trade_id
        LEFT JOIN conviction_scores c ON c.score_id = l.score_id
        WHERE t.symbol IS NOT NULL AND t.my_buy_price > 0
          AND t.status IN ('Open', 'Closed')
        ORDER BY t.buy_date
    """)
    log(f"Scoring {len(trades)} trade(s) across {trades['symbol'].nunique()} symbol(s)...")

    rows = []
    for i, t in trades.iterrows():
        sym = str(t['symbol']).strip()
        buy_dt = pd.Timestamp(t['buy_date']).tz_localize(None)
        buy_px = float(t['my_buy_price'])

        closed = t['status'] == 'Closed' and t['my_sell_price']
        exit_dt = (pd.Timestamp(t['my_sell_date']).tz_localize(None)
                   if closed and pd.notna(t['my_sell_date']) else pd.Timestamp.now().normalize())
        exit_px = float(t['my_sell_price']) if closed else price_on(sym, exit_dt)
        if exit_px is None or exit_px != exit_px or exit_px <= 0:
            continue

        bench_in, bench_out = price_on(BENCHMARK, buy_dt), price_on(BENCHMARK, exit_dt)
        if not bench_in or not bench_out:
            continue

        ret = (exit_px - buy_px) / buy_px * 100
        bench = (bench_out - bench_in) / bench_in * 100
        sc = score_as_of(sym, buy_dt, spt_target=t['target_price'])

        rows.append({
            'trade_id': int(t['trade_id']), 'symbol': sym, 'stock': t['stock_name'],
            'buy_date': buy_dt.date(), 'days': (exit_dt - buy_dt).days,
            'realised': bool(closed),
            'score_asof': None if not sc else sc['score'],
            'evidence_asof': None if not sc else sc['evidence'],
            'stored_score': None if pd.isna(t['stored_score']) else float(t['stored_score']),
            'return_pct': round(ret, 2),
            'bench_pct': round(bench, 2),
            'excess_pct': round(ret - bench, 2),
        })
        if (i + 1) % 25 == 0:
            log(f"  ...{i + 1}/{len(trades)}")
    return pd.DataFrame(rows)


def band_of(score):
    if score is None or pd.isna(score):
        return 'unscored'
    if score > CONVICTION_SIZING[0][0]:
        return f'>{CONVICTION_SIZING[0][0]:.0f}  (Rs {CONVICTION_SIZING[0][1]:,})'
    if score >= CONVICTION_MIN_SCORE:
        return f'{CONVICTION_MIN_SCORE:.0f}-{CONVICTION_SIZING[0][0]:.0f} (Rs {CONVICTION_SIZING[1][1]:,})'
    return f'<{CONVICTION_MIN_SCORE:.0f} (not bought)'


def report(df, log=print):
    if df.empty:
        log("No comparable trades."); return
    df = df.dropna(subset=['excess_pct'])
    scored = df[df['score_asof'].notna()]
    log("")
    log("=" * 78)
    log(f"  {len(df)} trade(s) compared; {len(scored)} rebuilt point-in-time; "
        f"{int(df['realised'].sum())} realised")
    log(f"  median holding {int(df['days'].median())} days · "
        f"mean stock {df['return_pct'].mean():+.2f}% vs NIFTY {df['bench_pct'].mean():+.2f}%")
    log("=" * 78)

    if len(scored) >= 5:
        for label, col in [("point-in-time score", 'score_asof'),
                           ("stored score (look-ahead)", 'stored_score')]:
            sub = df[df[col].notna()]
            if len(sub) < 5:
                continue
            # Spearman computed as Pearson on ranks — pandas' 'spearman'
            # pulls in scipy, which is heavy for a 956MB box.
            sp = sub[col].rank().corr(sub['excess_pct'].rank(), method='pearson')
            pe = sub[col].corr(sub['excess_pct'], method='pearson')
            log(f"\n  {label}: n={len(sub)}  Spearman={sp:+.3f}  Pearson={pe:+.3f}")
            log(f"    {'positive = higher score went with better excess return' if sp > 0 else 'NEGATIVE = the score ran against outcomes'}")

    log("\n  By the bands that decide position size (point-in-time score):")
    log(f"    {'band':24s} {'n':>4s} {'mean exc':>9s} {'median':>8s} {'win%':>6s}")
    scored = scored.copy()
    scored['band'] = scored['score_asof'].apply(band_of)
    order = [f'>{CONVICTION_SIZING[0][0]:.0f}  (Rs {CONVICTION_SIZING[0][1]:,})',
             f'{CONVICTION_MIN_SCORE:.0f}-{CONVICTION_SIZING[0][0]:.0f} (Rs {CONVICTION_SIZING[1][1]:,})',
             f'<{CONVICTION_MIN_SCORE:.0f} (not bought)']
    for b in order:
        g = scored[scored['band'] == b].dropna(subset=['excess_pct'])
        if g.empty:
            log(f"    {b:24s} {0:>4d} {'—':>9s} {'—':>8s} {'—':>6s}")
            continue
        log(f"    {b:24s} {len(g):>4d} {g['excess_pct'].mean():>+8.2f}% "
            f"{g['excess_pct'].median():>+7.2f}% {(g['excess_pct'] > 0).mean()*100:>5.0f}%")

    buy = scored[scored['score_asof'] >= CONVICTION_MIN_SCORE]
    skip = scored[scored['score_asof'] < CONVICTION_MIN_SCORE]
    if len(buy) and len(skip):
        log(f"\n  Would-buy vs would-skip: {buy['excess_pct'].mean():+.2f}% "
            f"(n={len(buy)}) vs {skip['excess_pct'].mean():+.2f}% (n={len(skip)})")
        gap = buy['excess_pct'].mean() - skip['excess_pct'].mean()
        log(f"    separation: {gap:+.2f} percentage points "
            f"{'in the intended direction' if gap > 0 else '— INVERTED, the gate prefers the worse half'}")

    log("\n  Best / worst by realised excess return:")
    for lbl, sub in [("best", df.nlargest(5, 'excess_pct')), ("worst", df.nsmallest(5, 'excess_pct'))]:
        log(f"    {lbl}:")
        for _, r in sub.iterrows():
            s = '—' if pd.isna(r['score_asof']) else f"{r['score_asof']:.0f}"
            log(f"      {r['stock'][:24]:24s} score={s:>4s} excess={r['excess_pct']:+7.2f}% "
                f"({r['days']}d)")

    log("\n  CAVEAT: under two months, few realised closes, one market regime.")
    log("  Enough to catch gross mis-calibration; not enough to validate the score.")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', help="Also write the per-trade detail to this path")
    args = ap.parse_args()
    frame = build()
    report(frame)
    if args.csv and not frame.empty:
        frame.to_csv(args.csv, index=False)
        print(f"\nwrote {args.csv}")
