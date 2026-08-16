#!/usr/bin/env python3
"""
conviction.py — scores how well a SPTulsian recommendation is supported by
public evidence.

This is DECISION SUPPORT, not advice and not a prediction. It computes
published metrics (Piotroski F-Score, Altman Z''-EM, Beneish M-Score, moving
averages, analyst consensus, NSE surveillance status) from public data and
shows its working. Every threshold here is a convention, not a fact; the
judgement stays with the human reading the output.

Design rules that matter:

1. FAIL-SOFT. An unreachable source or a missing line item marks that check
   UNKNOWN and drops it from the denominator. A data outage must never block
   a human from being offered the decision.

2. RENORMALISE, DON'T DERATE. The score is a percentage of the points
   actually attempted, and `evidence_pct` reports how much of the 100-point
   frame could be assessed at all. Derating for missing data would punish
   exactly the small and micro caps that have no analyst coverage — i.e.
   most of Little Gems — turning "we know less" into "this is worse". Those
   are different statements and are reported separately.

3. FINANCIALS ARE DIFFERENT. Piotroski, Altman and Beneish assume an
   operating balance sheet. On a bank or NBFC, current ratio, gross margin
   and asset turnover do not mean what they mean elsewhere. Those checks are
   marked N/A for the Financial Services sector rather than silently
   producing a confident wrong number.

4. HARD GATES ARE SEPARATE FROM THE SCORE. Distress, GSM surveillance and
   an illiquidity floor flag a recommendation regardless of how well it
   scores. A flagged call is still surfaced with its reason, never silently
   dropped.

Test independently:
    python3 conviction.py RELIANCE
"""
import time
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings('ignore')

# Layer budgets — must total 100.
LAYER_POINTS = {'fundamentals': 40, 'consensus': 25, 'technical': 20, 'governance': 15}

# Composite -> tier. Risk budgets are advisory only; nothing in this module
# sizes or places an order.
TIERS = [
    (80, 'T1', 'Strong evidence'),
    (65, 'T2', 'Good evidence'),
    (50, 'T3', 'Mixed evidence'),
    (0,  'T4', 'Weak evidence'),
]
ACCEPT_FLOOR = 50           # below this, recommend-reject is surfaced
MIN_EVIDENCE_PCT = 40       # too little assessed to call it either way
LIQUIDITY_FLOOR_CR = 1.0    # median daily traded value, in crore

OK, UNKNOWN, NA = 'OK', 'UNKNOWN', 'NA'


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def check(name, awarded, attempted, status, detail, potential=None):
    """One evaluated check.

    `attempted` is 0 for UNKNOWN/NA so the check leaves the score's
    denominator — that is what stops missing data acting as a penalty.

    `potential` is what the check would have been worth had it worked, and
    drives the separate evidence figure. The UNKNOWN/NA distinction matters:
      UNKNOWN — we tried and failed. Counts toward potential, so evidence
                falls. We know less about this stock.
      NA      — the metric does not apply (Piotroski on a bank). Excluded
                from potential entirely, because there is nothing here we
                failed to learn.
    """
    return {'name': name, 'awarded': awarded, 'attempted': attempted,
            'status': status, 'detail': detail,
            'potential': attempted if potential is None else potential}


def unknown(name, why, potential):
    return check(name, 0, 0, UNKNOWN, why, potential=potential)


def not_applicable(name, why):
    return check(name, 0, 0, NA, why, potential=0)


def _num(x):
    """Coerce to float, treating NaN/None/blank as missing."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else v  # NaN check


def _row(df, key, col=0):
    """One line item from a yfinance statement frame, or None."""
    if df is None or getattr(df, 'empty', True) or key not in df.index:
        return None
    try:
        return _num(df.loc[key].iloc[col])
    except (IndexError, KeyError):
        return None


def _scaled(value, lo, hi, points):
    """Linear award between lo (0 pts) and hi (full points), clamped."""
    if value is None:
        return None
    if hi == lo:
        return points if value >= hi else 0.0
    frac = (value - lo) / (hi - lo)
    return round(max(0.0, min(1.0, frac)) * points, 2)


# ── Evidence gathering ───────────────────────────────────────────────────

_NSE_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
           '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_asm_cache = None


def fetch_nse_surveillance(log=_log):
    """NSE's Additional Surveillance Measure list -> {symbol: stage}.

    Cached per process. Returns None (not {}) if the fetch failed, so callers
    can tell "no surveillance on this stock" apart from "we could not check",
    and mark the governance check UNKNOWN rather than awarding it a clean
    bill of health it has not earned."""
    global _asm_cache
    if _asm_cache is not None:
        return _asm_cache.get('data')

    import os
    import requests
    session = requests.Session()
    session.headers.update({'User-Agent': _NSE_UA, 'Accept': 'application/json'})
    # NSE may reject datacenter IPs the same way CloudFront does; reuse the
    # scraper's WARP egress if it is configured.
    proxy = os.environ.get('SPTULSIAN_PROXY', '')
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    try:
        session.get('https://www.nseindia.com', timeout=20)
        r = session.get('https://www.nseindia.com/api/reportASM?json=true', timeout=20)
        if r.status_code != 200:
            raise ValueError(f'HTTP {r.status_code}')
        payload = r.json()
        stages = {}
        for bucket in ('longterm', 'shortterm'):
            for row in ((payload.get(bucket) or {}).get('data') or []):
                sym = (row.get('symbol') or '').strip().upper()
                if sym:
                    stages[sym] = (row.get('asmSurvIndicator')
                                   or row.get('survIndicator') or 'ASM').strip()
        _asm_cache = {'data': stages}
        log(f"  NSE surveillance list: {len(stages)} symbol(s) under ASM")
        return stages
    except Exception as e:
        log(f"  NSE surveillance unavailable ({type(e).__name__}: {str(e)[:70]}) "
            f"— governance surveillance check will be UNKNOWN")
        _asm_cache = {'data': None}
        return None


def gather_evidence(symbol, spt_target=None, log=_log):
    """Pull everything the layers need for one symbol. Never raises: each
    piece independently degrades to None."""
    import yfinance as yf

    ev = {'symbol': symbol, 'spt_target': spt_target, 'info': {},
          'financials': None, 'balance': None, 'cashflow': None,
          'history': None, 'asm_stage': None, 'asm_checked': False,
          'errors': []}

    ticker = yf.Ticker(f'{symbol}.NS')
    try:
        ev['info'] = ticker.info or {}
    except Exception as e:
        ev['errors'].append(f'info: {type(e).__name__}')
    # Assign statement frames WITHOUT `or` — truth-testing a DataFrame raises
    # "truth value is ambiguous", which would be caught below and silently
    # null out every statement, making every accrual check look uncomputable.
    for attr, key in [('financials', 'financials'),
                      ('balance_sheet', 'balance'), ('cashflow', 'cashflow')]:
        try:
            df = getattr(ticker, attr)
            ev[key] = df if df is not None and not df.empty else None
        except Exception as e:
            ev['errors'].append(f'{key}: {type(e).__name__}')
    try:
        ev['history'] = ticker.history(period='1y')
    except Exception as e:
        ev['errors'].append(f'history: {type(e).__name__}')

    stages = fetch_nse_surveillance(log=log)
    if stages is not None:
        ev['asm_checked'] = True
        ev['asm_stage'] = stages.get(symbol.upper())

    return ev


def is_financial(ev):
    """Banks/NBFCs/exchanges — accrual and leverage ratios do not transfer."""
    return (ev.get('info') or {}).get('sector') == 'Financial Services'


# ── Layer 1: Fundamentals (40) ───────────────────────────────────────────

def piotroski(ev):
    """F-Score: 9 binary signals over profitability, leverage and efficiency,
    each needing this year vs last. Returns (score, max_possible, notes) with
    signals that lack data simply not counted, so a partial statement yields
    a partial denominator rather than a false zero."""
    fin, bs, cf = ev['financials'], ev['balance'], ev['cashflow']
    got, total, notes = 0, 0, []

    def signal(name, condition):
        nonlocal got, total
        if condition is None:
            return
        total += 1
        if condition:
            got += 1
            notes.append(f'+{name}')

    ni0, ni1 = _row(fin, 'Net Income', 0), _row(fin, 'Net Income', 1)
    ta0, ta1 = _row(bs, 'Total Assets', 0), _row(bs, 'Total Assets', 1)
    cfo0 = _row(cf, 'Operating Cash Flow', 0)
    roa0 = (ni0 / ta0) if (ni0 is not None and ta0) else None
    roa1 = (ni1 / ta1) if (ni1 is not None and ta1) else None

    signal('ROA>0', None if roa0 is None else roa0 > 0)
    signal('CFO>0', None if cfo0 is None else cfo0 > 0)
    signal('dROA>0', None if (roa0 is None or roa1 is None) else roa0 > roa1)
    # Accruals: cash earnings should exceed accounting earnings.
    signal('CFO>NI', None if (cfo0 is None or ni0 is None) else cfo0 > ni0)

    ltd0, ltd1 = _row(bs, 'Long Term Debt', 0), _row(bs, 'Long Term Debt', 1)
    lev0 = (ltd0 / ta0) if (ltd0 is not None and ta0) else None
    lev1 = (ltd1 / ta1) if (ltd1 is not None and ta1) else None
    signal('leverage down', None if (lev0 is None or lev1 is None) else lev0 < lev1)

    ca0, cl0 = _row(bs, 'Current Assets', 0), _row(bs, 'Current Liabilities', 0)
    ca1, cl1 = _row(bs, 'Current Assets', 1), _row(bs, 'Current Liabilities', 1)
    cr0 = (ca0 / cl0) if (ca0 is not None and cl0) else None
    cr1 = (ca1 / cl1) if (ca1 is not None and cl1) else None
    signal('current ratio up', None if (cr0 is None or cr1 is None) else cr0 > cr1)

    sh0, sh1 = _row(bs, 'Ordinary Shares Number', 0), _row(bs, 'Ordinary Shares Number', 1)
    signal('no dilution', None if (sh0 is None or sh1 is None) else sh0 <= sh1 * 1.001)

    gp0, gp1 = _row(fin, 'Gross Profit', 0), _row(fin, 'Gross Profit', 1)
    rv0, rv1 = _row(fin, 'Total Revenue', 0), _row(fin, 'Total Revenue', 1)
    gm0 = (gp0 / rv0) if (gp0 is not None and rv0) else None
    gm1 = (gp1 / rv1) if (gp1 is not None and rv1) else None
    signal('gross margin up', None if (gm0 is None or gm1 is None) else gm0 > gm1)

    at0 = (rv0 / ta0) if (rv0 is not None and ta0) else None
    at1 = (rv1 / ta1) if (rv1 is not None and ta1) else None
    signal('asset turnover up', None if (at0 is None or at1 is None) else at0 > at1)

    return got, total, notes


def altman_z_em(ev):
    """Altman Z''-EM, the emerging-market variant for non-manufacturers:
        Z'' = 3.25 + 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
    Zones: >5.85 safe, 4.15-5.85 grey, <4.15 distress."""
    bs, fin = ev['balance'], ev['financials']
    ta = _row(bs, 'Total Assets')
    if not ta:
        return None
    ca, cl = _row(bs, 'Current Assets'), _row(bs, 'Current Liabilities')
    re = _row(bs, 'Retained Earnings')
    ebit = _row(fin, 'EBIT')
    eq = _row(bs, 'Stockholders Equity')
    tl = _row(bs, 'Total Liabilities Net Minority Interest')
    if None in (ca, cl, re, ebit, eq, tl) or not tl:
        return None
    x1, x2, x3, x4 = (ca - cl) / ta, re / ta, ebit / ta, eq / tl
    return 3.25 + 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4


def beneish_m(ev):
    """Beneish M-Score. Above -1.78 is the conventional flag for possible
    earnings manipulation. Needs two consecutive years of eight ratios, so it
    is frequently unavailable — which is exactly why it fails soft."""
    fin, bs, cf = ev['financials'], ev['balance'], ev['cashflow']

    def pair(df, key):
        return _row(df, key, 0), _row(df, key, 1)

    (ar0, ar1) = pair(bs, 'Accounts Receivable')
    (rv0, rv1) = pair(fin, 'Total Revenue')
    (gp0, gp1) = pair(fin, 'Gross Profit')
    (ta0, ta1) = pair(bs, 'Total Assets')
    (ca0, ca1) = pair(bs, 'Current Assets')
    (pp0, pp1) = pair(bs, 'Net PPE')
    (dp0, dp1) = pair(cf, 'Depreciation And Amortization')
    (sg0, sg1) = pair(fin, 'Selling General And Administration')
    (ltd0, ltd1) = pair(bs, 'Long Term Debt')
    (cl0, cl1) = pair(bs, 'Current Liabilities')
    vals = [ar0, ar1, rv0, rv1, gp0, gp1, ta0, ta1, ca0, ca1,
            pp0, pp1, dp0, dp1, sg0, sg1, ltd0, ltd1, cl0, cl1]
    if any(v is None for v in vals) or not all([rv1, ta1, gp1, rv0, ta0]):
        return None
    try:
        dsri = (ar0 / rv0) / (ar1 / rv1)
        gmi = (gp1 / rv1) / (gp0 / rv0)
        aqi = ((1 - (ca0 + pp0) / ta0) / (1 - (ca1 + pp1) / ta1))
        sgi = rv0 / rv1
        depi = (dp1 / (dp1 + pp1)) / (dp0 / (dp0 + pp0))
        sgai = (sg0 / rv0) / (sg1 / rv1)
        lvgi = ((ltd0 + cl0) / ta0) / ((ltd1 + cl1) / ta1)
        tata = ((_row(fin, 'Net Income', 0) - (_row(cf, 'Operating Cash Flow', 0) or 0)) / ta0)
    except (ZeroDivisionError, TypeError):
        return None
    return (-4.84 + 0.92 * dsri + 0.528 * gmi + 0.404 * aqi + 0.892 * sgi
            + 0.115 * depi - 0.172 * sgai + 4.679 * tata - 0.327 * lvgi)


def score_fundamentals(ev):
    checks = []
    info = ev.get('info') or {}
    fin_sector = is_financial(ev)
    why_na = 'Financial Services — accrual/leverage ratios do not transfer to lenders'

    # Piotroski F-Score -> 12
    if fin_sector:
        checks.append(not_applicable('Piotroski F-Score', why_na))
    else:
        got, total, notes = piotroski(ev)
        if total < 5:
            checks.append(unknown('Piotroski F-Score', f'only {total}/9 signals computable', 12))
        else:
            checks.append(check('Piotroski F-Score', round(got / total * 12, 2), 12, OK,
                                f'{got}/{total} signals passed ({", ".join(notes) or "none"})'))

    # Altman Z''-EM -> 8 (also a hard gate; see hard_gates)
    if fin_sector:
        checks.append(not_applicable("Altman Z''-EM", why_na))
    else:
        z = altman_z_em(ev)
        if z is None:
            checks.append(unknown("Altman Z''-EM", 'balance-sheet items missing', 8))
        else:
            zone = 'safe' if z > 5.85 else ('grey' if z >= 4.15 else 'DISTRESS')
            checks.append(check("Altman Z''-EM", _scaled(z, 3.0, 7.0, 8), 8, OK,
                                f'Z"={z:.2f} ({zone})'))

    # Beneish M-Score -> 5
    if fin_sector:
        checks.append(not_applicable('Beneish M-Score', why_na))
    else:
        m = beneish_m(ev)
        if m is None:
            checks.append(unknown('Beneish M-Score', 'needs 2 yrs of 8 ratios; incomplete', 5))
        else:
            clean = m < -1.78
            checks.append(check('Beneish M-Score', 5 if clean else 0, 5, OK,
                                f'M={m:.2f} ' + ('(no manipulation flag)' if clean
                                                 else '(FLAG: above -1.78)')))

    # ROE -> 5
    roe = _num(info.get('returnOnEquity'))
    checks.append(check('ROE', _scaled(roe, 0.05, 0.25, 5), 5, OK, f'{roe:.1%}')
                  if roe is not None else unknown('ROE', 'not reported', 5))

    # Debt / equity -> 5 (lower is better; N/A for lenders, who are geared by design)
    if fin_sector:
        checks.append(not_applicable('Debt/Equity', 'leverage is the business model for lenders'))
    else:
        de = _num(info.get('debtToEquity'))
        if de is None:
            checks.append(unknown('Debt/Equity', 'not reported', 5))
        else:
            checks.append(check('Debt/Equity', _scaled(-de, -150, -10, 5), 5, OK, f'{de:.0f}%'))

    # Free-cash-flow yield -> 5
    fcf, mcap = _num(info.get('freeCashflow')), _num(info.get('marketCap'))
    if fcf is None or not mcap:
        checks.append(unknown('FCF yield', 'free cash flow or market cap missing', 5))
    else:
        y = fcf / mcap
        checks.append(check('FCF yield', _scaled(y, -0.02, 0.08, 5), 5, OK, f'{y:.1%}'))

    # Revenue growth -> 5
    g = _num(info.get('revenueGrowth'))
    checks.append(check('Revenue growth', _scaled(g, 0.0, 0.25, 5), 5, OK, f'{g:.1%}')
                  if g is not None else unknown('Revenue growth', 'not reported', 5))

    return checks


# ── Layer 2: Consensus (25) ──────────────────────────────────────────────

def score_consensus(ev):
    checks = []
    info = ev.get('info') or {}
    n = _num(info.get('numberOfAnalystOpinions')) or 0
    price = _num(info.get('currentPrice')) or _num(info.get('regularMarketPrice'))

    # With no analysts, the whole layer is UNKNOWN rather than zero. Scoring
    # it zero would penalise every uncovered micro cap — see module docstring.
    if n < 1:
        checks.append(unknown('Analyst rating', 'no analyst coverage', 10))
        checks.append(unknown('Target upside', 'no analyst coverage', 10))
        checks.append(unknown('Coverage breadth', 'no analyst coverage', 5))
    else:
        key = (info.get('recommendationKey') or '').lower()
        rating_pts = {'strong_buy': 10, 'buy': 8, 'hold': 4, 'underperform': 1, 'sell': 0}
        if key in rating_pts:
            checks.append(check('Analyst rating', rating_pts[key], 10, OK, f'{key} (n={n:.0f})'))
        else:
            checks.append(unknown('Analyst rating', f'unrecognised key {key!r}', 10))

        tgt = _num(info.get('targetMeanPrice'))
        if tgt and price:
            up = (tgt - price) / price
            checks.append(check('Target upside', _scaled(up, -0.05, 0.30, 10), 10, OK,
                                f'{up:+.1%} to consensus {tgt:,.0f}'))
        else:
            checks.append(unknown('Target upside', 'no consensus target', 10))

        checks.append(check('Coverage breadth', _scaled(n, 1, 10, 5), 5, OK, f'{n:.0f} analyst(s)'))

    # Advisory corroboration: is SPTulsian's target plausible against CMP?
    # Deliberately a sanity check on the ask, not agreement with the call.
    spt = _num(ev.get('spt_target'))
    if spt and price:
        up = (spt - price) / price
        if up <= 0:
            detail, pts = f'SPT target {spt:,.0f} is at or below CMP {price:,.0f}', 0
        elif up > 1.0:
            detail, pts = f'SPT target implies {up:+.0%} — implausibly large', 0
        else:
            detail, pts = f'SPT target {spt:,.0f} implies {up:+.1%}', _scaled(up, 0.0, 0.25, 5)
        checks.append(check('SPT target sanity', pts, 5, OK, detail))
    else:
        checks.append(unknown('SPT target sanity', 'no SPTulsian target on file', 5))

    return checks


# ── Layer 3: Technical (20) ──────────────────────────────────────────────

def score_technical(ev):
    checks = []
    hist = ev.get('history')
    if hist is None or getattr(hist, 'empty', True) or len(hist) < 60:
        n = 0 if hist is None or getattr(hist, 'empty', True) else len(hist)
        return [unknown('Technical', f'only {n} price bars (need >=60)', 20)]

    close, vol = hist['Close'], hist['Volume']
    price = float(close.iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None

    # Trend alignment -> 6
    if ma200 is None:
        checks.append(check('Trend alignment', 2 if price > ma50 else 0, 3, OK, potential=6, detail=
                            f'price {"above" if price > ma50 else "below"} 50DMA '
                            f'(too few bars for 200DMA)'))
    else:
        aligned = price > ma50 > ma200
        pts = 6 if aligned else (3 if price > ma200 else 0)
        checks.append(check('Trend alignment', pts, 6, OK,
                            f'price {price:,.0f} / 50DMA {ma50:,.0f} / 200DMA {ma200:,.0f}'
                            + (' — aligned' if aligned else '')))

    # Overbought guard (RSI-14) -> 4. Buying into an extended move is the
    # risk here, so this awards calm rather than strength.
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    if loss and loss > 0:
        rsi = 100 - 100 / (1 + gain / loss)
        pts = 4 if rsi < 70 else (2 if rsi < 80 else 0)
        checks.append(check('Overbought guard', pts, 4, OK, f'RSI14={rsi:.0f}'))
    else:
        checks.append(unknown('Overbought guard', 'RSI not computable', 4))

    # 52-week exhaustion guard -> 3
    hi52 = float(close.max())
    from_hi = (price - hi52) / hi52
    pts = 3 if from_hi < -0.10 else (1.5 if from_hi < -0.02 else 0)
    checks.append(check('52wk exhaustion guard', pts, 3, OK,
                        f'{from_hi:+.1%} vs 52wk high {hi52:,.0f}'))

    # Volume confirmation -> 3
    v20 = float(vol.tail(20).mean())
    v50 = float(vol.tail(50).mean())
    if v50 > 0:
        ratio = v20 / v50
        checks.append(check('Volume confirmation', _scaled(ratio, 0.6, 1.3, 3), 3, OK,
                            f'20d/50d volume = {ratio:.2f}x'))
    else:
        checks.append(unknown('Volume confirmation', 'no volume data', 3))

    # Liquidity floor -> 4 (also a hard gate)
    tv_cr = float((close * vol).tail(20).median()) / 1e7
    checks.append(check('Liquidity', _scaled(tv_cr, 0.5, 10.0, 4), 4, OK,
                        f'median 20d traded value Rs {tv_cr:.2f} cr/day'))
    return checks


# ── Layer 4: Governance (15) ─────────────────────────────────────────────

def score_governance(ev):
    checks = []
    info = ev.get('info') or {}

    # NSE surveillance -> 6 (GSM/Stage III+ is also a hard gate)
    if not ev.get('asm_checked'):
        checks.append(unknown('NSE surveillance', 'ASM list unreachable', 6))
    else:
        stage = ev.get('asm_stage')
        if not stage:
            checks.append(check('NSE surveillance', 6, 6, OK, 'not under ASM'))
        else:
            pts = 3 if 'I' == stage.replace('Stage', '').strip() else 0
            checks.append(check('NSE surveillance', pts, 6, OK, f'under ASM: {stage}'))

    # Promoter/insider holding -> 5
    held = _num(info.get('heldPercentInsiders'))
    if held is None or held <= 0:
        checks.append(unknown('Promoter holding', 'not reported', 5))
    else:
        checks.append(check('Promoter holding', _scaled(held, 0.20, 0.55, 5), 5, OK,
                            f'{held:.1%} insider-held'))

    # Institutional holding -> 2
    inst = _num(info.get('heldPercentInstitutions'))
    if inst is None or inst <= 0:
        checks.append(unknown('Institutional holding', 'not reported', 2))
    else:
        checks.append(check('Institutional holding', _scaled(inst, 0.02, 0.30, 2), 2, OK,
                            f'{inst:.1%} institution-held'))

    # Results event risk -> 2. Buying days before earnings is a distinct risk
    # from the company being bad, so it is scored, never gated.
    ts = info.get('earningsTimestamp') or info.get('mostRecentQuarter')
    if not ts:
        checks.append(unknown('Results event risk', 'no earnings date', 2))
    else:
        try:
            days = (datetime.fromtimestamp(float(ts)) - datetime.now()).days
            if 0 <= days <= 7:
                checks.append(check('Results event risk', 0, 2, OK, f'results in {days}d'))
            else:
                checks.append(check('Results event risk', 2, 2, OK, 'no imminent results'))
        except (TypeError, ValueError, OSError):
            checks.append(unknown('Results event risk', 'unparseable earnings date', 2))

    return checks


# ── Hard gates, composite, verdict ───────────────────────────────────────

def hard_gates(ev, layers):
    """Returns (gates, warnings).

    A GATE forces recommend-reject; a WARNING is surfaced but leaves the
    verdict to the score. The split is deliberate and was tuned against the
    live portfolio, where over-eager gates produced obvious false positives:

    - Beneish is a WARNING, never a gate. Its sales-growth term (SGI) pushes
      fast-growing companies over the -1.78 threshold, and on this portfolio
      it flagged BEL, CG Power, Solar Industries and Mazagon Dock — reputable
      high-growth defence and power names. A gate that rejects those trains
      the reader to ignore the tool.

    - Altman gates only in DEEP distress (<3.0), not merely below the 4.15
      grey-zone line. Vodafone Idea reads 0.57 and is unambiguous; a
      capital-intensive manufacturer sitting at 3.8 is not, and Z''-EM runs
      structurally low for them.

    Neither is used to silently drop a call: the reason is always reported
    alongside the recommendation.
    """
    gates, warns = [], []

    if not is_financial(ev):
        z = altman_z_em(ev)
        if z is not None:
            if z < 3.0:
                gates.append(f'Altman Z"={z:.2f} — deep distress (<3.0)')
            elif z < 4.15:
                warns.append(f'Altman Z"={z:.2f} — grey zone (<4.15), not deep distress')
        m = beneish_m(ev)
        if m is not None and m > -1.78:
            warns.append(f'Beneish M={m:.2f} — above -1.78; often a false positive on '
                         f'high sales growth, check the growth rate before reading it '
                         f'as an accounting concern')

    stage = (ev.get('asm_stage') or '')
    if 'GSM' in stage.upper():
        gates.append(f'Under NSE GSM surveillance ({stage})')
    elif stage and any(x in stage for x in ('III', 'IV')):
        gates.append(f'Under NSE ASM {stage}')
    elif stage:
        warns.append(f'Under NSE ASM {stage}')

    for c in layers.get('technical', []):
        if c['name'] == 'Liquidity' and c['status'] == OK:
            try:
                cr = float(c['detail'].split('Rs')[1].split('cr')[0])
                if cr < LIQUIDITY_FLOOR_CR:
                    gates.append(f'Illiquid: Rs {cr:.2f} cr/day median traded value '
                                 f'(floor Rs {LIQUIDITY_FLOOR_CR:.1f} cr) — a GTT may '
                                 f'not fill at the target')
            except (IndexError, ValueError):
                pass
    return gates, warns


def score_symbol(symbol, spt_target=None, log=_log):
    """Full conviction assessment for one symbol. Never raises."""
    ev = gather_evidence(symbol, spt_target=spt_target, log=log)

    layers = {
        'fundamentals': score_fundamentals(ev),
        'consensus': score_consensus(ev),
        'technical': score_technical(ev),
        'governance': score_governance(ev),
    }

    # Two independent quantities per layer:
    #   quality  = got/attempted — how well it did on what we could measure
    #   coverage = attempted/potential — how much of the layer we could measure
    #
    # The layer enters the composite weighted by budget*coverage, so a layer
    # resting on one surviving check carries proportionally less weight
    # instead of having that check silently extrapolated across the whole
    # budget. Quality is never reduced by missing data (rule 2), but the
    # evidence figure falls, which is the honest way to say "we know less".
    # N/A checks contribute no potential, so a bank is not marked
    # low-evidence for lacking ratios that never applied to it.
    layer_summary, total_awarded, total_attempted = {}, 0.0, 0.0
    for name, checks in layers.items():
        budget = LAYER_POINTS[name]
        got = sum(c['awarded'] for c in checks)
        att = sum(c['attempted'] for c in checks)
        pot = sum(c['potential'] for c in checks)
        quality = (got / att) if att else None
        coverage = (att / pot) if pot else 0.0
        weight = budget * coverage
        scaled_got = (quality * weight) if quality is not None else 0.0
        layer_summary[name] = {
            'budget': budget,
            'awarded': round(scaled_got, 1),
            'attempted': round(weight, 1),
            'pct': None if quality is None else round(quality * 100, 1),
            'coverage': round(coverage * 100, 1),
            'checks': checks,
        }
        total_awarded += scaled_got
        total_attempted += weight

    score = round(total_awarded / total_attempted * 100, 1) if total_attempted else None
    evidence_pct = round(total_attempted, 1)

    gates, warns = hard_gates(ev, layers)

    if score is None or evidence_pct < MIN_EVIDENCE_PCT:
        # Below the evidence floor the composite is computed off so few
        # checks that it is noise — publishing e.g. "100/100" from a single
        # surviving governance check would be actively misleading, so the
        # score is withheld rather than shown.
        tier, tier_label = 'NA', 'Insufficient evidence'
        verdict = 'INSUFFICIENT EVIDENCE'
        reasons = ([f'Only {evidence_pct:.0f} of 100 points could be assessed '
                    f'(floor {MIN_EVIDENCE_PCT}) — too little to judge either way.']
                   if score is not None else ['No layer could be assessed.'])
        score = None
    else:
        tier, tier_label = next((t, lbl) for floor, t, lbl in TIERS if score >= floor)
        reasons = []
        if gates:
            verdict = 'RECOMMEND REJECT'
            reasons = list(gates)
        elif score < ACCEPT_FLOOR:
            verdict = 'RECOMMEND REJECT'
            reasons = [f'Score {score:.0f} is below the {ACCEPT_FLOOR}-point floor.']
        else:
            verdict = 'ACCEPT'

    return {
        'symbol': symbol,
        'score': score,
        'evidence_pct': evidence_pct,
        'tier': tier,
        'tier_label': tier_label,
        'verdict': verdict,
        'reasons': reasons,
        'gates': gates,
        'warnings': warns,
        'is_financial': is_financial(ev),
        'sector': (ev.get('info') or {}).get('sector'),
        'layers': layer_summary,
        'errors': ev.get('errors', []),
    }


def format_report(result):
    """Human-readable breakdown — the score is only useful if its working is
    visible, so every check prints with its status and detail."""
    out = []
    s = result
    score = 'n/a' if s['score'] is None else f"{s['score']:.0f}/100"
    out.append(f"{s['symbol']}  score={score}  tier={s['tier']} ({s['tier_label']})  "
               f"evidence={s['evidence_pct']:.0f}/100  -> {s['verdict']}")
    if s['sector']:
        out.append(f"  sector: {s['sector']}" + ("  [financial: accrual ratios N/A]"
                                                 if s['is_financial'] else ""))
    for reason in s['reasons']:
        out.append(f"  ! {reason}")
    for w in s.get('warnings', []):
        out.append(f"  ~ warning: {w}")
    for name, layer in s['layers'].items():
        pct = 'not assessed' if layer['pct'] is None else f"quality {layer['pct']:.0f}%"
        out.append(f"  {name:13s} {layer['awarded']:5.1f}/{layer['attempted']:<4.1f} "
                   f"of {layer['budget']:<3d} ({pct}, coverage {layer['coverage']:.0f}%)")
        for c in layer['checks']:
            mark = {'OK': ' ', 'UNKNOWN': '?', 'NA': '-'}[c['status']]
            score_s = (f"{c['awarded']:5.1f}/{c['attempted']:<2.0f}"
                       if c['attempted'] else "   -   ")
            out.append(f"    {mark} {c['name']:24s} {score_s}  {c['detail']}")
    return '\n'.join(out)


if __name__ == '__main__':
    import sys
    sym = sys.argv[1] if len(sys.argv) > 1 else 'SOMANYCERA'
    tgt = float(sys.argv[2]) if len(sys.argv) > 2 else None
    print(format_report(score_symbol(sym, spt_target=tgt)))
