#!/usr/bin/env python3
"""
main_recommend.py — Phase 1 of the buy-side flow: parse today's SPTulsian
emails and resolve symbols, WITHOUT placing any orders. Cleanly-resolved
tips are recorded as PENDING_BUY; symbol failures are recorded as
NEEDS_REVIEW, exactly as before — just 90 minutes earlier than the actual
buy, so there's a review window to fix a SYMBOL_MAP mapping (or use the
dashboard's Needs Review manual buy) before real money moves.

Phase 2 (the actual buying) is main.py, scheduled 90 minutes after this.

This is the file the 9:30 AM cron job calls. Run directly:
    python3 main_recommend.py
"""
from config import log
from kite_client import get_enctoken, resolve_kite_symbol
from email_reader import parse_todays_emails
from spt_scraper import scrape_spt_stock, quit_spt_driver
from budget_manager import get_stock_cap_type, insert_trade_to_oracle, close_oracle_connection


def process_tip(tip, enctoken):
    # Target/Timeframe/Have-Interest from SPTulsian, via the WARP proxy. Logs
    # in and scrapes once per run, then serves each tip from that cache.
    # Returns blanks if the scrape fails or only a closed call matches — a
    # missing target never blocks recording the recommendation, and
    # spt_watchdog.py alerts separately if the scraper has gone dark.
    spt = scrape_spt_stock(tip['stock'], tip.get('category', ''), log=log)
    tip['type']          = spt['type']
    tip['target']        = spt['target']
    tip['timeframe']     = spt['timeframe']
    tip['have_interest'] = spt['have_interest']

    kite_symbol, symbol_status = resolve_kite_symbol(tip['stock'], enctoken)
    tip['kite_symbol'] = kite_symbol or ''

    if symbol_status not in ('MANUAL', 'EXACT'):
        # Same rule as always: never buy on a guess. Flag for review — now
        # with a window before the buy run instead of discovering it at buy
        # time with no time left to fix it.
        tip['buy_status'] = 'NEEDS_REVIEW'
        tip['note'] = (f'Symbol resolution status={symbol_status} for "{tip["stock"]}" — '
                        f'add correct mapping to SYMBOL_MAP in kite_client.py, then re-run')
        log(f"NEEDS REVIEW: {tip['stock']} — {symbol_status}, suggested match: {kite_symbol or 'none'}")
        insert_trade_to_oracle(tip, None)
        return

    tip['cap_type'] = get_stock_cap_type(tip['kite_symbol'])
    tip['buy_status'] = 'PENDING_BUY'
    tip['note'] = None
    log(f"Recommended: {tip['stock']} ({tip['kite_symbol']}) — cap_type={tip['cap_type'] or 'UNKNOWN'}")
    insert_trade_to_oracle(tip, None)


def run():
    log("=== Recommendation Bot (Phase 1) starting ===")

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

    quit_spt_driver()
    close_oracle_connection()
    log("=== Recommendation Bot (Phase 1) complete ===")


if __name__ == '__main__':
    run()
