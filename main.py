#!/usr/bin/env python3
"""
main.py — Phase 2 of the buy-side flow: takes today's PENDING_BUY
recommendations (written 90 minutes earlier by main_recommend.py's Phase 1)
and any still-NEEDS_REVIEW tips from today (re-tried here in case
SYMBOL_MAP was fixed in the meantime), fetches live price, decides order
type/qty, checks budget, and places the real buy.

No email parsing here — that already happened in Phase 1. This is the
file the 11:00 AM cron job calls. Run directly:
    python3 main.py
"""
import math
from datetime import datetime

from config import log, DRY_RUN, IST, INVEST_AMT
from kite_client import get_enctoken, resolve_kite_symbol, get_market_price, kite_buy
from budget_manager import (
    get_stock_cap_type, check_budget_available, get_pending_buy_trades,
    get_needs_review_trades_for_retry, update_trade_after_buy_attempt, close_oracle_connection,
)
from sheet_logger import log_to_sheet


def attempt_buy(trade, enctoken):
    trade_id    = trade['trade_id']
    stock       = trade['stock_name']
    symbol      = trade['symbol']
    category    = trade['category_name']
    email_price = float(trade['recommended_price'])
    cap_type    = trade.get('stock_type') or get_stock_cap_type(symbol)

    log(f"Trade #{trade_id}: {stock} ({symbol})")

    mkt_price = get_market_price(stock, enctoken, kite_symbol=symbol)
    log(f"{stock} email:{email_price} market:{mkt_price}")
    if mkt_price and mkt_price < email_price:
        buy_price, order_type = mkt_price, 'MARKET'
    else:
        buy_price, order_type = email_price, 'LIMIT'

    qty = max(1, math.floor(INVEST_AMT / buy_price))
    actual_cost = qty * buy_price
    log(f"Qty: {qty} x {stock} @ {buy_price} ({order_type}) | actual cost: Rs.{actual_cost:,.2f}")
    if actual_cost > INVEST_AMT:
        log(f"  Note: actual cost Rs.{actual_cost:,.2f} exceeds target Rs.{INVEST_AMT:,.2f} "
            f"(price > Rs.{INVEST_AMT:,.2f}/share) — checking budget against actual cost")

    sheet_tip = {
        'category': category, 'stock': stock, 'kite_symbol': symbol,
        'cap_type': cap_type, 'email_price': email_price,
        'target': trade.get('target_price') or '', 'timeframe': trade.get('timeframe') or '',
        'have_interest': trade.get('have_interest') or '',
        'mkt_price': mkt_price, 'buy_price': buy_price, 'order_type': order_type, 'qty': qty,
    }

    budget_ok, category_id = check_budget_available(category, cap_type, actual_cost, symbol=symbol)
    if not budget_ok:
        log(f"SKIPPING {stock} — insufficient budget for actual cost Rs.{actual_cost:,.2f}")
        sheet_tip['note'] = 'Insufficient category/stock-type budget'
        log_to_sheet(sheet_tip)
        update_trade_after_buy_attempt(trade_id, 'SKIPPED', category_id=category_id, symbol=symbol,
                                       stock_type=cap_type, notes='Insufficient category/stock-type budget')
        return

    if DRY_RUN:
        buy_order_id = 'DRY_RUN'
        sheet_tip['note'] = 'DRY RUN'
        log(f"[DRY RUN] Would BUY {qty} x {stock} @ {buy_price}")
    else:
        buy = kite_buy(sheet_tip, enctoken)
        buy_order_id = buy['order_id']
        sheet_tip['note'] = ''
        log(f"Buy placed: {buy_order_id}")

    sheet_tip['buy_order_id'] = buy_order_id
    log_to_sheet(sheet_tip)
    update_trade_after_buy_attempt(
        trade_id, 'Open', category_id=category_id, symbol=symbol, stock_type=cap_type,
        order_type=order_type, buy_order_id=buy_order_id, market_price_at_buy=mkt_price,
        my_buy_price=buy_price, my_buy_qty=qty, invested_amount=actual_cost, notes=None,
    )


def run():
    log("=== Stock Tip Bot — Buy Phase starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    today = datetime.now(IST).strftime('%Y-%m-%d')

    pending = get_pending_buy_trades([today])
    log(f"Found {len(pending)} PENDING_BUY trade(s) from today's recommendations")

    retry_candidates = get_needs_review_trades_for_retry([today])
    log(f"Re-checking {len(retry_candidates)} NEEDS_REVIEW trade(s) from today")
    to_buy = list(pending)
    for t in retry_candidates:
        kite_symbol, status = resolve_kite_symbol(t['stock_name'], enctoken)
        if status in ('MANUAL', 'EXACT'):
            log(f"  {t['stock_name']} now resolves to {kite_symbol} — retrying buy")
            t['symbol'] = kite_symbol
            t['stock_type'] = None
            to_buy.append(t)
        else:
            log(f"  {t['stock_name']} still {status} — leaving as NEEDS_REVIEW")

    for trade in to_buy:
        try:
            attempt_buy(trade, enctoken)
        except Exception as e:
            log(f"ERROR {trade['stock_name']}: {e}")
            update_trade_after_buy_attempt(trade['trade_id'], 'ERROR', notes=str(e))

    close_oracle_connection()
    log("=== Buy Phase complete ===")


if __name__ == '__main__':
    run()
