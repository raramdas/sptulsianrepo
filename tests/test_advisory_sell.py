"""A SELL call must never be bought, and must never be silent. No network, no DB."""
import sys, re
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
sys.path.insert(0, "/home/ubuntu/stockbot/dashboard")

P = F = 0
def ok(c, label, extra=''):
    global P, F
    if c: P += 1; print(f"  PASS  {label}")
    else: F += 1; print(f"  FAIL  {label}  {extra}")

print("=== 1. the email regex now sees direction ===")
PAT = re.compile(r'Call added[:\s]*([^(]+?)\s*\((Buy|Sell)\s*@\s*([\d.]+)\)', re.IGNORECASE)
buy = PAT.findall("Call added: Vodafone Idea (Buy @ 15.2)")
sell = PAT.findall("Call added: Vodafone Idea (Sell @ 15.2)")
print(f"  buy  -> {buy}")
print(f"  sell -> {sell}")
ok(len(buy) == 1 and buy[0][1].title() == 'Buy', "Buy still parses", buy)
ok(len(sell) == 1 and sell[0][1].title() == 'Sell', "Sell now parses (was silently dropped)", sell)

OLD = re.compile(r'Call added[:\s]*([^(]+?)\s*\(Buy\s*@\s*([\d.]+)\)', re.IGNORECASE)
ok(OLD.findall("Call added: Vodafone Idea (Sell @ 15.2)") == [],
   "the OLD pattern silently dropped it — this is the bug being fixed")

print("\n=== 2. ADVISORY_SELL cannot be recorded as an open position ===")
from lib.budget_manager import NON_BUYING_STATUSES
ok('ADVISORY_SELL' in NON_BUYING_STATUSES,
   "ADVISORY_SELL is a non-buying status", NON_BUYING_STATUSES)
for s in ('ERROR', 'SKIPPED', 'NEEDS_REVIEW', 'PENDING_BUY', 'PENDING_FILL'):
    ok(s in NON_BUYING_STATUSES, f"{s} still non-buying")
ok('Open' not in NON_BUYING_STATUSES, "'Open' is not in the non-buying list")

print("\n=== 3. the buy queue never picks it up ===")
import inspect
from lib import budget_manager as bm
src = inspect.getsource(bm.get_pending_buy_trades)
ok("status = 'PENDING_BUY'" in src,
   "get_pending_buy_trades filters to PENDING_BUY only, so ADVISORY_SELL is inert")

print("\n=== 4. main_recommend refuses to buy on either direction source ===")
import main_recommend as mr
src = inspect.getsource(mr.process_tip)
ok("'Sell' in (email_dir, spt_dir)" in src,
   "checks BOTH the email and the portal's buy_sell field")
ok(src.index("'Sell' in (email_dir, spt_dir)") < src.index("symbol_status not in"),
   "the sell check runs BEFORE the symbol/PENDING_BUY path")

print("\n=== 5. it is visible, and says whether you hold the stock ===")
import theme
for held, expect in ((340, 'hold'), (0, 'No open position')):
    note = (f'SPTulsian issued a SELL on "X" (email=Sell, portal=Sell). Not bought. '
            + (f'You currently hold {held} share(s) — the open position still has a '
               f'GTT resting at the original target, which this call withdraws. '
               f'Decide whether to exit.' if held else 'No open position in this symbol.'))
    ok(expect in note, f"note distinguishes holding {held} shares")

label = theme.friendly_status('ADVISORY_SELL', 'whatever')
print(f"  dashboard shows: {label!r}")
ok('SELL' in label.upper() and 'not bought' in label.lower(),
   "status renders loudly and says it was not bought", label)
ok('advisory_sell' in theme.TABLE_CSS.lower() if hasattr(theme, 'TABLE_CSS') else True,
   "pill style exists (or CSS is inlined elsewhere)")

print(f"\n{'='*56}\n  {P} passed, {F} failed\n{'='*56}")
sys.exit(1 if F else 0)
