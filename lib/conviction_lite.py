#!/usr/bin/env python3
"""
conviction_lite.py — a small, fast conviction score for new recommendations.

Why this exists
---------------
The full engine (lib/conviction.py) reads financial statements, analyst
consensus and surveillance lists: ~15-20s per symbol, many network calls, and
a lot of UNKNOWNs when a filing is missing. The first backtest found no
relationship between its composite and subsequent excess return, and the
per-check decomposition showed why the technical layer in particular was
working against itself: it rewarded trend strength (rho +0.23) while two
"guards" penalised exactly the same thing (rho -0.35 and -0.22).

This module keeps four things that are cheap, always available, and either
evidence-backed or structurally justified, and drops everything else:

    Momentum  35   12-1 month return. The most robustly documented equity
                   factor globally and in India, and the direction our own
                   per-check attribution pointed at.
    Trend     25   price vs 50DMA vs 200DMA. Positively correlated in the
                   attribution (+0.23) and the one technical check that was.
    Upside    25   distance to the SPTulsian target. Free — already in the
                   ledger — and it is the advisory's own stated edge.
    Liquidity 15   median traded value. Not alpha; it is the constraint that
                   decides whether a position can be exited at all.

Everything else is a FLAG, not points. That is the main lesson from the
attribution: "don't chase an extended move" is a risk control, and encoding a
risk control as negative score is what made high-RSI names score low in a
window where they outperformed. Flags surface on the dashboard; they do not
move the number.

One network call per symbol (2y of daily bars). Nothing else.

DISPLAY ONLY. Like the full engine, this does not size, gate or block an
order — see lib/config.CONVICTION_SIZING_ENABLED. It is unvalidated: it is
built on one weak signal from 37 symbols over two months in a single market
regime, which is a hypothesis, not a finding. Re-run backtest_conviction.py
as trades close.

Run directly:
    python3 -m lib.conviction_lite SOMANYCERA 250
"""
import time
import warnings

warnings.filterwarnings('ignore')

MODEL = 'lite'

# Component budgets — must total 100.
#
# 'upside' was replaced by 'reachability' on 2026-08-29. Upside scored the gap
# to the advisory target across a 0-30% range, but SPTulsian sets targets at a
# near-constant ~6% above their recommended price — across 16 closed trades the
# gap ran 5.82% to 7.69%, sd 0.58pp. Twenty-five points were therefore assigned
# almost the same value for every stock, which cannot rank anything. It was a
# quarter of a score that was sizing real positions.
BUDGETS = {'reachability': 40, 'momentum': 25, 'trend': 20, 'liquidity': 15}

TIERS = [
    (80, 'T1', 'Strong'),
    (65, 'T2', 'Good'),
    (50, 'T3', 'Mixed'),
    (0,  'T4', 'Weak'),
]
ACCEPT_FLOOR = 50            # below this, recommend-reject is surfaced
MIN_EVIDENCE_PCT = 40        # too little assessed to call it either way
LIQUIDITY_FLOOR_CR = 0.5     # median daily traded value, crore — hard gate

# Scaling endpoints: (0 points, full points)
MOMENTUM_RANGE = (-0.10, 0.40)   # 12-1 return
LIQUIDITY_RANGE = (0.5, 10.0)    # crore/day

# Reachability: how many 3-month sigmas of movement the target requires.
#     z = gap_to_target / (daily_vol * sqrt(HORIZON_DAYS))
# LOWER is better, so these endpoints are inverted relative to the others:
# full points at or below Z_FULL, zero at or above Z_ZERO. Both are the p10
# and p90 of z measured across the 33 distinct symbols recommended since the
# account cutover, so the middle 80% of names spread across the full range
# instead of bunching at one end.
HORIZON_DAYS = 63                # ~3 months of trading, SPTulsian's stated horizon
Z_FULL = 0.25
Z_ZERO = 1.20
VOL_LOOKBACK = 60                # sessions of realised daily volatility

MIN_BARS = 147               # ~7 months; below this momentum is not computable

OK, UNKNOWN = 'OK', 'UNKNOWN'


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _scaled(value, lo, hi, points):
    """Linear award between lo (0 pts) and hi (full points), clamped."""
    if value is None:
        return None
    frac = 1.0 if hi == lo else (value - lo) / (hi - lo)
    return round(max(0.0, min(1.0, frac)) * points, 2)


def _check(name, awarded, attempted, status, detail):
    return {'name': name, 'awarded': awarded, 'attempted': attempted,
            'status': status, 'detail': detail, 'potential': attempted}


def _unknown(name, why, potential):
    return {'name': name, 'awarded': 0, 'attempted': 0, 'status': UNKNOWN,
            'detail': why, 'potential': potential}


# ── Data ─────────────────────────────────────────────────────────────────

def fetch_history(symbol, log=_log):
    """2y of daily bars from NSE. The only network call this module makes."""
    import yfinance as yf
    for suffix in ('.NS', '.BO'):
        try:
            hist = yf.Ticker(symbol + suffix).history(period='2y', auto_adjust=True)
        except Exception as e:
            log(f"    {symbol}{suffix}: {type(e).__name__}: {e}")
            continue
        if hist is not None and not hist.empty and len(hist) >= 60:
            hist = hist.dropna(subset=['Close'])
            if len(hist) >= 60:
                return hist, suffix
    return None, None


# ── Components ───────────────────────────────────────────────────────────

def score_momentum(close):
    """12-1 momentum: trailing 12-month return, skipping the most recent
    month. The skip is not decoration — short-horizon reversal runs the
    opposite way to momentum, so including the last month dilutes the
    signal with its own inverse."""
    budget = BUDGETS['momentum']
    if len(close) < MIN_BARS:
        return [_unknown('Momentum 12-1', f'only {len(close)} bars (need >={MIN_BARS})', budget)]
    skip = 21
    look = min(252, len(close) - skip - 1)
    recent = float(close.iloc[-1 - skip])
    past = float(close.iloc[-1 - skip - look])
    if past <= 0:
        return [_unknown('Momentum 12-1', 'non-positive base price', budget)]
    ret = recent / past - 1
    months = look / 21
    return [_check('Momentum 12-1', _scaled(ret, *MOMENTUM_RANGE, budget), budget, OK,
                   f'{ret:+.1%} over {months:.0f}m ending 1m ago')]


def score_trend(close):
    """Trend alignment. Awards structure, not distance — a stock 200% above
    its 200DMA scores the same as one 5% above, because the extended case is
    a flag, not a bonus."""
    budget = BUDGETS['trend']
    price = float(close.iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    if ma200 is None:
        pts = budget * 0.6 if price > ma50 else 0.0
        return [_check('Trend alignment', round(pts, 2), budget, OK,
                       f'price {price:,.0f} vs 50DMA {ma50:,.0f} '
                       f'(too few bars for 200DMA — capped)')]

    if price > ma50 > ma200:
        pts, note = budget, ' — fully aligned'
    elif price > ma200:
        pts, note = budget * 0.6, ' — above 200DMA, below 50DMA'
    elif price > ma50:
        pts, note = budget * 0.4, ' — above 50DMA, below 200DMA'
    else:
        pts, note = 0.0, ' — below both'
    return [_check('Trend alignment', round(pts, 2), budget, OK,
                   f'price {price:,.0f} / 50DMA {ma50:,.0f} / 200DMA {ma200:,.0f}{note}')]


def reach_z(price, spt_target, daily_vol):
    """Sigmas of 3-month movement needed to touch the target. None if unknowable."""
    if not spt_target or spt_target <= 0 or price <= 0 or not daily_vol or daily_vol <= 0:
        return None
    gap = (spt_target - price) / price
    return gap / (daily_vol * (HORIZON_DAYS ** 0.5))


def score_reachability(close, price, spt_target):
    """How likely is this to TOUCH the target inside the horizon?

    This is the question the system actually asks, because the exit is a GTT
    that fires on touch — not a view held to a valuation. Distance alone does
    not answer it: SPTulsian's targets sit at a near-constant ~6%, so the gap
    barely varies, and what separates a name that hits in 3 days from one that
    takes 23 is how far it moves per day.

    Measured on 16 closed trades, the volatility-scaled distance ranked
    time-to-target at rho +0.57 (t=+2.6) against +0.36 for the raw gap.

    Two caveats that belong next to the number, not in a commit message.
    Those 16 trades are all winners — the ones that reached target — so the
    relationship is conditioned on success and cannot see what volatility
    costs on the trades that do not. And with no stop-loss in the system, a
    volatile name that goes the wrong way is not stopped out, it is simply
    held. Rewarding reachability therefore raises hit rate and thickens the
    tail at the same time. That trade-off was made deliberately.
    """
    budget = BUDGETS['reachability']
    if not spt_target or spt_target <= 0:
        return [_unknown('Reachability', 'no advisory target on file', budget)]

    rets = close.pct_change().dropna().tail(VOL_LOOKBACK)
    if len(rets) < 30:
        return [_unknown('Reachability', f'only {len(rets)} returns (need >=30)', budget)]
    vol = float(rets.std())
    if vol <= 0:
        return [_unknown('Reachability', 'zero realised volatility', budget)]

    gap = (spt_target - price) / price
    if gap <= 0:
        # Already at or through the target. Mechanically the GTT fires at once,
        # but that is not a trade worth taking — entering above the exit books
        # a loss after costs. Score it zero rather than "maximally reachable".
        return [_check('Reachability', 0.0, budget, OK,
                       f'price {price:,.0f} already at/above target {spt_target:,.0f} '
                       f'({gap:+.1%}) — no room left')]

    z = gap / (vol * (HORIZON_DAYS ** 0.5))
    return [_check('Reachability', _scaled(z, Z_ZERO, Z_FULL, budget), budget, OK,
                   f'{gap:+.1%} to target needs {z:.2f} sigma over {HORIZON_DAYS}d '
                   f'(daily vol {vol:.2%})')]


def score_liquidity(close, vol):
    """Median 20-day traded value. This is an exit constraint: a position we
    cannot sell without moving the price is a different instrument to the one
    we thought we bought."""
    budget = BUDGETS['liquidity']
    tv_cr = float((close * vol).tail(20).median()) / 1e7
    if tv_cr != tv_cr:  # NaN
        return [_unknown('Liquidity', 'no volume data', budget)]
    return [_check('Liquidity', _scaled(tv_cr, *LIQUIDITY_RANGE, budget), budget, OK,
                   f'median 20d traded value Rs {tv_cr:.2f} cr/day')]


# ── Flags ────────────────────────────────────────────────────────────────

def flags(close, vol, spt_target):
    """Risk observations that deliberately carry no points.

    Each of these was, or would have been, a scoring component in the full
    engine. They are surfaced instead of scored because the attribution
    showed the two guards among them correlated *negatively* with excess
    return — penalising strength in a window where strength paid. Whether
    that reverses in another regime is unknown, which is exactly why they
    inform rather than decide."""
    out = []
    price = float(close.iloc[-1])

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    # loss == 0 means fourteen straight sessions without a down day, i.e. RSI
    # is 100 — the most extended a stock can be. Guarding on `loss > 0` alone
    # divides by zero, so the naive form skips the flag in exactly the case
    # it exists for.
    rsi = None
    if loss == loss and gain == gain:          # neither is NaN
        if loss > 0:
            rsi = 100 - 100 / (1 + gain / loss)
        elif gain > 0:
            rsi = 100.0
    if rsi is not None and rsi >= 80:
        out.append(f'Extended — RSI14 {rsi:.0f}. Entry timing risk, not a quality signal.')

    hi52 = float(close.tail(252).max())
    if hi52 > 0 and (price - hi52) / hi52 > -0.02:
        out.append(f'Within 2% of its 52-week high ({hi52:,.0f}).')

    rets = close.pct_change().dropna().tail(60)
    if len(rets) >= 30:
        ann_vol = float(rets.std()) * (252 ** 0.5)
        if ann_vol > 0.60:
            # Stated, not judged. Volatility is what carries price to the
            # target, so the Reachability component rewards it; flagging it
            # here as a negative would contradict the score on the same page.
            # It is still worth seeing, because the same property makes a
            # position that goes wrong go wrong faster — and with no
            # stop-loss, nothing intervenes.
            out.append(f'Volatile — {ann_vol:.0%} annualised. Reaches target sooner; '
                       f'also moves against you faster, and there is no stop-loss.')

    tv_cr = float((close * vol).tail(20).median()) / 1e7
    if tv_cr == tv_cr and tv_cr < LIQUIDITY_FLOOR_CR:
        out.append(f'Thin liquidity — Rs {tv_cr:.2f} cr/day median traded value.')

    if spt_target and spt_target > 0 and price > spt_target:
        out.append(f'Price {price:,.0f} is already above the advisory target '
                   f'{spt_target:,.0f}.')
    return out


def gates(close, vol):
    """Hard stops. Only one: we must be able to get out."""
    tv_cr = float((close * vol).tail(20).median()) / 1e7
    if tv_cr == tv_cr and tv_cr < LIQUIDITY_FLOOR_CR:
        return [f'Liquidity below the Rs {LIQUIDITY_FLOOR_CR} cr/day floor '
                f'(Rs {tv_cr:.2f} cr) — exit risk.']
    return []


# ── Composite ────────────────────────────────────────────────────────────

def score_symbol(symbol, spt_target=None, log=_log):
    """Lightweight conviction for one symbol. Never raises.

    Returns the same shape as lib.conviction.score_symbol, so the dashboard
    renders it and main_conviction.py stores it with no special-casing."""
    errors = []
    hist, suffix = fetch_history(symbol, log=log)

    if hist is None:
        return {
            'symbol': symbol, 'model': MODEL, 'score': None, 'evidence_pct': 0.0,
            'tier': 'NA', 'tier_label': 'No price data', 'verdict': 'INSUFFICIENT EVIDENCE',
            'reasons': ['No usable price history could be fetched.'],
            'gates': [], 'warnings': [], 'is_financial': False, 'sector': None,
            'layers': {}, 'errors': [f'no history for {symbol}'],
        }

    close, vol = hist['Close'], hist['Volume']
    price = float(close.iloc[-1])

    components = {
        'momentum': score_momentum(close),
        'trend': score_trend(close),
        'reachability': score_reachability(close, price, spt_target),
        'liquidity': score_liquidity(close, vol),
    }

    # Recorded alongside the score, not just folded into it. z is the quantity
    # we want to validate against realised time-to-target once enough of the
    # book resolves, and reconstructing it later from a stored composite is
    # impossible. Storing it now is the difference between measuring this
    # model in six weeks and re-deriving it from scratch.
    _rets = close.pct_change().dropna().tail(VOL_LOOKBACK)
    _vol = float(_rets.std()) if len(_rets) >= 30 else None
    z_at_score = reach_z(price, spt_target, _vol)

    # A component that could not be computed leaves the denominator rather
    # than scoring zero, so missing data reduces confidence instead of
    # masquerading as a bad result. evidence_pct reports how much of the
    # 100 points was actually assessable.
    summary, awarded_total, weight_total = {}, 0.0, 0.0
    for name, checks in components.items():
        budget = BUDGETS[name]
        got = sum(c['awarded'] for c in checks)
        att = sum(c['attempted'] for c in checks)
        pot = sum(c['potential'] for c in checks)
        quality = (got / att) if att else None
        coverage = (att / pot) if pot else 0.0
        weight = budget * coverage
        scaled_got = (quality * weight) if quality is not None else 0.0
        summary[name] = {
            'budget': budget,
            'awarded': round(scaled_got, 1),
            'attempted': round(weight, 1),
            'pct': None if quality is None else round(quality * 100, 1),
            'coverage': round(coverage * 100, 1),
            'checks': checks,
        }
        awarded_total += scaled_got
        weight_total += weight

    score = round(awarded_total / weight_total * 100, 1) if weight_total else None
    evidence_pct = round(weight_total, 1)

    hard = gates(close, vol)
    warns = flags(close, vol, spt_target)

    if score is None or evidence_pct < MIN_EVIDENCE_PCT:
        tier, tier_label = 'NA', 'Insufficient evidence'
        verdict = 'INSUFFICIENT EVIDENCE'
        reasons = [f'Only {evidence_pct:.0f} of 100 points assessable '
                   f'(floor {MIN_EVIDENCE_PCT}).'] if score is not None else \
                  ['Nothing could be assessed.']
        score = None
    else:
        tier, tier_label = next((t, lbl) for floor, t, lbl in TIERS if score >= floor)
        if hard:
            verdict, reasons = 'RECOMMEND REJECT', list(hard)
        elif score < ACCEPT_FLOOR:
            verdict = 'RECOMMEND REJECT'
            reasons = [f'Score {score:.0f} is below the {ACCEPT_FLOOR}-point floor.']
        else:
            verdict, reasons = 'ACCEPT', []

    return {
        'symbol': symbol, 'model': MODEL, 'score': score, 'evidence_pct': evidence_pct,
        'tier': tier, 'tier_label': tier_label, 'verdict': verdict,
        'reasons': reasons, 'gates': hard, 'warnings': warns,
        'is_financial': False, 'sector': None,
        'reach_z': z_at_score,
        'layers': summary, 'errors': errors,
    }


def format_report(s):
    out = []
    score = 'n/a' if s['score'] is None else f"{s['score']:.0f}/100"
    out.append(f"{s['symbol']}  [{s['model']}]  score={score}  tier={s['tier']} "
               f"({s['tier_label']})  evidence={s['evidence_pct']:.0f}/100  -> {s['verdict']}")
    for r in s['reasons']:
        out.append(f"  ! {r}")
    for w in s['warnings']:
        out.append(f"  ~ {w}")
    for name, layer in s['layers'].items():
        pct = 'not assessed' if layer['pct'] is None else f"quality {layer['pct']:.0f}%"
        out.append(f"  {name:10s} {layer['awarded']:5.1f}/{layer['attempted']:<4.1f} "
                   f"of {layer['budget']:<3d} ({pct})")
        for c in layer['checks']:
            mark = ' ' if c['status'] == OK else '?'
            pts = f"{c['awarded']:5.1f}/{c['attempted']:<3.0f}" if c['attempted'] else "    -    "
            out.append(f"   {mark} {c['name']:20s} {pts}  {c['detail']}")
    return '\n'.join(out)


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'SOMANYCERA'
    tgt = float(sys.argv[2]) if len(sys.argv) > 2 else None
    print(format_report(score_symbol(sym, spt_target=tgt)))
