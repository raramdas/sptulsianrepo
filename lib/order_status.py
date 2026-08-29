#!/usr/bin/env python3
"""
order_status.py — looks up Kite order fill status, with a holdings-based
fallback for orders placed on a previous day (Kite's /oms/orders endpoint
only returns the current day's orders).

Test independently:
    python3 -c "
from lib.kite_client import get_enctoken
from lib.order_status import get_order_status
enc = get_enctoken()
print(get_order_status('260701170233191', enc, symbol_hint='IDEA'))
"
"""
import requests
from lib.config import log, OMS_BASE
from lib.kite_client import kite_headers

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


def find_sell_order_for_symbol(symbol, qty, enctoken, min_price=None, exclude_order_ids=()):
    """Fallback: scan the order book for the SELL/CNC order produced by a
    GTT, used when the GTT's own `result` carries no order_id to query.

    Two guards, both added after this matched the WRONG order and closed a
    live position at a fictional price:

    min_price — a sell LIMIT cannot fill below its limit. Two IDEA trades were
      held at once, one with a 14.00 limit and one with 15.20. Matching on
      symbol+quantity alone (both were 354 shares) attributed the 14.09 fill
      to BOTH, booking the 15.20 trade as closed at 14.09 for an invented
      loss. Passing the trade's own limit rejects a fill that cannot be its.

    exclude_order_ids — one fill belongs to one trade. Without this, a single
      sell closes every open lot of the same size in the same symbol.

    Symbol and quantity are NOT sufficient identity. Prefer the GTT's own
    order_id whenever it is available; this is the last resort.
    """
    orders = get_all_orders(enctoken)
    excluded = {str(x) for x in (exclude_order_ids or ())}
    candidates = [
        o for o in orders
        if o.get('tradingsymbol', '').upper() == symbol.upper()
        and o.get('transaction_type') == 'SELL'
        and o.get('product') == 'CNC'
        and str(o.get('order_id', '')) not in excluded
    ]
    if min_price:
        # Allow a small tolerance for tick rounding, but nothing that could
        # confuse one limit with a materially lower one.
        floor = float(min_price) * 0.98
        kept = []
        for o in candidates:
            avg = float(o.get('average_price', 0) or 0)
            if avg <= 0 or avg >= floor:
                kept.append(o)
            else:
                log(f"  Ignoring sell {o.get('order_id')} @ {avg} — below this "
                    f"trade's limit {min_price}, so it belongs to another lot")
        candidates = kept
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
        'order_id':   str(o.get('order_id', '')),
    }


def get_order_status(order_id, enctoken, symbol_hint=None,
                     expected_qty=None, unexplained_qty=None):
    """Find order status and filled qty from Kite orders list.

    Falls back to holdings (which persist across days) if the order is in
    neither today's list nor order history — but only when the caller supplies
    `unexplained_qty`: how many shares of this symbol are NOT already claimed
    by other open lots in the ledger. Holdings are per symbol and orders are
    per lot, so without that number the fallback cannot tell this order's
    shares from anyone else's. `expected_qty` (what the order asked for) caps
    the claim further.

    Returns status 'NOT_FILLED' when holdings are fully explained by other
    lots — a positive statement that this order did not fill, not an absence
    of information.
    """
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
            # Fallback: infer a previous-day fill from holdings.
            #
            # Holdings are per SYMBOL; orders are per lot. This fallback used
            # to return the entire holding as this order's filled_qty, which
            # is only correct when the symbol is held in exactly one lot. On
            # 2026-08-28 it marked a 3-share BSE order COMPLETE for 8 shares —
            # every one of which belonged to eight earlier one-share trades —
            # recording Rs 26,608 of stock that was never bought, in a trade
            # that had not filled at all.
            #
            # So the caller must say how many shares are NOT already explained
            # by other open lots. Without that number there is no sound
            # inference to make and the fallback declines to guess.
            if symbol_hint and unexplained_qty is not None:
                held = get_holding_qty(symbol_hint, enctoken)
                claimable = min(int(unexplained_qty), held)
                if expected_qty is not None:
                    claimable = min(claimable, int(expected_qty))
                if claimable > 0:
                    log(f"  Order {order_id} not in orders; {held} {symbol_hint} held, "
                        f"{unexplained_qty} unexplained by other lots — "
                        f"treating as COMPLETE for {claimable}")
                    return {
                        'status':        'COMPLETE',
                        'filled_qty':    claimable,
                        'symbol':        symbol_hint.upper(),
                        'from_holdings': True,
                    }
                log(f"  Order {order_id} not in orders; all {held} {symbol_hint} share(s) "
                    f"are accounted for by other open lots — this order did NOT fill")
                return {'status': 'NOT_FILLED', 'filled_qty': 0,
                        'symbol': symbol_hint.upper(), 'from_holdings': True}
            if symbol_hint:
                log(f"  Order {order_id} not in orders and no unexplained-quantity "
                    f"context supplied — declining to infer a fill from holdings")
            log(f"  Order {order_id} not found in orders or holdings — may be unfilled or sold")
    except Exception as e:
        log(f"  get_order_status error for {order_id}: {e}")
    return None


if __name__ == '__main__':
    from kite_client import get_enctoken
    enc = get_enctoken()
    print("Orders today:", len(get_all_orders(enc)))
    print("Holdings:", len(get_holdings(enc)))
