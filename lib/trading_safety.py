"""Conservative, dependency-free checks shared by the scheduled trading paths."""
import math


def positive(value):
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def available_delivery_quantity(symbol, exchange, holdings, positions, gtts, orders):
    """Settled/T1 inventory less broker-used quantities and outstanding sells.

    Do not count intraday buys: settlement and delivery authorisation must be
    resolved before creating an unattended delivery sell. Discrepant holdings
    and any nonzero intraday position require review, rather than guessing
    whether the holdings response has already incorporated that activity.
    """
    def matches(row):
        same_market = row.get('exchange') == exchange
        cash_markets = exchange in ('NSE', 'BSE') and row.get('exchange') in ('NSE', 'BSE')
        return row.get('tradingsymbol') == symbol and (same_market or cash_markets)

    if any(matches(p) and p.get('product') == 'CNC' and
           (float(p.get('quantity', 0)) or float(p.get('day_sell_quantity', 0))
            or float(p.get('day_buy_quantity', 0))) for p in positions):
        raise ValueError('Delivery position activity requires reconciliation')
    matched_holdings = [h for h in holdings if matches(h)]
    if len(matched_holdings) > 1:
        raise ValueError('Multiple holding entries require identity reconciliation')
    inventory = 0
    for h in holdings:
        if not matches(h):
            continue
        if h.get('discrepancy') or h.get('product') != 'CNC':
            raise ValueError('Holding product or quantity requires reconciliation')
        inventory += max(0, float(h.get('quantity', 0)) + float(h.get('t1_quantity', 0))
                         - float(h.get('used_quantity', 0)))
    reserved = 0
    for g in gtts:
        if str(g.get('status', '')).lower() != 'active':
            continue
        # OCO alternatives reserve the maximum branch, not their sum.
        quantities = [float(o['quantity']) for o in g.get('orders', [])
                      if matches(o) and o.get('transaction_type') == 'SELL']
        reserved += max(quantities, default=0)
    for o in orders:
        if matches(o) and o.get('transaction_type') == 'SELL' and o.get('product') == 'CNC':
            if o.get('status') not in ('COMPLETE', 'CANCELLED', 'REJECTED'):
                reserved += max(0, float(o['quantity']) - float(o.get('filled_quantity', 0)))
    if not math.isfinite(inventory + reserved):
        raise ValueError('Non-finite broker quantity')
    return max(0, inventory - reserved)


def validate_sell_request(qty, trigger_price, last_price, available):
    if not positive(qty) or float(qty) != int(float(qty)):
        raise ValueError('Sell quantity must be a positive integer')
    if not positive(trigger_price) or not positive(last_price):
        raise ValueError('A real, positive market price and target are required')
    if float(qty) > available:
        raise ValueError(f'Insufficient unreserved delivery holdings: need {qty}, available {available}')
    if trigger_price - last_price < max(round(trigger_price * .005, 2), .15):
        raise ValueError('Target already crossed or too close to market; review required')


def confirmed_sell(info, symbol, qty, exchange):
    return bool(info and info.get('status') == 'COMPLETE'
                and info.get('symbol') == symbol and info.get('exchange') == exchange
                and info.get('transaction_type') == 'SELL' and info.get('product') == 'CNC'
                and positive(qty) and info.get('filled_qty') == qty
                and positive(info.get('avg_price')) and info.get('order_timestamp'))


def safe_buy_retry(info):
    # Missing history/holdings inference cannot prove an order never filled.
    return bool(info and info.get('status') in ('CANCELLED', 'REJECTED')
                and info.get('filled_qty') == 0 and not info.get('from_holdings'))
