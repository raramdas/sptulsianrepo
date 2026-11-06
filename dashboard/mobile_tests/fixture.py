"""Synthetic, offline-only UI fixture. Not imported by the production app."""
from copy import deepcopy
from datetime import datetime, timezone


def sample():
    holdings = [dict(tradingsymbol=s, account_label='NEW', quantity=q, average_price=a, last_price=p) for s,q,a,p in [('BSE',24,2400,2835),('CDSL',40,1200,1370),('TDPOWERSYS',110,355,408),('CGPOWER',65,630,612),('APOLLO',80,310,342)]]
    names=['BSE','Central Depository Services','TD Power Systems','CG Power','Apollo Micro Systems','Solar Industries']
    trades=[dict(trade_id=i+1,stock_name=name,symbol=['BSE','CDSL','TDPOWERSYS','CGPOWER','APOLLO','SOLARINDS'][i],category_name=['Big Gems','Medium Term','Big Gems','Little Gems','Short Term','Big Gems'][i],status='Open' if i<5 else 'PENDING_FILL',buy_date='2026-11-02',my_buy_qty=10,my_buy_price=350+i*150,target_price=450+i*150,have_interest='Have Interest',timeframe='6 months',conviction=95-i*4,conviction_model='lite',notes='Awaiting verified execution' if i==5 else '',gtt_id=None) for i,name in enumerate(names)]
    ideas=[dict(t,recommended_price=t['my_buy_price']) for t in trades]
    return dict(accounts=['NEW','OLD'],holdings=holdings,orders=[dict(order_id='DEMO-001',tradingsymbol='BSE',transaction_type='BUY',quantity=2,filled_quantity=2,price=2800,average_price=2800,status='COMPLETE',account_label='NEW',order_timestamp='2026-11-06T10:45:00+05:30')],gtts=[dict(id='DEMO-GTT',symbol='SOLARINDS',status='active',quantity=1,trigger_price=19159,account_label='NEW')],sync=[dict(account_label='NEW',synced_at=datetime.now(timezone.utc).isoformat(),holdings_count=5),dict(account_label='OLD',synced_at=datetime.now(timezone.utc).isoformat(),holdings_count=0)],budget=dict(total_budget=1000000,invested=638000,available=362000,utilization_pct=63.8,open_positions=5),categories=[dict(category_name=name,category_budget=budget,invested=invested,allocation_pct=pct,available=budget-invested) for name,budget,invested,pct in [('Big Gems',400000,290000,40),('Medium Term',250000,177000,25),('Short Term',200000,105000,20),('Little Gems',150000,66000,15)]],trades=trades,ideas=ideas,scores={t['symbol']:[t['conviction'],'lite'] for t in trades},performance=dict(total_realized=34750,trade_count=18),history=[dict(month=f'2026-{m:02}',cumulative_pnl=v) for m,v in [(7,4300),(8,12400),(9,9300),(10,22500),(11,34750)]])


class FixtureStore:
    def __init__(self):
        self.data=sample();self.writes=[]
        self.data['trades'].append(dict(trade_id=99,stock_name='Voltamp Transformers',symbol='VOLTAMP',category_name='Big Gems',status='NEEDS_REVIEW',buy_date='2026-11-06',notes='Confirm exact company symbol',my_buy_qty=None,target_price=None))
    def snapshot(self): return deepcopy(self.data)
    def refresh(self): return {'NEW': {'holdings':5},'OLD':{'holdings':0}}
    def target(self,tid,body): self.writes.append(('target',tid,body))
    def budget(self,body): self.writes.append(('budget',body))

# Methods used by the restored workflows. Synthetic data only.
def _symbol_preview(self, tid, symbol):
    return dict(trade_id=tid,stock_name='Voltamp Transformers',category='Big Gems',symbol=symbol.upper(),company_name='VOLTAMP TRANSFORMERS',exchange='NSE',cap_type='Small Cap',recommended_price=7800)
def _symbol_confirm(self, preview):
    self.writes.append(('symbol',preview))
    for t in self.data['trades']:
        if t['trade_id']==preview['trade_id']:t['status']='PENDING_BUY';t['symbol']=preview['symbol']
def _classification(self,q):return [dict(company_name='Voltamp Transformers',symbol='VOLTAMP',cap_type='Small Cap',amfi_rank=350,source_period='2026-H1')]
def _evidence(self,tid):return [dict(score=80,model='lite',scored_at='2026-11-06T10:00:00+05:30',evidence_pct=75,verdict='ACCEPT',reasons='[]',warnings='["Check earnings update"]',layers_json='{}')]
def _reconcile(self):
    return dict(observed_at='2026-11-06T10:00:00+05:30',accounts={'NEW':{'holdings':1,'active_sell_gtts':0}},rows=[dict(account='NEW',symbol='TEST',held=10,t1=0,reserved=0,uncovered=10,flags=['MISSING_GTT_COVERAGE'],candidate_lots=[dict(trade_id=1,quantity=10,target_price=120,state='EXISTING_TARGET_CANDIDATE')],target_missing_qty=0,triggers=[])],ledger_only=[])
FixtureStore.symbol_preview=_symbol_preview
FixtureStore.symbol_confirm=_symbol_confirm
FixtureStore.classification=_classification
FixtureStore.evidence=_evidence
FixtureStore.reconcile=_reconcile
