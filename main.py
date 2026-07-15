#!/usr/bin/env python3
"""
main.py — orchestrates the full buy-side flow:
  1. Login to Kite
  2. Parse today's SPTulsian emails
  3. For each tip: resolve symbol, classify cap type, check budget,
     fetch market price, decide order type/qty, place order (or DRY_RUN),
     log to Google Sheet + Oracle TRADES table

This is the file the cron job calls. Run it directly:
    python3 main.py
"""
import math

from config import log, DRY_RUN, TEST_DATE, INVEST_AMT
from kite_client import get_enctoken, resolve_kite_symbol, get_market_price, kite_buy
from email_reader import parse_todays_emails
from spt_scraper import scrape_spt_stock, quit_spt_driver
from budget_manager import get_stock_cap_type, check_budget_available, insert_trade_to_oracle, close_oracle_connection
from sheet_logger import log_to_sheet


def process_tip(tip, enctoken):
    # Scrape Type, Target, Timeframe, Have Interest from SPTulsian (currently disabled — returns blanks)
    spt = scrape_spt_stock(tip['stock'], tip.get('category', ''))
    tip['type']          = spt['type']
    tip['target']        = spt['target']
    tip['timeframe']     = spt['timeframe']
    tip['have_interest'] = spt['have_interest']

    kite_symbol, symbol_status = resolve_kite_symbol(tip['stock'], enctoken)
    tip['kite_symbol'] = kite_symbol or ''

    if symbol_status not in ('MANUAL', 'EXACT'):
        # FUZZY or NOT_FOUND — this is exactly the class of guess that
        # previously bought the wrong stock (CG Power, Apollo Micro Systems).
        # Never buy on it; flag for human review instead.
        tip['buy_status'] = 'NEEDS_REVIEW'
        tip['note'] = (f'Symbol resolution status={symbol_status} for "{tip["stock"]}" — '
                        f'add correct mapping to SYMBOL_MAP in kite_client.py, then re-run')
        log(f"SKIPPING {tip['stock']} — symbol resolution not trusted ({symbol_status}), "
            f"suggested match: {kite_symbol or 'none'}")
        log_to_sheet(tip)
        insert_trade_to_oracle(tip, None)
        return

    # Determine cap type from AMFI classification, independent of SPT scraping
    tip['cap_type'] = get_stock_cap_type(tip['kite_symbol'])
    log(f"  Cap type for {tip['stock']} ({tip['kite_symbol']}): {tip['cap_type'] or 'UNKNOWN'}")

    # Determine market price, order type, qty and ACTUAL cost BEFORE checking budget,
    # since a stock priced above INVEST_AMT still buys a minimum of 1 share (actual
    # cost = price x 1, which can exceed the flat Rs.5000 target).
    tip['mkt_price'] = get_market_price(tip['stock'], enctoken, kite_symbol=tip['kite_symbol'])
    log(f"{tip['stock']} email:{tip['email_price']} market:{tip['mkt_price']}")
    if tip['mkt_price'] and tip['mkt_price'] < tip['email_price']:
        tip['buy_price']  = tip['mkt_price']
        tip['order_type'] = 'MARKET'
    else:
        tip['buy_price']  = tip['email_price']
        tip['order_type'] = 'LIMIT'
    tip['qty'] = max(1, math.floor(INVEST_AMT / tip['buy_price']))
    actual_cost = tip['qty'] * tip['buy_price']
    log(f"Qty: {tip['qty']} x {tip['stock']} @ {tip['buy_price']} ({tip['order_type']}) "
        f"| actual cost: Rs.{actual_cost:,.2f}")
    if actual_cost > INVEST_AMT:
        log(f"  Note: actual cost Rs.{actual_cost:,.2f} exceeds target Rs.{INVEST_AMT:,.2f} "
            f"(price > Rs.{INVEST_AMT:,.2f}/share) — checking budget against actual cost")

    # Budget check — category cap + stock-type cap within category, using the
    # ACTUAL cost of this trade rather than the flat INVEST_AMT.
    budget_ok, category_id = check_budget_available(tip.get('category', ''), tip['cap_type'], actual_cost)
    if not budget_ok:
        tip['buy_status'] = 'SKIPPED'
        tip['note']       = 'Insufficient category/stock-type budget'
        log(f"SKIPPING {tip['stock']} — insufficient budget for actual cost Rs.{actual_cost:,.2f}")
        log_to_sheet(tip)
        insert_trade_to_oracle(tip, category_id)
        return

    if DRY_RUN:
        tip['buy_order_id'] = 'DRY_RUN'
        tip['buy_status']   = 'DRY_RUN'
        tip['note']         = 'DRY RUN'
        log(f"[DRY RUN] Would BUY {tip['qty']} x {tip['stock']} @ {tip['buy_price']}")
    else:
        buy = kite_buy(tip, enctoken)
        tip['buy_order_id'] = buy['order_id']
        tip['buy_status']   = 'PLACED'
        log(f"Buy placed: {tip['buy_order_id']}")

    log_to_sheet(tip)
    insert_trade_to_oracle(tip, category_id)


def run():
    log("=== Stock Tip Bot starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    if TEST_DATE:
        log(f"TEST DATE override: {TEST_DATE}")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    tips = parse_todays_emails()
    if not tips:
        log("No tips found today.")
        return
    log(f"Tips found: {[t['stock'] for t in tips]}")

    for tip in tips:
        try:
            process_tip(tip, enctoken)
        except Exception as e:
            log(f"ERROR {tip['stock']}: {e}")
            tip['buy_status'] = 'ERROR'
            tip['note'] = str(e)
            log_to_sheet(tip)

    quit_spt_driver()
    close_oracle_connection()
    log("=== Automation complete ===")


if __name__ == '__main__':
    run()
