#!/usr/bin/env python3
"""Read-only broker/ledger audit. Output is private; never commit it.

Run from the repository on the configured host. Authenticates both configured
accounts, then performs broker GETs and an Oracle read-only transaction. No
order/GTT or ledger changes. A failed source aborts rather than implying zero.
"""
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from lib.config import OMS_BASE, EXCHANGE
from lib.kite_client import get_enctoken_for, kite_headers
from lib.budget_manager import get_oracle_connection
from lib.trading_safety import available_delivery_quantity


def audit():
    result = {'observed_at': datetime.now(timezone.utc).isoformat(), 'accounts': {}}
    for account, prefix in [('NEW', 'ZERODHA'), ('OLD', 'ZERODHA_OLD')]:
        if not os.getenv(prefix + '_USER_ID'):
            result['accounts'][account] = {'unavailable': 'credentials not configured'}
            continue
        token = get_enctoken_for(os.environ[prefix + '_USER_ID'],
                                os.environ[prefix + '_PASSWORD'],
                                os.environ[prefix + '_TOTP_SECRET'])
        def read(endpoint):
            response = requests.get(f'{OMS_BASE}/{endpoint}', headers=kite_headers(token), timeout=20)
            response.raise_for_status()
            payload = response.json()
            if payload.get('status') != 'success':
                raise RuntimeError(f'{account}: cannot read {endpoint}')
            return payload['data']
        holdings = read('portfolio/holdings')
        positions = read('portfolio/positions')['net']
        gtts = read('gtt/triggers')
        orders = read('orders')
        active = [g for g in gtts if g.get('status') == 'active']
        warnings = []
        for g in active:
            for o in g.get('orders', []):
                if o.get('transaction_type') != 'SELL':
                    continue
                symbol, exchange = o['tradingsymbol'], o['exchange']
                try:
                    qty = available_delivery_quantity(symbol, exchange, holdings, positions,
                            [other for other in active if other['id'] != g['id']], orders)
                    reason = 'insufficient unreserved holdings' if o['quantity'] > qty else None
                except (ValueError, TypeError, KeyError) as exc:
                    reason = str(exc)
                if reason:
                    warnings.append({'gtt_id': g['id'], 'symbol': symbol,
                                     'quantity': o['quantity'], 'reason': reason})
        result['accounts'][account] = {
            'holding_symbols': len(holdings), 'active_gtts': len(active),
            'gtt_review_required': warnings,
            'solar_holdings': [dict(symbol=h['tradingsymbol'],quantity=h.get('quantity'),t1=h.get('t1_quantity'))
                               for h in holdings if h.get('tradingsymbol') == 'SOLARINDS'],
            'solar_positions': [dict(symbol=p['tradingsymbol'],quantity=p.get('quantity'))
                                for p in positions if p.get('tradingsymbol') == 'SOLARINDS'],
            'solar_gtts': [dict(id=g['id'],status=g['status'],trigger_values=g.get('condition',{}).get('trigger_values'))
                           for g in gtts if g.get('condition',{}).get('tradingsymbol') == 'SOLARINDS'],
        }
    conn = get_oracle_connection()
    if conn is None:
        raise RuntimeError('Oracle unavailable')
    try:
        cur = conn.cursor()
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SELECT trade_id, status, my_buy_qty, gtt_id, notes FROM trades WHERE symbol = 'SOLARINDS' AND status = 'Open' ORDER BY trade_id")
        result['solar_open_ledger'] = [dict(zip(['trade_id','status','quantity','gtt_id','notes'],r)) for r in cur.fetchall()]
        cur.execute("SELECT category_name, COUNT(*) FROM trades WHERE status = 'PENDING_FILL' GROUP BY category_name")
        result['categories_held_for_pending_fills'] = [dict(category=r[0],count=r[1]) for r in cur.fetchall()]
        cur.execute('SELECT model, COUNT(*) FROM conviction_scores GROUP BY model')
        result['score_models'] = dict(cur.fetchall())
    finally:
        conn.rollback()
        conn.close()
    return result


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2, default=str))
