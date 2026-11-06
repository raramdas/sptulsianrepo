import copy
import datetime as dt
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from research.evidence import Prices, association, build, evaluate_scores, normalize, rho, summary
from research.export_oracle import extract


def prices():
    start = dt.date(2025, 1, 1)
    sessions = [(start + dt.timedelta(days=i)).isoformat() for i in range(500)
                if (start + dt.timedelta(days=i)).weekday() < 5]
    bars = [{'date': d, 'adjusted_close': 100 + i} for i, d in enumerate(sessions)]
    return {'basis': 'split_dividend_adjusted_close', 'source': 'SYNTHETIC_TEST_ONLY',
            'retrieved_at': '2026-06-01', 'calendar_source': 'synthetic weekday calendar',
            'calendar_verified': True, 'sessions': sessions,
            'series': {'ABC': {'identity_verified': True, 'currency': 'INR', 'bars': bars},
                       '^NSEI': {'identity_verified': True, 'currency': 'INR',
                                 'bars': [dict(b, adjusted_close=100) for b in bars]}}}


def snapshot():
    return {'format_version': 1, 'captured_at': '2026-06-01', 'source': 'SYNTHETIC_TEST_ONLY',
            'tables': {'trades': [
                {'trade_id': 1, 'symbol': 'ABC', 'buy_date': '2025-01-01', 'status': 'Closed',
                 'category_name': 'Big Gems', 'recommended_price': 100, 'target_price': 105,
                 'my_buy_date': '2025-01-02', 'my_buy_price': 100, 'my_buy_qty': 10,
                 'my_sell_date': '2025-02-01', 'my_sell_price': 110, 'my_sell_qty': 10},
                {'trade_id': 2, 'symbol': 'XYZ', 'buy_date': '2025-01-01', 'status': 'Cancelled'}],
                'conviction_scores': [
                    {'score_id': 1, 'trade_id': 1, 'scored_at': '2025-01-15', 'score': 80, 'model': 'lite'},
                    {'score_id': 2, 'trade_id': 1, 'scored_at': '2025-02-15', 'score': 100, 'model': 'lite'},
                    {'score_id': 3, 'trade_id': 1, 'scored_at': '2025-01-15', 'score': 60, 'model': 'full'}]}}


class Outcomes(unittest.TestCase):
    def test_next_session_and_drawdown(self):
        p = prices()
        path = p['series']['ABC']['bars']
        for b in path:
            b['adjusted_close'] = 100
        path[2]['adjusted_close'] = 150
        path[3]['adjusted_close'] = 75
        o = Prices(p, '2026-01-01').outcome('ABC', '2025-01-01', 30)
        self.assertEqual(o['entry_date'], '2025-01-02')
        self.assertEqual(o['return'], 0)
        self.assertAlmostEqual(o['max_gain'], .5)
        self.assertAlmostEqual(o['max_drawdown'], -.5)
        self.assertAlmostEqual(o['max_adverse_excursion'], -.25)
        self.assertTrue(o['milestones']['1.5']['hit'])
        self.assertFalse(o['milestones']['2']['hit'])

    def test_censoring_missing_and_no_forward_fill(self):
        p = prices()
        self.assertIn('RIGHT_CENSORED', Prices(p, '2025-01-10').outcome('ABC', '2025-01-01', 30)['flags'])
        del p['series']['ABC']['bars'][4]
        out = Prices(p, '2026-01-01').outcome('ABC', '2025-01-01', 30)
        self.assertIsNone(out['return'])
        self.assertIn('MISSING_OR_INVALID_PRICE_SESSIONS', out['flags'])

    def test_identity_adjustments_calendar_and_bad_prices(self):
        for field, value, flag in [('basis', 'raw', 'UNVERIFIED_ADJUSTMENT_BASIS'),
                                   ('calendar_verified', False, 'MISSING_SESSION_CALENDAR')]:
            p = prices(); p[field] = value
            self.assertIn(flag, Prices(p, '2026-01-01').outcome('ABC', '2025-01-01', 30)['flags'])
        for value in (None, 0, -1, float('nan'), float('inf')):
            p = prices(); p['series']['ABC']['bars'][3]['adjusted_close'] = value
            self.assertIsNone(Prices(p, '2026-01-01').outcome('ABC', '2025-01-01', 30)['return'])
        p = prices(); p['series']['ABC']['identity_verified'] = False
        self.assertIn('UNVERIFIED_INSTRUMENT', Prices(p, '2026-01-01').outcome('ABC', '2025-01-01', 30)['flags'])

    def test_duplicate_dates_fail(self):
        p = prices(); p['series']['ABC']['bars'].append(p['series']['ABC']['bars'][0])
        with self.assertRaises(ValueError):
            Prices(p, '2026-01-01')

    def test_split_adjusted_series_not_raw_advisory_price(self):
        p = prices()
        for b in p['series']['ABC']['bars']:
            b.update(adjusted_close=50, provider_close=100 if b['date'] < '2025-01-15' else 50)
        o = Prices(p, '2026-01-01').outcome('ABC', '2025-01-01', 30)
        self.assertEqual(o['return'], 0)
        self.assertEqual(o['max_drawdown'], 0)

    def test_summary_flat_and_missing(self):
        s = summary([None, -.5, 0, .5])
        self.assertEqual(s['n'], 3)
        self.assertEqual(s['win_rate'], 1/3)
        self.assertEqual(s['expectancy'], 0)
        self.assertEqual(s['payoff_ratio'], 1)
        self.assertIsNone(summary([])['win_rate'])


class Scores(unittest.TestCase):
    def test_identity_conflict_and_test_rows_never_enter_scores(self):
        s = snapshot(); p = prices()
        p['excluded_recommendations'] = {'trade:1': 'name and ticker mismatch'}
        recs, links = normalize(s)
        rows, _ = evaluate_scores(s['tables']['conviction_scores'], recs, links,
                                  Prices(p, '2026-05-01'), '^NSEI')
        self.assertEqual(rows, [])
        s['tables']['trades'][0]['status'] = 'DRY_RUN_TEST'
        recs, links = normalize(s)
        rows, _ = evaluate_scores(s['tables']['conviction_scores'], recs, links,
                                  Prices(prices(), '2026-05-01'), '^NSEI')
        self.assertEqual(rows, [])
    def test_stored_date_separate_models_not_latest_score(self):
        s = snapshot(); recs, links = normalize(s)
        rows, analysis = evaluate_scores(s['tables']['conviction_scores'], recs, links,
                                        Prices(prices(), '2026-05-01'), '^NSEI')
        self.assertEqual({r['score_id'] for r in rows}, {'1','3'})
        self.assertEqual(rows[0]['entry_date'], '2025-01-16')
        self.assertIn('lite/30d', analysis)
        self.assertIn('full/30d', analysis)
        self.assertIsNone(analysis['lite/30d']['spearman'])

    def test_invalid_scores_and_pre_recommendation_excluded(self):
        for value, date, model in [(101,'2025-01-15','lite'), (-1,'2025-01-15','lite'),
                                    (80,'2024-01-01','lite'), (80,None,'lite'), (80,'2025-01-15',None)]:
            s = snapshot(); s['tables']['conviction_scores'] = [
                {'score_id':1, 'trade_id':1, 'score':value, 'scored_at':date, 'model':model}]
            recs, links = normalize(s)
            rows, _ = evaluate_scores(s['tables']['conviction_scores'], recs, links,
                                     Prices(prices(),'2026-05-01'), '^NSEI')
            self.assertEqual(rows, [])

    def test_symbol_clustering(self):
        s = snapshot()
        s['tables']['trades'].append(dict(s['tables']['trades'][0], trade_id=3))
        s['tables']['conviction_scores'].append(
            {'score_id':4,'trade_id':3,'score':90,'model':'lite','scored_at':'2025-02-01'})
        recs, links = normalize(s)
        _, analysis = evaluate_scores(s['tables']['conviction_scores'], recs, links,
                                     Prices(prices(), '2026-05-01'), '^NSEI')
        self.assertEqual(analysis['lite/30d']['n_symbols'], 1)

    def test_rank_ties_constant_and_seed(self):
        self.assertAlmostEqual(rho([(1,2),(1,2),(3,4)]), 1)
        self.assertIsNone(rho([(1,2),(1,3),(1,4)]))
        pairs = [(i,i*i) for i in range(12)]
        self.assertEqual(association(pairs), association(pairs))


class Database(unittest.TestCase):
    def test_broker_accounts_preserved_separately(self):
        s = snapshot()
        s['tables']['kite_holdings_snapshot'] = [
            {'tradingsymbol':'ABC','account_label':account,'quantity':qty,'synced_at':'2026-01-01'}
            for account, qty in [('OLD',10),('NEW',20)]]
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d)/'e.sqlite')
            build(s, prices(), '2026-05-01', path)
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM broker_snapshots').fetchone()[0],2)

    def test_test_and_identity_conflict_exclusions(self):
        s = snapshot(); p = prices()
        s['tables']['trades'][1]['status']='DRY_RUN_TEST'
        p['excluded_recommendations']={'trade:1':'name/ticker conflict'}
        with tempfile.TemporaryDirectory() as d:
            report = build(s, p, '2026-05-01', str(Path(d)/'e.sqlite'))
            self.assertEqual(report['baseline']['30']['all']['n'],0)
            self.assertEqual(report['baseline']['30']['flags'],
                             {'INSTRUMENT_IDENTITY_CONFLICT':1,'SYNTHETIC_SOURCE_RECORD':1})
    def test_idempotence_append_and_preservation(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d)/'evidence.sqlite')
            s, p = snapshot(), prices()
            before = copy.deepcopy(s)
            a = build(s,p,'2026-05-01',path)
            b = build(s,p,'2026-05-01',path)
            self.assertEqual(a,b)
            self.assertEqual(s,before)
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],1)
                self.assertEqual(c.execute('SELECT count(*) FROM source_records').fetchone()[0],5)
                self.assertEqual(c.execute('SELECT count(*) FROM model_scores WHERE hc_research_score IS NOT NULL').fetchone()[0],0)
                self.assertEqual(c.execute("SELECT ledger_quantity FROM holdings WHERE trade_id='2'").fetchone()[0],None)
                self.assertEqual(c.execute("SELECT gross_return FROM realized_outcomes WHERE trade_id='2'").fetchone()[0],None)
                self.assertAlmostEqual(c.execute("SELECT gross_return FROM realized_outcomes WHERE trade_id='1'").fetchone()[0],.1)
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            s['captured_at'] = '2026-06-02'; s['tables']['trades'][0]['target_price'] = 120
            build(s,p,'2026-05-01',path)
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM runs').fetchone()[0],2)
                self.assertEqual(c.execute("SELECT target FROM target_observations WHERE recommendation_id='trade:1' ORDER BY observed_at").fetchall(), [(105.,),(120.,)])

    def test_explicit_links_only(self):
        s=snapshot(); s['tables']['recommendations'] = [
            {'rec_id':9,'symbol':'ABC','email_date':'2025-01-01'}]
        recs, links = normalize(s)
        self.assertEqual(len(recs),3)
        self.assertEqual(links['1'],'trade:1')
        s['tables']['trades'][0]['rec_id'] = 9
        recs, links = normalize(s)
        self.assertEqual(len(recs),2)
        self.assertEqual(links['1'],'rec:9')

    def test_future_realization_not_counted(self):
        with tempfile.TemporaryDirectory() as d:
            result = build(snapshot(),prices(),'2025-01-20',str(Path(d)/'e.sqlite'))
            self.assertEqual(result['realized_ledger_only']['n'],0)


class ReadOnlyExport(unittest.TestCase):
    def test_allowlist_and_rollback(self):
        class Lob:
            def read(self): return 'ABC'
        class Cursor:
            def execute(self, sql):
                calls.append(sql)
                if sql.startswith('SELECT table_name'):
                    self.rows = [('TRADES','TRADE_ID','NUMBER'),('TRADES','SYMBOL','VARCHAR2')]
                elif sql.startswith('SELECT trade_id'):
                    self.rows = [(1,Lob())]
            def fetchone(self): return [dt.datetime(2026,1,1)]
            def fetchall(self): return self.rows
            def __iter__(self): return iter(self.rows)
            def close(self): pass
        class Connection:
            def cursor(self): return Cursor()
            def rollback(self): calls.append('ROLLBACK')
        calls=[]
        s=extract(Connection())
        self.assertEqual(calls[0],'SET TRANSACTION READ ONLY')
        self.assertEqual(calls[-1],'ROLLBACK')
        self.assertTrue(all(x.startswith(('SELECT','SET TRANSACTION READ ONLY','ROLLBACK')) for x in calls))
        self.assertEqual(s['tables']['trades'],[{'trade_id':1,'symbol':'ABC'}])
        self.assertIn('CONVICTION_SCORES',s['missing'])


if __name__ == '__main__':
    unittest.main()
