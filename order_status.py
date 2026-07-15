#!/usr/bin/env python3
"""
order_status.py — looks up Kite order fill status, with a holdings-based
fallback for orders placed on a previous day (Kite's /oms/orders endpoint
only returns the current day's orders).

Test independently:
    python3 -c "
from kite_client import get_enctoken
from order_status import get_order_status
enc = get_enctoken()
print(get_order_status('260701170233191', enc, symbol_hint='IDEA'))
"
"""
import requests
from config import log, OMS_BASE
from kite_client import kite_headers

_orders_cache = None
_holdings_cache = None


def get_all_orders(enctoken):
    """Fetch all of today's orders from Kite (cached for this run)."""
    global _orders_cache
    if _orders_cache is not None:
        return _orders_cache
    try:
        r = requests.get(f'{OMS_BASE}/orders', headers=kite_headers(enctoken), timeout=10)
        data = r.json()
        if data.get('status') == 'success':
            _orders_cache = data.get('data', [])
            log(f"  Fetched {len(_orders_cache)} orders from Kite")
            return _orders_cache
    except Exception as e:
        log(f"  get_all_orders error: {e}")
    _orders_cache = []
    return []


def get_holdings(enctoken):
    """Fetch holdings (persists across days) — cached for this run."""
    global _holdings_cache
    if _holdings_cache is not None:
        return _holdings_cache
    try:
        r = requests.get(f'{OMS_BASE}/portfolio/holdings', headers=kite_headers(enctoken), timeout=10)
        data = r.json()
        if data.get('status') == 'success':
            _holdings_cache = data.get('data', [])
            log(f"  Fetched {len(_holdings_cache)} holdings from Kite")
            return _holdings_cache
    except Exception as e:
        log(f"  get_holdings error: {e}")
    _holdings_cache = []
    return []


def get_holding_qty(symbol, enctoken):
    """Return total held qty for a symbol from holdings (previous-day fills)."""
    holdings = get_holdings(enctoken)
    for h in holdings:
        if h.get('tradingsymbol', '').upper() == symbol.upper():
            total = int(h.get('quantity', 0)) + int(h.get('t1_quantity', 0))
            return total
    return 0


def find_sell_order_for_symbol(symbol, qty, enctoken):
    """Fallback: scan today's order book for a SELL/CNC order matching the
    symbol (and ideally quantity), used when a GTT's `result` field doesn't
    carry an order_id we can query directly. Returns the same dict shape as
    get_order_status(), or None."""
    orders = get_all_orders(enctoken)
    candidates = [
        o for o in orders
        if o.get('tradingsymbol', '').upper() == symbol.upper()
        and o.get('transaction_type') == 'SELL'
        and o.get('product') == 'CNC'
    ]
    if not candidates:
        return None
    exact = [o for o in candidates if int(o.get('quantity', 0)) == qty]
    pool = exact or candidates
    pool.sort(key=lambda o: o.get('order_timestamp', ''))
    o = pool[-1]
    return {
        'status':     o.get('status', '').upper(),
        'filled_qty': int(o.get('filled_quantity', 0)),
        'avg_price':  float(o.get('average_price', 0) or 0),
        'symbol':     o.get('tradingsymbol', ''),
    }


def get_order_status(order_id, enctoken, symbol_hint=None):
    """Find order status and filled qty from Kite orders list.
    Falls back to holdings (persists across days) if the order isn't in today's list."""
    try:
        orders = get_all_orders(enctoken)
        matched = [o for o in orders if str(o.get('order_id', '')) == str(order_id)]
        if matched:
            o = matched[-1]
            return {
                'status':     o.get('status', '').upper(),
                'filled_qty': int(o.get('filled_quantity', 0)),
                'avg_price':  float(o.get('average_price', 0) or 0),
                'symbol':     o.get('tradingsymbol', ''),
            }
        else:
            log(f"  Order {order_id} not found in today's orders — checking order history")
            r = requests.get(f'{OMS_BASE}/orders/{order_id}', headers=kite_headers(enctoken), timeout=10)
            data = r.json()
            if data.get('status') == 'success' and data.get('data'):
                o = data['data'][-1]
                return {
                    'status':     o.get('status', '').upper(),
                    'filled_qty': int(o.get('filled_quantity', 0)),
                    'avg_price':  float(o.get('average_price', 0) or 0),
                    'symbol':     o.get('tradingsymbol', ''),
                }
            # Fallback: check holdings for a previous-day fill
            if symbol_hint:
                held = get_holding_qty(symbol_hint, enctoken)
                if held > 0:
                    log(f"  Order {order_id} not in orders, but {held} shares of {symbol_hint} "
                        f"found in holdings — treating as COMPLETE")
                    return {
                        'status':        'COMPLETE',
                        'filled_qty':    held,
                        'symbol':        symbol_hint.upper(),
                        'from_holdings': True,
                    }
            log(f"  Order {order_id} not found in orders or holdings — may be unfilled or sold")
    except Exception as e:
        log(f"  get_order_status error for {order_id}: {e}")
    return None


if __name__ == '__main__':
    from kite_client import get_enctoken
    enc = get_enctoken()
    print("Orders today:", len(get_all_orders(enc)))
    print("Holdings:", len(get_holdings(enc)))
