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
from lib.config import log
from lib.kite_client import get_enctoken, resolve_kite_symbol
from lib.email_reader import parse_todays_emails
from lib.spt_scraper import refresh_spt_data, scrape_spt_stock, quit_spt_driver
from lib.budget_manager import (get_stock_cap_type, insert_trade_to_oracle,
                                close_oracle_connection, open_qty_for_symbol)


def holdings_qty_for(symbol):
    """Shares of `symbol` the ledger currently shows as held. 0 when unknown.

    Used only to make a SELL alert actionable — "they said exit and you hold
    340 shares" is a different message from "they said exit and you own none".
    Never blocks anything, so a lookup failure degrades to the quieter wording
    rather than losing the alert.
    """
    if not symbol:
        return 0
    try:
        return open_qty_for_symbol(symbol) or 0
    except Exception as e:
        log(f"  (could not check holdings for {symbol}: {type(e).__name__})")
        return 0


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
    # Advisory context, stored with the trade. Not used by any gate — captured
    # because the portal only shows what is live, so a field not recorded on
    # the day of the call is gone.
    for k in ('spt_market_price_at_call', 'spt_below_reco',
              'spt_direction', 'spt_rationale'):
        tip[k] = spt.get(k)

    kite_symbol, symbol_status = resolve_kite_symbol(tip['stock'], enctoken)
    tip['kite_symbol'] = kite_symbol or ''

    # A Sell call must never become a buy. Direction comes from two places and
    # either one saying Sell is enough: the email ("Call added: X (Sell @ N)")
    # and SPTulsian's own buy_sell field on the call row. They are checked
    # together because the email pattern is the only source for sections whose
    # portal rows carry no direction, and the portal is the only source when an
    # email is missed.
    email_dir = (tip.get('direction') or '').strip().title()
    spt_dir = (tip.get('spt_direction') or '').strip().title()
    if 'Sell' in (email_dir, spt_dir):
        held = holdings_qty_for(tip['kite_symbol'])
        tip['buy_status'] = 'ADVISORY_SELL'
        tip['note'] = (
            f'SPTulsian issued a SELL on "{tip["stock"]}" '
            f'(email={email_dir or "n/a"}, portal={spt_dir or "n/a"}). Not bought. '
            + (f'You currently hold {held} share(s) — the open position still has a '
               f'GTT resting at the original target, which this call withdraws. '
               f'Decide whether to exit.'
               if held else 'No open position in this symbol.'))
        log(f"!! ADVISORY SELL: {tip['stock']} ({tip['kite_symbol'] or 'unresolved'}) — "
            f"NOT buying. Holding {held} share(s).")
        log(f"   {tip['note']}")
        insert_trade_to_oracle(tip, None)
        return

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

    # Refresh SPTulsian BEFORE the no-tips early return, so the liveness
    # watermark is written on every weekday run. If it only happened as a
    # side effect of a tip lookup, a market holiday (weekday, no tips) would
    # leave the watermark stale and spt_watchdog.py would cry wolf.
    refresh_spt_data(log=log)

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
