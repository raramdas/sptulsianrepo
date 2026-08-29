"""End-to-end: a SELL tip through the real process_tip. Oracle write mocked."""
import sys
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
import main_recommend as mr

captured = []
mr.insert_trade_to_oracle = lambda tip, x: captured.append(dict(tip))
mr.resolve_kite_symbol = lambda name, enc: ('IDEA', 'MANUAL')
mr.get_stock_cap_type = lambda s: 'Mid Cap'
mr.holdings_qty_for = lambda s: 340

P = F = 0
def ok(c, l, e=''):
    global P, F
    if c: P += 1; print(f"  PASS  {l}")
    else: F += 1; print(f"  FAIL  {l}  {e}")

def run_tip(direction, spt_direction):
    captured.clear()
    mr.scrape_spt_stock = lambda n, c, log=None: {
        'type': '', 'target': 15.2, 'timeframe': '3 Months',
        'have_interest': 'Have Interest',
        'spt_market_price_at_call': 14.9, 'spt_below_reco': 0,
        'spt_direction': spt_direction, 'spt_rationale': '',
    }
    mr.process_tip({'stock': 'Vodafone Idea', 'email_price': 15.2,
                    'category': 'Big Gems', 'direction': direction}, 'tok')
    return captured[0] if captured else None

print("=== SELL in the email ===")
t = run_tip('Sell', 'Buy')
ok(t['buy_status'] == 'ADVISORY_SELL', "status is ADVISORY_SELL", t['buy_status'])
ok('340 share' in t['note'], "note says how much is held", t['note'][:90])

print("\n=== SELL only on the portal (email said Buy) ===")
t = run_tip('Buy', 'Sell')
ok(t['buy_status'] == 'ADVISORY_SELL', "portal alone is enough to block", t['buy_status'])

print("\n=== normal BUY is unaffected ===")
t = run_tip('Buy', 'Buy')
ok(t['buy_status'] == 'PENDING_BUY', "still queues normally", t['buy_status'])

print("\n=== direction missing entirely (HTML sections carry none) ===")
t = run_tip('', '')
ok(t['buy_status'] == 'PENDING_BUY',
   "blank direction does NOT block — blank is the norm, not a sell", t['buy_status'])

print("\n=== no holding: message changes, still blocked ===")
mr.holdings_qty_for = lambda s: 0
t = run_tip('Sell', '')
ok(t['buy_status'] == 'ADVISORY_SELL', "still blocked")
ok('No open position' in t['note'], "note says nothing to unwind", t['note'][-60:])

print(f"\n{'='*52}\n  {P} passed, {F} failed\n{'='*52}")
sys.exit(1 if F else 0)
