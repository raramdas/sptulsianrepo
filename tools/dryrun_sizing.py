"""Dry run of the recalibrated sizing over today's four recommendations.

Mirrors main.attempt_buy step for step using the REAL decide_position_size,
the REAL market price, and the REAL budget check — but every write and order
path is replaced with a tripwire that raises, so a mistake here fails loudly
instead of spending money.
"""
import sys, math, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, "/home/ubuntu/stock_bot_v4")
sys.path.insert(0, "/home/ubuntu/stockbot/dashboard")

import main
import db


class Tripwire(Exception):
    pass


def _forbidden(name):
    def _f(*a, **k):
        raise Tripwire(f"{name} was called during a dry run")
    return _f


# Tripwires: nothing below may write to Oracle, the sheet, or the broker.
for fn in ('update_trade_after_buy_attempt', 'requeue_for_retry',
           'record_buy_attempt', 'log_to_sheet', 'place_order',
           'mark_trade_error_oracle'):
    if hasattr(main, fn):
        setattr(main, fn, _forbidden(fn))

trades = db._df("""
    SELECT t.trade_id, t.category_name, t.stock_name, t.symbol, t.stock_type,
           t.recommended_price, t.target_price, t.timeframe, t.have_interest,
           t.buy_attempts, t.status
    FROM trades t
    WHERE t.buy_date >= TRUNC(SYSDATE)
    ORDER BY t.trade_id
""")

print(f"=== policy in force ===")
print(f"  enabled={main.CONVICTION_SIZING_ENABLED}  bands={main.CONVICTION_SIZING}  "
      f"floor={main.CONVICTION_MIN_SCORE}  have_interest={main.REQUIRE_HAVE_INTEREST}")
print(f"\n=== dry run over {len(trades)} trade(s) recommended today ===")
print("(real sizing, real prices, real budget check — all writes tripwired)\n")

try:
    enctoken = main.get_enctoken() if hasattr(main, 'get_enctoken') else None
except Exception as e:
    enctoken = None
    print(f"  (no broker token: {type(e).__name__} — market price will fall back)\n")

total = 0.0
for _, t in trades.iterrows():
    trade = t.to_dict()
    tid, stock, symbol = trade['trade_id'], trade['stock_name'], trade['symbol']
    print(f"--- #{int(tid)} {stock} ({symbol})   [ledger status now: {trade['status']}] ---")

    conv = main.get_latest_conviction(tid)
    score = conv.get('score') if conv else None
    print(f"    conviction : {score}  ({conv.get('verdict') if conv else 'none'})")
    print(f"    have_interest: {trade.get('have_interest')!r}")

    invest_amt, skip_reason, retryable = main.decide_position_size(trade)

    if skip_reason:
        attempts = int(trade.get('buy_attempts') or 0)
        if retryable and main.RETRY_ON_UNKNOWN and attempts < main.BUY_RETRY_DAYS:
            print(f"    -> WOULD HOLD for retry ({attempts + 1} of {main.BUY_RETRY_DAYS})")
            print(f"       {skip_reason}")
        else:
            print(f"    -> WOULD SKIP: {skip_reason}")
        print()
        continue

    print(f"    position   : Rs {invest_amt:,}")

    email_price = float(trade['recommended_price'])
    try:
        mkt = main.get_market_price(stock, enctoken, kite_symbol=symbol)
    except Exception as e:
        mkt = None
        print(f"    (price lookup failed: {type(e).__name__})")

    if mkt and mkt < email_price:
        buy_price, order_type = mkt, 'MARKET'
    else:
        buy_price, order_type = email_price, 'LIMIT'

    qty = max(1, math.floor(invest_amt / buy_price))
    cost = qty * buy_price
    print(f"    price      : email {email_price}  market {mkt}  -> {order_type} @ {buy_price}")
    print(f"    order      : {qty} x {stock} = Rs {cost:,.2f}")
    if cost > invest_amt:
        print(f"    note       : cost exceeds target Rs {invest_amt:,} (share price > target)")

    cap_type = trade.get('stock_type') or main.get_stock_cap_type(symbol)
    budget_ok, cat_id = main.check_budget_available(
        trade['category_name'], cap_type, cost, symbol=symbol)
    if not budget_ok:
        print(f"    -> WOULD SKIP: insufficient budget for Rs {cost:,.2f}")
        print()
        continue

    print(f"    budget     : PASSED ({cap_type}, category {trade['category_name']})")
    print(f"    -> WOULD BUY {qty} x {stock} @ {buy_price} = Rs {cost:,.2f}")
    total += cost
    print()

print(f"=== total that would be deployed: Rs {total:,.2f} ===")
print(f"    (flat Rs 5,000 policy on the same set: "
      f"Rs {5000 * len(trades):,} across {len(trades)})")
print("\nNo orders placed, no ledger rows touched.")
