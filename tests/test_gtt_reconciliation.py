"""Offline plan generation: no account credentials or mutations."""
import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dashboard'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from reconciliation import analyse
from lib.trading_safety import available_delivery_quantity

H=dict(tradingsymbol='TEST',exchange='BSE',product='CNC',quantity=10,t1_quantity=0,used_quantity=0,isin='ISIN1',last_price=100)
def trigger(q=10,symbol='TEST',kind='single'):
 order=dict(tradingsymbol=symbol,exchange='NSE',transaction_type='SELL',product='CNC',quantity=q,price=120)
 return dict(id=1,status='active',type=kind,condition=dict(tradingsymbol=symbol,trigger_values=[119]),orders=[order] if kind=='single' else [order,order])
def snap(holdings=None,gtts=None,positions=None,ledger=None):
 return dict(observed_at='2026-11-06T17:00:00Z',accounts={'NEW':dict(holdings=[H] if holdings is None else holdings,gtts=[] if gtts is None else gtts,positions=positions or [],orders=[])},ledger=ledger or [])
class ReconciliationTests(unittest.TestCase):
 def test_cross_exchange_is_same_holding(self):
  r=analyse(snap(gtts=[trigger()]))['rows'][0]
  self.assertEqual(r['held'],10);self.assertEqual(r['uncovered'],0);self.assertNotIn('REMOVE_CANDIDATE_NO_HOLDING',r['flags'])
  self.assertEqual(available_delivery_quantity('TEST','NSE',[H],[],[],[]),10)
  self.assertEqual(available_delivery_quantity('TEST','NSE',[H],[],[trigger()],[]),0)
 def test_oco_reserved_once(self):
  self.assertEqual(analyse(snap(gtts=[trigger(kind='two-leg')]))['rows'][0]['reserved'],10)
 def test_true_orphan(self):
  self.assertIn('REMOVE_CANDIDATE_NO_HOLDING',analyse(snap(holdings=[],gtts=[trigger()]))['rows'][0]['flags'])
 def test_alias_not_false_orphan(self):
  flags=analyse(snap(gtts=[trigger(symbol='TEST-BE')]))['rows'][0]['flags']
  self.assertIn('SYMBOL_SERIES_REVIEW',flags);self.assertNotIn('REMOVE_CANDIDATE_NO_HOLDING',flags)
 def test_intraday_not_false_oversell(self):
  p=dict(tradingsymbol='TEST',product='CNC',quantity=2,day_buy_quantity=2)
  flags=analyse(snap(holdings=[dict(H,quantity=1)],gtts=[trigger(q=2)],positions=[p]))['rows'][0]['flags']
  self.assertIn('INTRADAY_REVIEW',flags);self.assertNotIn('EXCESS_RESERVED_QUANTITY',flags)
 def test_exact_ledger_required_for_target_candidates(self):
  t=dict(trade_id=1,status='Open',symbol='TEST',my_buy_qty=10,target_price=120,gtt_id=None)
  r=analyse(snap(ledger=[t]))['rows'][0]
  self.assertEqual(r['candidate_lots'][0]['state'],'EXISTING_TARGET_CANDIDATE')
  self.assertIn('MISSING_GTT_COVERAGE',r['flags'])
  self.assertFalse(analyse(snap(ledger=[dict(t,my_buy_qty=11)]))['rows'][0]['candidate_lots'])
 def test_crossed_target_never_add_candidate(self):
  t=dict(trade_id=1,status='Open',symbol='TEST',my_buy_qty=10,target_price=90,gtt_id=None)
  self.assertEqual(analyse(snap(ledger=[t]))['rows'][0]['candidate_lots'][0]['state'],'TARGET_ALREADY_CROSSED_OR_TOO_CLOSE')
 def test_failed_account_is_not_empty_holdings(self):
  r=analyse(dict(observed_at='now',accounts={'NEW':{'error':'unavailable'}},ledger=[]))
  self.assertFalse(r['rows']);self.assertIn('error',r['accounts']['NEW'])
 def test_duplicate_holdings_fail_closed(self):
  with self.assertRaises(ValueError):available_delivery_quantity('TEST','NSE',[H,H],[],[],[])
if __name__=='__main__':unittest.main()
