"""Read-only reconciliation. This module has no broker mutation functions."""
from collections import defaultdict
from datetime import datetime, timezone
import math


def number(value):
    try:
        n=float(value or 0)
        if not math.isfinite(n) or n < 0: raise ValueError('Invalid quantity')
        return n
    except (TypeError, ValueError):
        raise ValueError('Invalid broker/ledger quantity')


def analyse(snapshot):
    """Compare per-account delivery inventory, never allocate shares by guesswork.

    NSE/BSE display exchanges do not partition demat holdings. Exact symbols
    may match across those exchanges. Series suffix aliases remain REVIEW.
    OCO alternatives reserve max(branch quantities), not their sum.
    """
    result={'observed_at':snapshot['observed_at'],'accounts':{},'rows':[],'ledger_only':[]}
    ledger=defaultdict(list)
    for t in snapshot.get('ledger',[]):
        if t.get('status')=='Open':ledger[t.get('symbol')].append(t)
    for account, data in snapshot['accounts'].items():
        if data.get('error'):
            result['accounts'][account]={'error':data['error']};continue
        hs=defaultdict(list);gs=defaultdict(list);pending=defaultdict(float);activity=set();aliases={}
        for h in data['holdings']:hs[h['tradingsymbol']].append(h)
        for p in data['positions']:
            if p.get('product')=='CNC' and any(float(p.get(k) or 0) for k in ('quantity','day_buy_quantity','day_sell_quantity')):activity.add(p['tradingsymbol'])
        for o in data['orders']:
            if o.get('product')=='CNC' and o.get('transaction_type')=='SELL' and o.get('status') not in ('COMPLETE','CANCELLED','REJECTED'):
                pending[o['tradingsymbol']]+=max(0,number(o['quantity'])-number(o.get('filled_quantity')))
        for g in data['gtts']:
            if str(g.get('status','')).lower()!='active':continue
            sells=[o for o in g.get('orders',[]) if o.get('transaction_type')=='SELL']
            if not sells:continue
            symbol=g.get('condition',{}).get('tradingsymbol') or sells[0]['tradingsymbol']
            canonical=symbol
            if symbol.endswith('-BE') and symbol[:-3] in hs:
                canonical=symbol[:-3];aliases[canonical]=symbol
            gs[canonical].append(dict(id=str(g['id']),symbol=symbol,type=g.get('type'),quantity=max(number(o['quantity']) for o in sells),
                trigger_values=g.get('condition',{}).get('trigger_values',[]),prices=[o.get('price') for o in sells],
                exchanges=sorted(set(o.get('exchange','') for o in sells))))
        for symbol in sorted(set(hs)|set(gs)):
            holdings=hs[symbol];triggers=gs[symbol];lots=ledger[symbol] if account=='NEW' else []
            qty=sum(number(h.get('quantity'))+number(h.get('t1_quantity')) for h in holdings)
            t1=sum(number(h.get('t1_quantity')) for h in holdings)
            used=sum(number(h.get('used_quantity')) for h in holdings)
            reserved=sum(g['quantity'] for g in triggers)
            uncertain=any(h.get('discrepancy') or h.get('product')!='CNC' for h in holdings)
            isins={h['isin'] for h in holdings if h.get('isin')}
            if len(holdings)>1 or len(isins)>1:uncertain=True
            latest=next((h.get('last_price') for h in holdings if h.get('last_price')),None)
            row=dict(account=account,symbol=symbol,held=qty,settled=max(0,qty-t1),t1=t1,reserved=reserved,
                     pending_sell=pending[symbol],uncovered=max(0,qty-used-pending[symbol]-reserved),
                     triggers=triggers,last_price=latest,ledger_qty=sum(number(t.get('my_buy_qty')) for t in lots),
                     flags=[],candidate_lots=[],target_missing_qty=0)
            if symbol in aliases:row['flags'].append('SYMBOL_SERIES_REVIEW')
            if uncertain:row['flags'].append('INVENTORY_REVIEW')
            if symbol in activity:row['flags'].append('INTRADAY_REVIEW')
            if t1:row['flags'].append('SETTLEMENT_REVIEW')
            if not qty and triggers and not row['flags']:row['flags'].append('REMOVE_CANDIDATE_NO_HOLDING')
            elif reserved+pending[symbol]>max(0,qty-used) and symbol not in activity:row['flags'].append('EXCESS_RESERVED_QUANTITY')
            if lots and row['ledger_qty']!=qty:row['flags'].append('LEDGER_QUANTITY_MISMATCH')
            linked={g['id']:g for g in triggers}
            if any(str(t.get('gtt_id')) in linked and number(t.get('my_buy_qty'))!=linked[str(t['gtt_id'])]['quantity'] for t in lots):
                row['flags'].append('LINKED_LOT_QUANTITY_MISMATCH')
            # Candidate target levels must already exist in an exactly matched
            # ledger. Never invent prices or distribute an uncertain lot.
            if row['uncovered'] and lots and row['ledger_qty']==qty and not uncertain and symbol not in activity and symbol not in aliases and 'LINKED_LOT_QUANTITY_MISMATCH' not in row['flags']:
                free=row['uncovered']
                for t in lots:
                    if str(t.get('gtt_id')) in linked:continue
                    q=number(t.get('my_buy_qty'));target=t.get('target_price')
                    if not target:
                        row['target_missing_qty']+=q;continue
                    if q>free:continue
                    reason='EXISTING_TARGET_CANDIDATE'
                    if not latest or float(target)<=float(latest)*1.005:reason='TARGET_ALREADY_CROSSED_OR_TOO_CLOSE'
                    elif t1:reason='WAIT_FOR_SETTLEMENT_REVIEW'
                    row['candidate_lots'].append(dict(trade_id=t['trade_id'],quantity=q,target_price=target,state=reason))
                    free-=q
            if row['uncovered']:row['flags'].append('MISSING_GTT_COVERAGE')
            if row['uncovered'] and not row['candidate_lots']:row['flags'].append('UNCOVERED_SHARES_TARGET_NEEDED')
            if any(latest and any(number(v)<=number(latest) for v in g['trigger_values']) for g in triggers):
                row['flags'].append('TRIGGER_BELOW_SNAPSHOT_PRICE_REVIEW_INTENT')
            result['rows'].append(row)
        if account=='NEW':
            for symbol,lots in ledger.items():
                if symbol not in hs and symbol not in activity:
                    result['ledger_only'].append(dict(symbol=symbol,trade_ids=[t['trade_id'] for t in lots],quantity=sum(number(t.get('my_buy_qty')) for t in lots)))
        result['accounts'][account]={'holdings':len(data['holdings']),'active_sell_gtts':sum(len(v) for v in gs.values())}
    return result


def collect(db, broker):
    """Broker GET calls only; Oracle queries only. Fail closed per account."""
    snap={'observed_at':datetime.now(timezone.utc).isoformat(),'accounts':{},'ledger':db.trades().to_dict('records')}
    for account in broker._ACCOUNTS:
        try:
            snap['accounts'][account]={'holdings':broker._get('/portfolio/holdings',account),
                'positions':broker._get('/portfolio/positions',account)['net'],
                'gtts':broker._get('/gtt/triggers',account),'orders':broker._get('/orders',account)}
        except Exception:
            snap['accounts'][account]={'error':'Account could not be read. No quantities inferred; try again.'}
    return snap
