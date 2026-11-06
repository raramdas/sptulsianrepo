"""Deterministic evidence database and baseline report from immutable snapshots.

Standard library only. Prices are separately supplied, with an explicit exchange
calendar and adjusted-close basis. Missing evidence is never replaced by zero.
"""
import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import random
import sqlite3
import statistics as st

VERSION = '1.2'
HORIZONS = (30, 90, 180, 365)


def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(x):
    return hashlib.sha256(canonical(x).encode()).hexdigest()


def number(x):
    try:
        x = float(x)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def day(x):
    try:
        return dt.date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def summary(values):
    values = [v for v in values if v is not None]
    wins, losses = [v for v in values if v > 0], [v for v in values if v < 0]
    return {'n': len(values), 'win_rate': len(wins) / len(values) if values else None,
            'mean_return': st.mean(values) if values else None,
            'median_return': st.median(values) if values else None,
            'average_winner': st.mean(wins) if wins else None,
            'average_loser': st.mean(losses) if losses else None,
            # Mean includes flat outcomes: empirical per-observation expectancy.
            'expectancy': st.mean(values) if values else None,
            'payoff_ratio': st.mean(wins) / abs(st.mean(losses)) if wins and losses else None}


def ranks(xs):
    result = [0.] * len(xs)
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    i = 0
    while i < len(xs):
        j = i + 1
        while j < len(xs) and xs[order[j]] == xs[order[i]]:
            j += 1
        for k in range(i, j):
            result[order[k]] = (i + j - 1) / 2
        i = j
    return result


def rho(pairs):
    if len(pairs) < 3:
        return None
    x, y = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    xm, ym = st.mean(x), st.mean(y)
    den = math.sqrt(sum((v-xm)**2 for v in x) * sum((v-ym)**2 for v in y))
    return sum((a-xm)*(b-ym) for a, b in zip(x, y)) / den if den else None


def association(pairs):
    result = {'n_symbols': len(pairs), 'spearman': rho(pairs), 'bootstrap_95pct': None}
    if len(pairs) >= 10:
        rng = random.Random(20261106)
        sims = [rho(rng.choices(pairs, k=len(pairs))) for _ in range(1000)]
        sims = sorted(s for s in sims if s is not None)
        if sims:
            result['bootstrap_95pct'] = [sims[int(.025*(len(sims)-1))],
                                         sims[int(.975*(len(sims)-1))]]
    return result


class Prices:
    def __init__(self, payload, as_of):
        self.payload, self.as_of = payload, day(as_of)
        self.calendar = [day(x) for x in payload.get('sessions', [])]
        if any(x is None for x in self.calendar) or self.calendar != sorted(set(self.calendar)):
            raise ValueError('sessions must be unique, sorted ISO dates')
        self.series = {}
        for symbol, data in payload.get('series', {}).items():
            rows = {}
            for bar in data.get('bars', []):
                d = day(bar.get('date'))
                if d is None or d in rows:
                    raise ValueError(f'Invalid or duplicate price date: {symbol}')
                rows[d] = number(bar.get('adjusted_close'))
            self.series[symbol] = rows

    def outcome(self, symbol, signal, horizon):
        out = {'return': None, 'max_gain': None, 'max_drawdown': None,
               'max_adverse_excursion': None, 'entry_date': None, 'end_date': None,
               'milestones': {}, 'flags': []}
        flags = out['flags']
        signal = day(signal)
        if not signal or not self.as_of:
            flags.append('MISSING_SIGNAL_DATE'); return out
        if self.payload.get('basis') != 'split_dividend_adjusted_close':
            flags.append('UNVERIFIED_ADJUSTMENT_BASIS'); return out
        if not self.payload.get('source') or not self.payload.get('retrieved_at'):
            flags.append('MISSING_PRICE_PROVENANCE'); return out
        if (not self.calendar or not self.payload.get('calendar_source')
                or self.payload.get('calendar_verified') is not True):
            flags.append('MISSING_SESSION_CALENDAR'); return out
        future = [d for d in self.calendar if d > signal]
        if signal >= self.as_of:
            flags.append('RIGHT_CENSORED'); return out
        if not future or (future[0] - signal).days > 7:
            flags.append('MISSING_ENTRY_SESSION'); return out
        entry = future[0]  # date-only signals: next-session close, never same-day hindsight
        deadline = entry + dt.timedelta(days=horizon)
        ends = [d for d in self.calendar if d >= deadline]
        if deadline > self.as_of:
            flags.append('RIGHT_CENSORED'); return out
        if not ends or (ends[0] - deadline).days > 7:
            flags.append('MISSING_END_SESSION'); return out
        end = ends[0]
        if end > self.as_of:
            flags.append('RIGHT_CENSORED'); return out
        out.update(entry_date=entry.isoformat(), end_date=end.isoformat(),
                   holding_days=(end-entry).days)
        metadata = self.payload.get('series', {}).get(symbol, {})
        if metadata.get('identity_verified') is not True:
            flags.append('UNVERIFIED_INSTRUMENT'); return out
        if metadata.get('currency') != 'INR':
            flags.append('UNSUPPORTED_CURRENCY'); return out
        rows = self.series.get(symbol, {})
        required = [d for d in self.calendar if entry <= d <= end]
        if any(rows.get(d) is None or rows[d] <= 0 for d in required):
            flags.append('MISSING_OR_INVALID_PRICE_SESSIONS'); return out
        values = [rows[d] for d in required]
        peak, drawdown = values[0], 0.
        for v in values:
            peak = max(peak, v)
            drawdown = min(drawdown, v/peak-1)
        out.update(return_=values[-1]/values[0]-1)
        out['return'] = out.pop('return_')
        out.update(max_gain=max(values)/values[0]-1, max_drawdown=drawdown,
                   max_adverse_excursion=min(values)/values[0]-1)
        for multiple in (1.5, 2, 3, 5):
            hit = next((d for d, v in zip(required, values) if v/values[0] >= multiple), None)
            out['milestones'][str(multiple)] = {'hit': hit is not None,
                'days': (hit-entry).days if hit else None}
        return out


SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, version TEXT NOT NULL, as_of TEXT NOT NULL,
 snapshot_hash TEXT NOT NULL, price_hash TEXT NOT NULL, manifest_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS source_records (
 run_id TEXT REFERENCES runs, source_table TEXT, source_id TEXT, payload_json TEXT NOT NULL,
 PRIMARY KEY(run_id,source_table,source_id));
CREATE TABLE IF NOT EXISTS recommendations (
 run_id TEXT REFERENCES runs, recommendation_id TEXT, source_table TEXT, source_id TEXT,
 symbol TEXT, recommended_at TEXT, category TEXT, direction TEXT, recommended_price REAL,
 observed_target REAL, have_interest TEXT, existing_status TEXT, flags_json TEXT,
 PRIMARY KEY(run_id,recommendation_id));
CREATE TABLE IF NOT EXISTS trade_links (
 run_id TEXT, trade_id TEXT, recommendation_id TEXT, PRIMARY KEY(run_id,trade_id),
 FOREIGN KEY(run_id,recommendation_id) REFERENCES recommendations);
CREATE TABLE IF NOT EXISTS model_scores (
 run_id TEXT, score_id TEXT, recommendation_id TEXT, model TEXT, scored_at TEXT,
 existing_score REAL, hc_research_score REAL, payload_json TEXT, flags_json TEXT,
 PRIMARY KEY(run_id,score_id), FOREIGN KEY(run_id,recommendation_id) REFERENCES recommendations);
CREATE TABLE IF NOT EXISTS executions (
 run_id TEXT REFERENCES runs, trade_id TEXT, status TEXT, payload_json TEXT,
 PRIMARY KEY(run_id,trade_id));
CREATE TABLE IF NOT EXISTS holdings (
 run_id TEXT REFERENCES runs, trade_id TEXT, symbol TEXT, ledger_quantity REAL,
 flags_json TEXT, PRIMARY KEY(run_id,trade_id));
CREATE TABLE IF NOT EXISTS realized_outcomes (
 run_id TEXT REFERENCES runs, trade_id TEXT, gross_return REAL, flags_json TEXT,
 PRIMARY KEY(run_id,trade_id));
CREATE TABLE IF NOT EXISTS target_observations (
 run_id TEXT, recommendation_id TEXT, observed_at TEXT, target REAL,
 effective_at TEXT, flags_json TEXT, PRIMARY KEY(run_id,recommendation_id),
 FOREIGN KEY(run_id,recommendation_id) REFERENCES recommendations);
CREATE TABLE IF NOT EXISTS price_history (
 run_id TEXT REFERENCES runs, symbol TEXT, session_date TEXT, adjusted_close REAL,
 PRIMARY KEY(run_id,symbol,session_date));
CREATE TABLE IF NOT EXISTS research_outcomes (
 run_id TEXT, recommendation_id TEXT, horizon_days INTEGER, payload_json TEXT,
 PRIMARY KEY(run_id,recommendation_id,horizon_days),
 FOREIGN KEY(run_id,recommendation_id) REFERENCES recommendations);
CREATE TABLE IF NOT EXISTS score_outcomes (
 run_id TEXT, score_id TEXT, horizon_days INTEGER, payload_json TEXT,
 PRIMARY KEY(run_id,score_id,horizon_days),
 FOREIGN KEY(run_id,score_id) REFERENCES model_scores);
CREATE TABLE IF NOT EXISTS broker_snapshots (
 run_id TEXT REFERENCES runs, snapshot_type TEXT, account_label TEXT, source_id TEXT,
 synced_at TEXT, payload_json TEXT,
 PRIMARY KEY(run_id,snapshot_type,account_label,source_id));
'''


def normalize(snapshot):
    tables = snapshot.get('tables', {})
    recs, links = {}, {}
    for r in tables.get('recommendations', []):
        key = 'rec:' + str(r['rec_id'])
        recs[key] = dict(r, recommendation_id=key, source_table='recommendations',
                         source_id=str(r['rec_id']), recommended_at=r.get('email_date'))
    for t in tables.get('trades', []):
        linked = 'rec:' + str(t.get('rec_id'))
        if t.get('rec_id') is not None and linked in recs:
            key = linked
        else:
            # Do not invent links using symbol/date: multiple calls/lots can share both.
            key = 'trade:' + str(t['trade_id'])
            recs[key] = dict(t, recommendation_id=key, source_table='trades',
                             source_id=str(t['trade_id']), recommended_at=t.get('buy_date'))
        links[str(t['trade_id'])] = key
    fingerprints = Counter((r.get('symbol'), r.get('recommended_at'), r.get('category_name'))
                           for r in recs.values())
    for r in recs.values():
        flags = ['TARGET_VINTAGE_UNKNOWN', 'HISTORICAL_FUNDAMENTALS_UNAVAILABLE',
                 'STOP_LOSS_UNAVAILABLE', 'RECOMMENDATION_UNIVERSE_COMPLETENESS_UNKNOWN',
                 'ATTRIBUTE_VINTAGE_UNKNOWN']
        if not r.get('spt_direction'):
            flags.append('DIRECTION_UNKNOWN_LONG_PROXY_ONLY')
        if r['source_table'] == 'trades':
            flags.append('TRADE_DERIVED_RECOMMENDATION')
        if fingerprints[(r.get('symbol'), r.get('recommended_at'), r.get('category_name'))] > 1:
            flags.append('POSSIBLE_DUPLICATE_CALL')
        if not r.get('symbol'):
            flags.append('MISSING_SYMBOL')
        if not day(r.get('recommended_at')):
            flags.append('MISSING_RECOMMENDATION_DATE')
        r['flags'] = flags
    earliest = {}
    for r in recs.values():
        date, symbol = day(r.get('recommended_at')), r.get('symbol')
        if date and symbol:
            earliest[symbol] = min(date, earliest.get(symbol, date))
    for r in recs.values():
        date = day(r.get('recommended_at'))
        first = earliest.get(r.get('symbol'))
        r['repeat_status'] = ('UNKNOWN' if not date or not first else
                              'repeat' if first < date else 'first_observed')
    return recs, links


def evaluate_scores(scores, recs, links, prices, benchmark):
    """First STORED score per recommendation/model, evaluated from its own date.

    Never rebuild past fundamentals from today's statements. A late score starts
    a late window, never gets paired with a return already earned before scoring.
    """
    chosen = {}
    for s in sorted(scores, key=lambda s: (str(s.get('scored_at') or ''), str(s['score_id']))):
        rid = links.get(str(s.get('trade_id')))
        model = s.get('model') or 'unknown'
        r = recs.get(rid)
        if rid in prices.payload.get('excluded_recommendations', {}):
            continue
        if r and r.get('status') == 'DRY_RUN_TEST':
            continue
        value = number(s.get('score'))
        if not r or value is None or not 0 <= value <= 100 or model == 'unknown':
            continue
        scored, recommended = day(s.get('scored_at')), day(r.get('recommended_at'))
        if not scored or not recommended or scored < recommended or scored > prices.as_of:
            continue
        if str(r.get('spt_direction') or 'Buy').lower() != 'buy':
            continue
        chosen.setdefault((rid, model), s)
    rows = []
    for (rid, model), s in chosen.items():
        for horizon in HORIZONS:
            r = recs[rid]
            out = prices.outcome(r.get('symbol'), s['scored_at'], horizon)
            bench = prices.outcome(benchmark, s['scored_at'], horizon)
            excess = (out['return'] - bench['return']
                      if out['return'] is not None and bench['return'] is not None else None)
            rows.append({'score_id': str(s['score_id']), 'recommendation_id': rid,
                         'symbol': r.get('symbol'), 'model': model,
                         'scored_at': s['scored_at'], 'score': number(s['score']),
                         'horizon': horizon, 'excess_return': excess,
                         'benchmark_flags': bench['flags'], **out})
    # Select earliest scored observation per symbol BEFORE checking outcome coverage.
    # This avoids substituting a later, surviving observation for missing early data.
    groups = defaultdict(dict)
    for row in sorted(rows, key=lambda r: (r['scored_at'], r['score_id'])):
        groups[row['model'], row['horizon']].setdefault(row['symbol'], row)
    analysis = {}
    for (model, horizon), observations in groups.items():
        all_rows = sorted(observations.values(), key=lambda r: (r['scored_at'], r['symbol']))
        valid = [r for r in all_rows if r['excess_return'] is not None]
        pairs = [(r['score'], r['excess_return']) for r in valid]
        cutoff = all_rows[len(all_rows)//2]['scored_at'][:10] if all_rows else None
        cohorts = {label: association([(r['score'], r['excess_return']) for r in valid
                    if (r['scored_at'][:10] < cutoff) == earlier])
                   for label, earlier in [('earlier', True), ('later', False)]}
        analysis[f'{model}/{horizon}d'] = {
            'eligible_symbols': len(all_rows), **association(pairs),
            'coverage': len(valid)/len(all_rows) if all_rows else None,
            'calendar_split_date': cutoff, 'temporal_stability': cohorts,
            'score_thresholds': {str(t): summary([r['excess_return'] for r in valid if r['score'] >= t])
                                 for t in (70, 80, 90)},
            'all_scored': summary([r['excess_return'] for r in valid]),
            'conclusion': 'DESCRIPTIVE_ONLY; no validated sizing or HC model'}
    return rows, analysis


def build(snapshot, price_payload, as_of, db_path, benchmark='^NSEI'):
    if snapshot.get('format_version') != 1 or not day(snapshot.get('captured_at')):
        raise ValueError('Snapshot requires format_version=1 and captured_at')
    if not day(as_of):
        raise ValueError('Explicit ISO as-of date required')
    prices = Prices(price_payload, as_of)
    recs, links = normalize(snapshot)
    for rid, rec in recs.items():
        if rid in price_payload.get('excluded_recommendations', {}):
            rec['flags'].append('INSTRUMENT_IDENTITY_CONFLICT')
        if rec.get('status') == 'DRY_RUN_TEST':
            rec['flags'].append('SYNTHETIC_SOURCE_RECORD')
    manifest = {'version': VERSION, 'as_of': as_of, 'snapshot_hash': digest(snapshot),
                'price_hash': digest(price_payload), 'benchmark': benchmark,
                'horizons_calendar_days': HORIZONS, 'entry_rule': 'next_session_close',
                'captured_at': snapshot['captured_at'], 'price_provenance':
                {k: v for k, v in price_payload.items() if k not in ('series', 'sessions')},
                'source': snapshot.get('source'), 'missing_tables': snapshot.get('missing', [])}
    run = digest(manifest)
    outcomes = []
    for rid, r in sorted(recs.items()):
        for horizon in HORIZONS:
            if r.get('status') == 'DRY_RUN_TEST':
                out = {'return': None, 'flags': ['SYNTHETIC_SOURCE_RECORD']}
            elif rid in price_payload.get('excluded_recommendations', {}):
                out = {'return': None, 'flags': ['INSTRUMENT_IDENTITY_CONFLICT'],
                       'exclusion_reason': price_payload['excluded_recommendations'][rid]}
            elif str(r.get('spt_direction') or 'Buy').lower() != 'buy':
                out = {'return': None, 'flags': ['NON_BUY_ADVISORY']}
            else:
                out = prices.outcome(r.get('symbol'), r.get('recommended_at'), horizon)
            outcomes.append({'recommendation_id': rid, 'horizon': horizon, **out})
    score_rows, predictive = evaluate_scores(snapshot['tables'].get('conviction_scores', []),
                                             recs, links, prices, benchmark)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        with conn:
            # Same inputs are idempotent; changed snapshots append a different run.
            if not conn.execute('SELECT 1 FROM runs WHERE run_id=?', (run,)).fetchone():
                conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?)',
                             (run, VERSION, as_of, digest(snapshot), digest(price_payload), canonical(manifest)))
                for table, pk in [('trades','trade_id'), ('recommendations','rec_id'), ('conviction_scores','score_id')]:
                    for r in snapshot['tables'].get(table, []):
                        conn.execute('INSERT INTO source_records VALUES (?,?,?,?)',
                                     (run, table, str(r[pk]), canonical(r)))
                for rid, r in recs.items():
                    conn.execute('INSERT INTO recommendations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (run,rid,r['source_table'],r['source_id'],r.get('symbol'),r.get('recommended_at'),
                         r.get('category_name'),r.get('spt_direction'),number(r.get('recommended_price')),
                         number(r.get('target_price')),r.get('have_interest'),r.get('status'),canonical(r['flags'])))
                    conn.execute('INSERT INTO target_observations VALUES (?,?,?,?,?,?)',
                        (run,rid,snapshot['captured_at'],number(r.get('target_price')),None,
                         canonical(['EFFECTIVE_DATE_UNKNOWN', 'NOT_PROVEN_ORIGINAL_TARGET'])))
                for tid, rid in links.items():
                    conn.execute('INSERT INTO trade_links VALUES (?,?,?)', (run,tid,rid))
                for table, key in [('kite_holdings_snapshot','tradingsymbol'),
                                   ('kite_orders_snapshot','order_id'), ('kite_gtt_snapshot','gtt_id')]:
                    for item in snapshot['tables'].get(table, []):
                        conn.execute('INSERT INTO broker_snapshots VALUES (?,?,?,?,?,?)',
                            (run,table,item.get('account_label') or 'UNKNOWN',str(item[key]),
                             item.get('synced_at'),canonical(item)))
                for s in snapshot['tables'].get('conviction_scores', []):
                    rid = links.get(str(s.get('trade_id')))
                    flags = ['MODEL_VERSION_UNKNOWN'] + ([] if rid else ['UNLINKED_SCORE'])
                    conn.execute('INSERT INTO model_scores VALUES (?,?,?,?,?,?,?,?,?)',
                        (run,str(s['score_id']),rid,s.get('model') or 'unknown',s.get('scored_at'),
                         number(s.get('score')),None,canonical(s),canonical(flags)))
                for t in snapshot['tables'].get('trades', []):
                    tid = str(t['trade_id'])
                    conn.execute('INSERT INTO executions VALUES (?,?,?,?)', (run,tid,t.get('status'),canonical(t)))
                    bq, sq = number(t.get('my_buy_qty')), number(t.get('my_sell_qty'))
                    bp, sp = number(t.get('my_buy_price')), number(t.get('my_sell_price'))
                    flags, qty, ret = ['LEDGER_ONLY_NOT_BROKER_VERIFIED'], None, None
                    if bq is not None and bq > 0 and bp is not None and bp > 0 and day(t.get('my_buy_date')):
                        if t.get('status') == 'Open' and sq in (None, 0):
                            qty = bq
                        elif t.get('status') in ('Open', 'Closed') and sq is not None and 0 <= sq <= bq:
                            qty = bq - sq
                        else:
                            flags.append('AMBIGUOUS_REMAINING_QUANTITY')
                        if (t.get('status') == 'Closed' and sq == bq and sp is not None and sp > 0
                                and day(t.get('my_sell_date')) and day(t['my_sell_date']) >= day(t['my_buy_date'])
                                and day(t['my_sell_date']) <= day(as_of)):
                            ret = sp/bp-1
                    else:
                        flags.append('NO_CONFIRMED_LEDGER_BUY')
                    if t.get('status') == 'Closed' and ret is None:
                        flags.append('CLOSED_OUTCOME_UNRESOLVED')
                        if sq is None or sp is None or not day(t.get('my_sell_date')):
                            flags.append('MISSING_EXIT_DETAILS')
                        if sq is not None and bq is not None and sq != bq:
                            flags.append('BUY_SELL_QUANTITY_MISMATCH')
                    conn.execute('INSERT INTO holdings VALUES (?,?,?,?,?)',
                                 (run,tid,t.get('symbol'),qty,canonical(flags)))
                    conn.execute('INSERT INTO realized_outcomes VALUES (?,?,?,?)',
                                 (run,tid,ret,canonical(flags + ['GROSS_EXCLUDES_COSTS_DIVIDENDS'])))
                for symbol, bars in prices.series.items():
                    for date, value in bars.items():
                        conn.execute('INSERT INTO price_history VALUES (?,?,?,?)', (run,symbol,date.isoformat(),value))
                for o in outcomes:
                    conn.execute('INSERT INTO research_outcomes VALUES (?,?,?,?)',
                                 (run,o['recommendation_id'],o['horizon'],canonical(o)))
                for o in score_rows:
                    conn.execute('INSERT INTO score_outcomes VALUES (?,?,?,?)',
                                 (run,o['score_id'],o['horizon'],canonical(o)))
        realized = [row[0] for row in conn.execute('SELECT gross_return FROM realized_outcomes WHERE run_id=?', (run,))]
    finally:
        conn.close()
    groups = {}
    for horizon in HORIZONS:
        selected = [o for o in outcomes if o['horizon'] == horizon]
        groups[str(horizon)] = {'all': summary([o['return'] for o in selected]),
            'total_observations': len(selected),
            'flags': dict(Counter(f for o in selected for f in o['flags']))}
        valid = [o for o in selected if o['return'] is not None]
        groups[str(horizon)]['path_metrics'] = {
            'median_max_gain': st.median([o['max_gain'] for o in valid]) if valid else None,
            'median_max_drawdown': st.median([o['max_drawdown'] for o in valid]) if valid else None,
            'worst_individual_drawdown': min(o['max_drawdown'] for o in valid) if valid else None,
            'milestone_rates': {str(m): sum(o['milestones'][str(m)]['hit'] for o in valid)/len(valid)
                                if valid else None for m in (1.5,2,3,5)}}
        first_by_symbol = {}
        for o in sorted(selected, key=lambda o: (str(recs[o['recommendation_id']].get('recommended_at') or ''),
                                                  o['recommendation_id'])):
            rec = recs[o['recommendation_id']]
            if (rec.get('symbol') and day(rec.get('recommended_at'))
                    and not set(o['flags']) & {'SYNTHETIC_SOURCE_RECORD','INSTRUMENT_IDENTITY_CONFLICT','NON_BUY_ADVISORY'}):
                first_by_symbol.setdefault(rec['symbol'], o)
        groups[str(horizon)]['first_observation_per_symbol'] = {
            'eligible_symbols': len(first_by_symbol),
            **summary([o['return'] for o in first_by_symbol.values()])}
        for dimension in ('category_name', 'have_interest', 'stock_type', 'repeat_status'):
            buckets = defaultdict(list)
            for o in selected:
                buckets[recs[o['recommendation_id']].get(dimension) or 'UNKNOWN'].append(o['return'])
            groups[str(horizon)][dimension] = {k: summary(v) for k, v in sorted(buckets.items())}
    source_trades = snapshot['tables'].get('trades', [])
    broker_freshness = {}
    for table in ('kite_holdings_snapshot', 'kite_orders_snapshot', 'kite_gtt_snapshot'):
        dates = sorted({r.get('synced_at') for r in snapshot['tables'].get(table, []) if r.get('synced_at')})
        latest = dates[-1] if dates else None
        age = (day(as_of) - day(latest)).days if day(latest) else None
        broker_freshness[table] = {'latest_sync': latest, 'age_calendar_days': age,
                                  'flag': 'STALE_SNAPSHOT' if age is not None and age > 0 else
                                          'MISSING_SNAPSHOT' if age is None else 'SAME_DAY_NOT_REALTIME'}
    return {'run_id': run, 'manifest': manifest,
            'source_counts': {k: len(v) for k, v in snapshot['tables'].items()},
            'score_coverage': {'stored_rows': len(snapshot['tables'].get('conviction_scores', [])),
                               'selected_first_scores': len({r['score_id'] for r in score_rows}),
                               'selection': 'first valid stored score per recommendation/model; later scores retained but not tested'},
            'recommendations': len(recs), 'baseline': groups,
            'data_quality': {'execution_statuses': dict(Counter(t.get('status') for t in source_trades)),
                             'broker_snapshot_freshness': broker_freshness,
                             'closed_records': sum(t.get('status') == 'Closed' for t in source_trades),
                             'usable_closed_records': sum(v is not None for v in realized)},
            'realized_ledger_only': summary(realized), 'predictive': predictive,
            'recommendation_flags': dict(Counter(f for r in recs.values() for f in r['flags'])),
            'status': 'BASELINE_COMPUTED' if any(o['return'] is not None for o in outcomes)
                       else 'INSUFFICIENT_PRICE_EVIDENCE',
            'limitations': [
                'Observed universe may exclude omitted recommendations, delistings and historical archives.',
                'Trade-derived observations are not independent calls; unresolved duplicates are retained and flagged.',
                'Missing advisory direction is flagged and evaluated only as a hypothetical long-return proxy.',
                'Fixed-horizon adjusted close returns are research proxies, not executable fills or portfolio returns.',
                'Close-only drawdowns and milestone dates omit intraday extremes; taxes, fees and slippage excluded.',
                'Target revisions are snapshot observations, not reconstructed effective dates; target-hit tests withheld.',
                'Original valuation, sector, financial releases and catalysts are not reconstructed from current data.',
                'Stored scores are evaluated from their own dates, separately by model, without retraining.',
                'Predictive summary selects first scored observation per symbol/model; bootstrap assumes independent symbols.',
                'Temporal cohorts are stability diagnostics, not a purged out-of-sample backtest.',
                'The default NIFTY 50 benchmark (^NSEI) is a price index, not TRI; dividend-adjusted stock excess returns are not matched total-return alpha.',
                'Model version is absent in the source schema; lite/full separation does not isolate intra-model revisions.',
                'Existing scores are preserved; HC research scores remain NULL pending validation.']}


def report_markdown(report):
    lines = ['# SPTulsian Historical Evidence — baseline', '',
             f"Status: **{report['status']}**", f"As of: {report['manifest']['as_of']}",
             f"Run: `{report['run_id']}`", '',
             '## Coverage', '', f"Source rows: `{canonical(report['source_counts'])}`",
             f"Recommendation observations: {report['recommendations']}", '',
             '## Fixed-horizon baseline', '',
             '| Calendar days | Eligible observations | Total observations | Mean | Median | Win rate |',
             '|---|---:|---:|---:|---:|---:|']
    def pct(x):
        return 'unavailable' if x is None else f'{x:.2%}'
    for h, data in report['baseline'].items():
        s = data['all']
        lines.append(f"| {h} | {s['n']} | {data['total_observations']} | {pct(s['mean_return'])} | {pct(s['median_return'])} | {pct(s['win_rate'])} |")
    lines += ['', '### Sensitivity: first observation per stock', '',
              '| Calendar days | Complete stocks | Eligible stocks | Mean | Median | Win rate |',
              '|---|---:|---:|---:|---:|---:|']
    for h, data in report['baseline'].items():
        s = data['first_observation_per_symbol']
        lines.append(f"| {h} | {s['n']} | {s['eligible_symbols']} | {pct(s['mean_return'])} | {pct(s['median_return'])} | {pct(s['win_rate'])} |")
    lines += ['', '## Existing-score predictive power', '',
              'First scored observation per symbol and model; excess return over the configured benchmark.']
    if not report['predictive']:
        lines += ['', '**Insufficient evidence:** no eligible stored scores. No predictive claim is supported.']
    for key, data in report['predictive'].items():
        lines += ['', f"### {key}", '', f"Symbols with matched outcomes: {data['n_symbols']} / {data['eligible_symbols']}",
                  f"Spearman correlation: {data['spearman']}; bootstrap 95% interval: {data['bootstrap_95pct']}"]
    lines += ['', '## Data-quality flags', '', '```json',
              json.dumps({'source_quality': report['data_quality'],
                          'recommendations': report['recommendation_flags'],
                          'outcomes': {h: d['flags'] for h, d in report['baseline'].items()}}, indent=2), '```',
              '', '## Interpretation limits', '']
    lines += ['- ' + x for x in report['limitations']]
    lines += ['', 'Full category, interest, cap-class, score-threshold and temporal-cohort statistics are in report.json.', '']
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', required=True)
    p.add_argument('--prices', required=True)
    p.add_argument('--as-of', required=True)
    p.add_argument('--database', required=True)
    p.add_argument('--out', required=True, help='New report directory; refuses overwrite')
    p.add_argument('--benchmark', default='^NSEI')
    a = p.parse_args()
    out = Path(a.out)
    if out.exists():
        raise FileExistsError(out)
    result = build(json.loads(Path(a.snapshot).read_text()), json.loads(Path(a.prices).read_text()),
                   a.as_of, a.database, a.benchmark)
    out.mkdir(parents=True)
    (out / 'report.json').write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    (out / 'report.md').write_text(report_markdown(result))
    print(f"{result['status']}: {out / 'report.md'}")


if __name__ == '__main__':
    main()
