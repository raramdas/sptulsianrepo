"""The shared Oracle connection must survive every helper in a run.

Replays the exact 2026-09-01/02 failure: reconcile -> open_qty_for_symbol ->
requeue -> get_pending_buy_trades, all in one process.
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
from lib import budget_manager as bm

P = F = 0
def ok(c, l, e=''):
    global P, F
    if c: P += 1; print(f"  PASS  {l}")
    else: F += 1; print(f"  FAIL  {l}  {e}")

print("=== 1. open_qty_for_symbol must not close the shared connection ===")
c1 = bm.get_oracle_connection()
ok(c1 is not None, "connection opened")
n = bm.open_qty_for_symbol('BSE')
print(f"  open_qty_for_symbol('BSE') = {n}")
ok(n is not None, "query returned a value", n)
ok(c1.is_healthy(), "connection still healthy AFTER the call  <-- the bug")

print("\n=== 2. the exact sequence that broke the buy run ===")
pend = bm.get_pending_fill_trades()
print(f"  get_pending_fill_trades()  -> {len(pend)} row(s)")
for t in pend[:1]:
    q = bm.open_qty_for_symbol(t['symbol'], exclude_trade_id=t['trade_id'])
    print(f"  open_qty_for_symbol({t['symbol']}) -> {q}")
buys = bm.get_pending_buy_trades(retry_days=2)
print(f"  get_pending_buy_trades()   -> {len(buys)} row(s)")
ok(isinstance(buys, list), "get_pending_buy_trades did NOT fail with DPY-1001")
unsc = bm.unscored_pending_buys()
ok(isinstance(unsc, list), "unscored_pending_buys works after the sequence")
sells = bm.advisory_sells_today()
ok(isinstance(sells, list), "advisory_sells_today works after the sequence")

print("\n=== 3. a poisoned connection now self-heals ===")
c2 = bm.get_oracle_connection()
c2.close()                      # simulate any stray close anywhere
ok(not c2.is_healthy(), "connection deliberately closed")
c3 = bm.get_oracle_connection()
ok(c3 is not None and c3.is_healthy(),
   "get_oracle_connection returned a WORKING connection, not the dead one")
n2 = bm.open_qty_for_symbol('BSE')
ok(n2 is not None, "queries work again after the poisoning", n2)

print(f"\n{'='*56}\n  {P} passed, {F} failed\n{'='*56}")
sys.exit(1 if F else 0)
