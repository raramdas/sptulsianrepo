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

from lib.config import (log, DRY_RUN, IST, INVEST_AMT, CONVICTION_SIZING,
                        CONVICTION_SIZING_ENABLED, CONVICTION_MIN_SCORE,
                        REQUIRE_HAVE_INTEREST, BUY_RETRY_DAYS, RETRY_ON_UNKNOWN)
from lib.kite_client import get_enctoken, resolve_kite_symbol, get_market_price, kite_buy
from lib.order_status import get_order_status, get_holding_qty
from lib.budget_manager import (
    get_stock_cap_type, check_budget_available, get_pending_buy_trades,
    get_needs_review_trades_for_retry, update_trade_after_buy_attempt, close_oracle_connection,
    get_latest_conviction, get_pending_fill_trades, requeue_for_retry, record_buy_attempt,
    open_qty_for_symbol,
)
from lib.sheet_logger import log_to_sheet


def decide_position_size(trade):
    """Apply the buy gates. Returns (invest_amt, reason, retryable).

    Conviction sizing is ON (lib/bands.py): >85 -> Rs 25,000, 63-85 ->
    Rs 10,000, below 63 not bought. Set CONVICTION_SIZING_ENABLED False and
    every accepted buy reverts to the flat INVEST_AMT. The SPTulsian "Have
    Interest" gate is independent of both — that is the advisory's own
    disclosure, not our score.

    `retryable` is True when the skip reflects OUR pipeline having no data
    rather than a judgement on the call — a blank have_interest (the scrape
    found no live call to match) or a trade the conviction run never scored.
    Those are retried; 'No Interest' and a genuine low score are not.

    Two gates, both hard:

      1. SPTulsian must disclose "Have Interest" in the stock. A blank is
         NOT treated as consent — it usually means the scrape found no live
         call to match, i.e. we do not know. Skipping on unknown is the
         conservative reading, but note that a scraper outage therefore stops
         buying for the day; spt_watchdog.py is what surfaces that.

      2. Conviction sizing, ONLY when CONVICTION_SIZING_ENABLED is set. It is
         currently off, so this gate does not run and the score cannot skip a
         trade. When on: the score sets the band, anything under the floor is
         not bought, and a missing score is "unknown" rather than zero — it is
         skipped and flagged rather than silently sized at the bottom band.
    """
    if REQUIRE_HAVE_INTEREST:
        hi = (trade.get('have_interest') or '').strip()
        if hi != 'Have Interest':
            if not hi:
                # Unknown, not refused: the scrape found no live call to match.
                return None, ('SPTulsian interest unknown — no matching live call '
                              'found by the scrape'), True
            return None, f'SPTulsian discloses No Interest in this stock', False

    if not CONVICTION_SIZING_ENABLED:
        # Flat sizing. The score is still computed and displayed, but the
        # backtest found nothing to justify letting it decide how much — or
        # whether — to buy. See lib/config.CONVICTION_SIZING_ENABLED.
        return INVEST_AMT, None, False

    conv = get_latest_conviction(trade['trade_id'])
    if conv is None:
        # The scoring job did not run or did not reach this trade — unknown,
        # not weak. Retryable.
        return None, ('No conviction score on file — main_conviction.py did not '
                      'score this trade, so there is no basis to size it'), True
    score = conv.get('score')
    if score is None:
        # Engine deliberately withheld a composite for lack of evidence. That
        # IS a judgement, and re-running tomorrow will not conjure evidence.
        return None, (f"Conviction withheld ({conv.get('verdict')}, evidence "
                      f"{conv.get('evidence_pct')}/100) — too little evidence to size"), False
    if score < CONVICTION_MIN_SCORE:
        return None, (f'Conviction {score:.1f} is below the '
                      f'{CONVICTION_MIN_SCORE}-point floor'), False

    for floor, amount in CONVICTION_SIZING:
        if score > floor:
            return amount, None, False
    # Score is at or above the floor but not above any band's threshold, i.e.
    # exactly CONVICTION_MIN_SCORE — take the lowest band.
    return CONVICTION_SIZING[-1][1], None, False


def attempt_buy(trade, enctoken):
    trade_id    = trade['trade_id']
    stock       = trade['stock_name']
    symbol      = trade['symbol']
    category    = trade['category_name']
    email_price = float(trade['recommended_price'])
    cap_type    = trade.get('stock_type') or get_stock_cap_type(symbol)

    attempts = int(trade.get('buy_attempts') or 0)
    log(f"Trade #{trade_id}: {stock} ({symbol})" + (f" [attempt {attempts + 1}]" if attempts else ""))

    invest_amt, skip_reason, retryable = decide_position_size(trade)
    if skip_reason:
        # An outage is not a verdict. When the pipeline simply had no data,
        # leave the trade queued so a later run can buy it once the data
        # arrives — rather than burning the opportunity on our own downtime.
        if retryable and RETRY_ON_UNKNOWN and attempts < BUY_RETRY_DAYS:
            note = f'{skip_reason} — will retry ({attempts + 1} of {BUY_RETRY_DAYS})'
            log(f"HOLDING {stock} — {note}")
            requeue_for_retry(trade_id, attempts + 1, note)
            return
        if retryable:
            skip_reason = (f'{skip_reason} — no retries left after '
                           f'{attempts} attempt(s)')
        log(f"SKIPPING {stock} — {skip_reason}")
        update_trade_after_buy_attempt(trade_id, 'SKIPPED', symbol=symbol,
                                       stock_type=cap_type, notes=skip_reason)
        return
    # Name the actual source. Saying "by conviction" while CONVICTION_SIZING_ENABLED
    # is False puts a claim in the audit trail that invariant #4 forbids, and
    # anyone reconstructing why a position was sized this way would believe it.
    sizing_src = "by conviction" if CONVICTION_SIZING_ENABLED else "flat"
    log(f"  Position size Rs.{invest_amt:,} ({sizing_src})")

    mkt_price = get_market_price(stock, enctoken, kite_symbol=symbol)
    log(f"{stock} email:{email_price} market:{mkt_price}")
    if mkt_price and mkt_price < email_price:
        buy_price, order_type = mkt_price, 'MARKET'
    else:
        buy_price, order_type = email_price, 'LIMIT'

    qty = max(1, math.floor(invest_amt / buy_price))
    actual_cost = qty * buy_price
    log(f"Qty: {qty} x {stock} @ {buy_price} ({order_type}) | actual cost: Rs.{actual_cost:,.2f}")
    if actual_cost > invest_amt:
        log(f"  Note: actual cost Rs.{actual_cost:,.2f} exceeds target Rs.{invest_amt:,.2f} "
            f"(price > Rs.{invest_amt:,.2f}/share) — checking budget against actual cost")

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
    record_buy_attempt(trade_id, attempts + 1)

    # Confirm the order actually filled before calling it a position.
    #
    # A LIMIT at the recommended price does not fill when the market is above
    # it, and a DAY order then expires at the close. Recording 'Open' with the
    # full invested_amount at placement time — as this did — booked positions
    # that were never bought: budget consumed, P&L overstated, and the GTT job
    # skipping them forever with "limit order still open". Unfilled orders now
    # sit in PENDING_FILL and are reconciled on a later run.
    if not DRY_RUN:
        filled = get_order_status(buy_order_id, enctoken, symbol_hint=symbol)
        if not (filled and filled['status'] == 'COMPLETE' and filled['filled_qty'] > 0):
            state = (filled or {}).get('status', 'UNKNOWN')
            log(f"  Not filled yet (order status {state}) — holding as PENDING_FILL")
            update_trade_after_buy_attempt(
                trade_id, 'PENDING_FILL', category_id=category_id, symbol=symbol,
                stock_type=cap_type, order_type=order_type, buy_order_id=buy_order_id,
                market_price_at_buy=mkt_price, invested_amount=0,
                notes=f'Order placed at {buy_price}, awaiting fill (attempt {attempts + 1})')
            return
        # Use what actually filled, not what we asked for.
        qty = filled['filled_qty']
        buy_price = filled.get('avg_price') or buy_price
        actual_cost = qty * buy_price
        log(f"  Filled: {qty} @ {buy_price} (Rs.{actual_cost:,.2f})")

    update_trade_after_buy_attempt(
        trade_id, 'Open', category_id=category_id, symbol=symbol, stock_type=cap_type,
        order_type=order_type, buy_order_id=buy_order_id, market_price_at_buy=mkt_price,
        my_buy_price=buy_price, my_buy_qty=qty, invested_amount=actual_cost, notes=None,
    )


def reconcile_pending_fills(enctoken):
    """Resolve buys placed on an earlier run whose fill was never confirmed.

    Three outcomes per trade:
      filled            -> Open, with the ACTUAL filled qty and average price
      unfilled, budget  -> back to PENDING_BUY so this run re-prices and
        of retries left     re-places it (the DAY order has already expired at
                            the exchange, so there is nothing to cancel)
      retries exhausted -> SKIPPED, recorded as a genuine miss

    This is what makes "retry for the next 2 days" real: a call whose price
    never came back today gets another chance tomorrow instead of being lost.
    """
    pending = get_pending_fill_trades()
    if not pending:
        return
    log(f"Reconciling {len(pending)} order(s) awaiting fill...")

    for t in pending:
        trade_id = t['trade_id']
        stock, symbol = t['stock_name'], t['symbol']
        attempts = int(t.get('buy_attempts') or 0)
        # How many held shares are not already spoken for by other open lots.
        # Holdings are per symbol, orders are per lot, so this is what lets
        # the holdings fallback tell this order's fill from someone else's.
        # None means we could not find out, and the fallback then declines to
        # infer anything rather than assuming the whole holding is ours.
        held_by_others = open_qty_for_symbol(symbol, exclude_trade_id=trade_id)
        from_kite = get_holding_qty(symbol, enctoken) if held_by_others is not None else None
        unexplained = (None if (held_by_others is None or from_kite is None)
                       else max(0, from_kite - held_by_others))

        info = get_order_status(t['buy_order_id'], enctoken, symbol_hint=symbol,
                                expected_qty=t.get('my_buy_qty'),
                                unexplained_qty=unexplained)
        status = (info or {}).get('status', 'UNKNOWN')
        filled_qty = (info or {}).get('filled_qty') or 0

        if info and status == 'COMPLETE' and filled_qty > 0:
            price = info.get('avg_price') or float(t['recommended_price'])
            cost = filled_qty * price
            log(f"  #{trade_id} {stock}: FILLED {filled_qty} @ {price} — marking Open")
            update_trade_after_buy_attempt(
                trade_id, 'Open', symbol=symbol, my_buy_price=price,
                my_buy_qty=filled_qty, invested_amount=cost,
                notes=f'Filled on attempt {attempts}')
            continue

        if attempts <= BUY_RETRY_DAYS:
            note = (f'Not filled at the recommended price (order {status}); '
                    f'retry {attempts} of {BUY_RETRY_DAYS}')
            log(f"  #{trade_id} {stock}: not filled ({status}) — requeueing, {note}")
            requeue_for_retry(trade_id, attempts, note)
        else:
            note = (f'Never filled at the recommended price after '
                    f'{attempts} attempt(s) over {BUY_RETRY_DAYS} retry day(s)')
            log(f"  #{trade_id} {stock}: giving up — {note}")
            update_trade_after_buy_attempt(trade_id, 'SKIPPED', symbol=symbol,
                                           invested_amount=0, notes=note)


def _log_gate_summary(considered):
    """Separate skips caused by the CALL from skips caused by US.

    Both gates now depend on our own pipeline: a failed scrape leaves
    have_interest blank, and a failed conviction run leaves no score. Either
    silently stops buying, and the trade log alone reads the same as a day
    when nothing was worth buying. Over a recent 10-day window, 8 of 29
    recommendations were skipped for infrastructure reasons rather than
    merit — including the two highest-scoring calls in the window. Surface
    that difference so a quiet day is distinguishable from a broken one.
    """
    infra = merit = 0
    for t in considered:
        _, reason, retryable = decide_position_size(t)
        if not reason:
            continue
        if retryable:
            infra += 1
        else:
            merit += 1
    if merit:
        log(f"Skipped {merit} recommendation(s) on their merits (no interest / low conviction)")
    if infra:
        log("")
        log(f"  *** {infra} recommendation(s) were HELD because OUR PIPELINE had no data,")
        log(f"      not because the call was judged weak. They stay queued and are")
        log(f"      retried for up to {BUY_RETRY_DAYS} more day(s). Fix the cause today:")
        log(f"        python3 spt_watchdog.py --check-only")
        log(f"        tail -40 /home/ubuntu/conviction.log")


def run():
    log("=== Stock Tip Bot — Buy Phase starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    today = datetime.now(IST).strftime('%Y-%m-%d')

    # First settle earlier orders, so anything that missed its price gets
    # requeued into PENDING_BUY and is picked up by this same run.
    reconcile_pending_fills(enctoken)

    pending = get_pending_buy_trades(retry_days=BUY_RETRY_DAYS)
    log(f"Found {len(pending)} PENDING_BUY trade(s) (today's recommendations + retries)")

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

    _log_gate_summary(to_buy)
    close_oracle_connection()
    log("=== Buy Phase complete ===")


if __name__ == '__main__':
    run()
