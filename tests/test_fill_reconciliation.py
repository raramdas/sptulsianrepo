"""The holdings fallback must not claim shares belonging to other lots.

Replays the exact 2026-08-28 BSE case. No network, no DB: Kite calls are
stubbed.
"""
import sys
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")

from lib import order_status as os_mod

P = F = 0
def ok(c, label, extra=''):
    global P, F
    if c: P += 1; print(f"  PASS  {label}")
    else: F += 1; print(f"  FAIL  {label}  {extra}")

# Stub Kite: the order is in neither today's orders nor order history, and
# the account holds 8 BSE shares.
HELD = {'BSE': 8}
os_mod.get_all_orders = lambda enc: []
os_mod.get_holding_qty = lambda sym, enc: HELD.get(sym.upper(), 0)

class _Resp:
    def json(self): return {'status': 'error', 'data': None}
os_mod.requests = type('R', (), {'get': staticmethod(lambda *a, **k: _Resp())})()
os_mod.log = lambda m: None

def status(**kw):
    return os_mod.get_order_status('260827220550589', 'tok', symbol_hint='BSE', **kw)

print("=== the real case: 3-share order, 8 held, all 8 owned by other lots ===")
r = status(expected_qty=3, unexplained_qty=0)
print(f"  -> {r}")
ok(r['status'] == 'NOT_FILLED', "reports NOT_FILLED", r['status'])
ok(r['filled_qty'] == 0, "claims 0 shares", r['filled_qty'])
ok(r['filled_qty'] != 8, "does NOT claim the 8 shares owned by other trades")

print("\n=== a genuine previous-day fill: 3 ordered, 3 unexplained ===")
r = status(expected_qty=3, unexplained_qty=3)
print(f"  -> {r}")
ok(r['status'] == 'COMPLETE', "reports COMPLETE", r['status'])
ok(r['filled_qty'] == 3, "claims exactly the 3 it ordered", r['filled_qty'])

print("\n=== partial: 3 ordered, only 2 unexplained ===")
r = status(expected_qty=3, unexplained_qty=2)
ok(r['filled_qty'] == 2, "claims 2, not 3", r['filled_qty'])

print("\n=== never claims more than it ordered ===")
r = status(expected_qty=3, unexplained_qty=8)
ok(r['filled_qty'] == 3, "8 unexplained but only 3 ordered -> claims 3", r['filled_qty'])

print("\n=== no context supplied: declines to guess ===")
r = status()
ok(r is None or r.get('filled_qty', 0) == 0,
   "without unexplained_qty it infers nothing", r)

print("\n=== a real order in the list still wins over any fallback ===")
os_mod.get_all_orders = lambda enc: [{
    'order_id': '260827220550589', 'status': 'COMPLETE',
    'filled_quantity': 3, 'average_price': 3326.0, 'tradingsymbol': 'BSE'}]
r = status(expected_qty=3, unexplained_qty=0)
ok(r['status'] == 'COMPLETE' and r['filled_qty'] == 3,
   "authoritative order data is used, fallback not consulted", r)

print(f"\n{'='*54}\n  {P} passed, {F} failed\n{'='*54}")
sys.exit(1 if F else 0)
