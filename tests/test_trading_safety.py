"""Offline regression tests. Extract real functions without credential imports."""
import ast
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.trading_safety import available_delivery_quantity, validate_sell_request, confirmed_sell, safe_buy_retry, positive
from lib.bands import CONVICTION_SIZING, CONVICTION_MIN_SCORE, SIZING_MODEL
ROOT = Path(__file__).resolve().parents[1]


def function(path, name, **context):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), context)
    return context[name]


class SafetyTests(unittest.TestCase):
    def test_inventory_and_reservations(self):
        holding = dict(tradingsymbol='SOLARINDS', exchange='NSE', product='CNC', quantity=3)
        sell = dict(holding, transaction_type='SELL', quantity=1)
        available = lambda h, g=[], o=[], p=[]: available_delivery_quantity('SOLARINDS','NSE',h,p,g,o)
        self.assertEqual(available([]), 0)
        self.assertEqual(available([holding]), 3)
        self.assertEqual(available([holding],[dict(status='active', orders=[sell])]),2)
        self.assertEqual(available([holding],[dict(status='active', orders=[sell,sell])]),2)
        self.assertEqual(available([holding],[],[dict(sell,status='OPEN')]),2)
        with self.assertRaises(ValueError): available([holding],p=[dict(holding,quantity=1)])
        with self.assertRaises(ValueError): available([dict(holding,discrepancy=True)])

    def test_zero_holdings_and_false_price(self):
        for qty, trigger, ltp, held in [(1,19159,19000,0),(1,19159,19770,1),(1,100,None,1),(.5,100,90,1),(1,100,float('nan'),1)]:
            with self.assertRaises(ValueError): validate_sell_request(qty,trigger,ltp,held)
        validate_sell_request(1,100,90,1)

    def test_fill_identity(self):
        info=dict(status='COMPLETE',symbol='SOLARINDS',exchange='NSE',transaction_type='SELL',product='CNC',filled_qty=1,avg_price=20000,order_timestamp='2026-11-05 10:00:00')
        self.assertTrue(confirmed_sell(info,'SOLARINDS',1,'NSE'))
        for field,value in [('symbol','OTHER'),('transaction_type','BUY'),('filled_qty',2),('filled_qty',0),('avg_price',0),('product','MIS'),('order_timestamp',None)]:
            self.assertFalse(confirmed_sell(dict(info,**{field:value}),'SOLARINDS',1,'NSE'))

    def test_buy_retry(self):
        for status in ['UNKNOWN','OPEN','COMPLETE','NOT_FILLED','TRIGGER PENDING']:
            self.assertFalse(safe_buy_retry(dict(status=status,filled_qty=0)))
        self.assertFalse(safe_buy_retry(dict(status='CANCELLED',filled_qty=1)))
        self.assertTrue(safe_buy_retry(dict(status='CANCELLED',filled_qty=0)))
        self.assertTrue(safe_buy_retry(dict(status='REJECTED',filled_qty=0)))

    def test_actual_sizing_gates(self):
        lookup=Mock()
        size=function('main.py','decide_position_size', math=math, REQUIRE_HAVE_INTEREST=True,
            CONVICTION_SIZING_ENABLED=True,CONVICTION_SIZING=CONVICTION_SIZING,
            CONVICTION_MIN_SCORE=CONVICTION_MIN_SCORE,get_latest_conviction=lookup)
        for score,expected in [(68.99,None),(69,10000),(92,10000),(92.01,25000),(100,25000),(101,None),(float('nan'),None),(float('inf'),None),('bad',None)]:
            lookup.return_value=dict(score=score)
            self.assertEqual(size(dict(trade_id=1,have_interest='Have Interest'))[0],expected)
        lookup.return_value=dict(score=99)
        self.assertIsNone(size(dict(trade_id=1,have_interest='No Interest'))[0])
        self.assertIsNone(size(dict(trade_id=1,have_interest=''))[0])

    def test_budget_fail_closed(self):
        connection=Mock()
        check=function('lib/budget_manager.py','check_budget_available',get_oracle_connection=connection,get_category_id=lambda _:1,log=lambda _:None,math=math)
        connection.return_value=None
        self.assertFalse(check('Big Gems','Large Cap',10000,'SOLARINDS')[0])
        connection.return_value=Mock()
        connection.return_value.cursor.side_effect=RuntimeError('offline')
        self.assertFalse(check('Big Gems','Large Cap',10000,'SOLARINDS')[0])
        connection.return_value.cursor.side_effect=None
        connection.return_value.cursor.return_value.fetchone.return_value=(1,)
        self.assertFalse(check('Big Gems','Large Cap',10000,'SOLARINDS')[0])
        self.assertFalse(check('Big Gems','Large Cap',float('nan'),'SOLARINDS')[0])

    def test_score_model_query(self):
        conn=Mock();conn.cursor.return_value.fetchone.return_value=(95,'ACCEPT',100)
        get=function('lib/budget_manager.py','get_latest_conviction',get_oracle_connection=lambda:conn,log=lambda _:None,SIZING_MODEL=SIZING_MODEL)
        self.assertEqual(get(1)['score'],95)
        args=conn.cursor.return_value.execute.call_args.args
        self.assertIn('model = :model',args[0]);self.assertEqual(args[1]['model'],'lite')

    def test_gtt_terminal_never_recreates(self):
        for dry in (True,False):
            for state in ('triggered','cancelled','expired','unknown'):
                write=Mock();place=Mock(side_effect=AssertionError('broker mutation'))
                run=function('main_gtt_oracle.py','run',log=lambda _:None,GTT_DRY_RUN=dry,IST=timezone.utc,
                    datetime=datetime,timedelta=timedelta,get_enctoken=lambda:'fake',
                    get_open_trades_with_target=lambda _:[],get_open_trades_with_gtt=lambda:[dict(trade_id=223,stock_name='Solar',symbol='SOLARINDS',gtt_id='123',buy_order_id='buy',target_price=19160,my_buy_qty=1)],
                    get_gtt_detail=lambda *_:dict(status=state,orders=[]),confirmed_sell=confirmed_sell,
                    EXCHANGE='NSE',mark_gtt_failure_oracle=write,place_gtt=place,close_oracle_connection=lambda:None)
                run();place.assert_not_called()
                self.assertEqual(write.call_count,0 if dry else 1)

    def test_place_gtt_broker_tripwire(self):
        import json
        http=Mock();http.post.side_effect=AssertionError('must not place an order')
        def get(url,**kwargs):
            data={'net':[]} if url.endswith('positions') else []
            return Mock(json=lambda:dict(status='success',data=data))
        http.get.side_effect=get
        place=function('lib/kite_client.py','place_gtt',requests=http,OMS_BASE='mock',EXCHANGE='NSE',
            kite_headers=lambda _: {},get_tick_size=lambda _:1,_round_to_tick=lambda x,*a,**k:round(x),json=json,log=lambda _:None)
        with self.assertRaises(ValueError):place('SOLARINDS',1,19160,19770,'fake')
        http.post.assert_not_called()
        http.get.side_effect=RuntimeError('holdings unavailable')
        with self.assertRaises(RuntimeError):place('SOLARINDS',1,19160,19000,'fake')
        http.post.assert_not_called()

    def test_buy_dry_run_has_no_writes(self):
        deny=Mock(side_effect=AssertionError('dry run wrote state'))
        for decision,budget in [((10000,None,False),True),((10000,None,False),False),((None,'No interest',False),True)]:
            attempt=function('main.py','attempt_buy',DRY_RUN=True,log=lambda _:None,math=math,positive=positive,
                decide_position_size=lambda _:decision,CONVICTION_SIZING_ENABLED=True,
                get_market_price=lambda *a,**k:100,check_budget_available=lambda *a,**k:(budget,1),
                kite_buy=deny,log_to_sheet=deny,record_buy_attempt=deny,update_trade_after_buy_attempt=deny,requeue_for_retry=deny)
            attempt(dict(trade_id=1,stock_name='Test',symbol='TEST',category_name='Big Gems',recommended_price=100,stock_type='Large Cap'),'fake')
        deny.assert_not_called()
        reconcile=function('main.py','reconcile_pending_fills',DRY_RUN=True,log=lambda _:None,get_pending_fill_trades=deny)
        reconcile('fake');deny.assert_not_called()

    def test_gtt_initial_dry_run_has_no_writes(self):
        deny=Mock(side_effect=AssertionError('dry run wrote state'))
        run=function('main_gtt_oracle.py','run',log=lambda _:None,GTT_DRY_RUN=True,IST=timezone.utc,
            datetime=datetime,timedelta=timedelta,get_enctoken=lambda:'fake',
            get_open_trades_with_target=lambda _:[dict(trade_id=1,stock_name='Test',symbol='TEST',target_price=200,buy_order_id='buy',my_buy_qty=1)],
            get_order_status=lambda *a,**k:dict(status='COMPLETE',filled_qty=1,symbol='TEST',transaction_type='BUY',product='CNC'),
            get_open_trades_with_gtt=lambda:[],place_gtt=deny,set_gtt_placed_oracle=deny,close_oracle_connection=lambda:None)
        run();deny.assert_not_called()

    def test_pending_order_reconciliation_never_guesses(self):
        for status,filled,expected in [('OPEN',0,0),('UNKNOWN',0,0),('NOT_FILLED',0,0),('CANCELLED',1,0),('CANCELLED',0,1)]:
            retry=Mock();update=Mock()
            reconcile=function('main.py','reconcile_pending_fills',
                get_pending_fill_trades=lambda:[dict(trade_id=1,stock_name='Solar',symbol='SOLARINDS',buy_attempts=1,buy_order_id='buy')],
                open_qty_for_symbol=lambda *a,**k:0,get_holding_qty=lambda *a:0,
                get_order_status=lambda *a,**k:dict(status=status,filled_qty=filled),
                log=lambda _:None,safe_buy_retry=safe_buy_retry,BUY_RETRY_DAYS=2,DRY_RUN=False,
                requeue_for_retry=retry,update_trade_after_buy_attempt=update)
            reconcile('fake');self.assertEqual(retry.call_count,expected);update.assert_not_called()

    def test_budget_positive_and_cap_boundaries(self):
        for rows,expected in [([(0,),(50000,),(2,),(500000,),(0,)],True),
                              ([(0,),(50000,),(2,),(500000,),(1,)],False),
                              ([(0,),(9999,)],False), ([(0,),(float('nan'),)],False)]:
            conn=Mock();conn.cursor.return_value.fetchone.side_effect=rows
            check=function('lib/budget_manager.py','check_budget_available',get_oracle_connection=lambda:conn,
                get_category_id=lambda _:1,log=lambda _:None,math=math)
            self.assertEqual(check('Big Gems','Micro Cap',10000,'SOLARINDS')[0],expected)

    def test_close_rolls_back_ambiguous_lots(self):
        conn=Mock();conn.cursor.return_value.rowcount=2
        close=function('lib/budget_manager.py','close_trade_in_oracle',get_oracle_connection=lambda:conn)
        with self.assertRaises(ValueError):close('buy','2026-11-06',20000,1)
        conn.commit.assert_not_called();conn.rollback.assert_called_once()
        with self.assertRaises(ValueError):close('buy','2026-11-06',None,1)

if __name__ == '__main__': unittest.main()
