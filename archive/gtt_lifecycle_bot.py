#!/usr/bin/env python3
# gtt_lifecycle_bot.py  (Bot C)
# Run once daily after market close (e.g. 3:45 PM IST). By then, any DAY
# order placed that morning (buy or GTT-triggered sell) has already reached
# its final state, so there's no need to wait for "next morning" separately.
#
# Phase 1: for Open rows with a completed buy and no GTT yet, place a GTT
#          sell trigger at (target - GTT_OFFSET).
# Phase 2: for rows with an existing GTT, check the ACTUAL resulting sell
#          order's fill status — not just the GTT's own status. A GTT can
#          show "triggered" even when the DAY-validity sell order it placed
#          never filled and got cancelled at EOD. If that happened, this
#          recreates the GTT at the same target price rather than leaving
#          it for you to redo by hand.

from datetime import datetime, timedelta

from archive.kite_common import (
    log, get_sheet, get_enctoken, get_order_status, get_ltp,
    place_gtt, get_gtt_detail, find_sell_order_for_symbol, clean_float, pad_row,
    IST,
    COL_STOCK, COL_SYMBOL, COL_BUY_DATE, COL_TARGET, COL_STATUS, COL_TARGET_MET,
    COL_EXIT_DATE, COL_GAIN, COL_BUY_OID, COL_MY_BUY_QTY, COL_MY_BUY_PX,
    COL_SELL_DATE, COL_SELL_PRICE, COL_SELL_QTY, COL_GAIN_LOSS,
    COL_GTT_ID, COL_GTT_STATUS, COL_NOTES, COL_RETRY_CNT,
)

DRY_RUN     = True    # Set to False for live
GTT_OFFSET  = 0.10    # Trigger fires Rs 0.10 below target, per your spec


def run():
    log("=== GTT Lifecycle Bot starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")

    enctoken = get_enctoken()
    log("Kite enctoken obtained OK.")

    ws   = get_sheet()
    rows = ws.get_all_values()

    today     = datetime.now(IST).strftime('%Y-%m-%d')
    yesterday = (datetime.now(IST) - timedelta(days=1)).strftime('%Y-%m-%d')

    placed, skipped, closed, recreated = 0, 0, 0, 0

    for i, row in enumerate(rows[1:], start=2):
        row = pad_row(row)

        stock      = row[COL_STOCK].strip()
        sheet_symbol = row[COL_SYMBOL].strip()
        buy_date   = row[COL_BUY_DATE].strip()
        status     = row[COL_STATUS].strip()
        target     = clean_float(row[COL_TARGET])
        buy_oid    = row[COL_BUY_OID].strip()
        gtt_id     = row[COL_GTT_ID].strip()
        my_buy_qty = row[COL_MY_BUY_QTY].strip()
        my_buy_px  = clean_float(row[COL_MY_BUY_PX])
        retry_cnt  = clean_float(row[COL_RETRY_CNT]) or 0

        if status != 'Open':
            continue

        # ── Phase 1: place GTT for a buy that has now filled ─────────────
        if (target and target > 0
                and buy_oid and buy_oid not in ('', 'DRY_RUN')
                and not gtt_id):

            log(f"Row {i}: {stock} | buy_oid={buy_oid} | target={target}")
            order_info = get_order_status(buy_oid, enctoken)
            if not order_info:
                log("  Could not fetch order status — skipping")
                skipped += 1
                continue

            kite_status = order_info['status']
            filled_qty  = order_info['filled_qty']
            symbol      = order_info['symbol'] or sheet_symbol or stock.upper().replace(' ', '')
            log(f"  Kite status: {kite_status} | filled_qty: {filled_qty}")

            if kite_status == 'COMPLETE' and filled_qty > 0:
                trigger_price = round(target - GTT_OFFSET, 2)
                ltp = get_ltp(symbol, enctoken) or target
                if DRY_RUN:
                    log(f"  [DRY RUN] Would place GTT SELL {filled_qty} x {symbol} @ {trigger_price}")
                    ws.update_cell(i, COL_GTT_ID + 1,     'DRY_RUN')
                    ws.update_cell(i, COL_GTT_STATUS + 1, 'DRY_RUN')
                else:
                    trig_id = place_gtt(symbol, filled_qty, trigger_price, ltp, enctoken)
                    log(f"  GTT placed: {trig_id} @ trigger {trigger_price}")
                    ws.update_cell(i, COL_GTT_ID + 1,     trig_id)
                    ws.update_cell(i, COL_GTT_STATUS + 1, 'PLACED')
                placed += 1

            elif kite_status in ('OPEN', 'TRIGGER PENDING', 'AMO REQ RECEIVED'):
                log("  Limit order still open — skipping GTT")
                skipped += 1

            elif kite_status in ('REJECTED', 'CANCELLED'):
                log(f"  Order {kite_status} — marking ERROR")
                if not DRY_RUN:
                    ws.update_cell(i, COL_STATUS + 1, 'ERROR')
                    ws.update_cell(i, COL_NOTES + 1,  f'Buy order {kite_status}')
                skipped += 1
            else:
                log(f"  Unknown status {kite_status} — skipping")
                skipped += 1

        # ── Phase 2: check existing GTT — verify ACTUAL fill, not just
        #             the GTT's own status ────────────────────────────────
        elif gtt_id and gtt_id not in ('', 'DRY_RUN'):
            detail = get_gtt_detail(gtt_id, enctoken)
            if not detail:
                log(f"Row {i}: {stock} | GTT {gtt_id} — could not fetch detail, skipping")
                skipped += 1
                continue

            gtt_status = detail.get('status', '').upper()
            log(f"Row {i}: {stock} | GTT {gtt_id} status: {gtt_status}")

            if gtt_status == 'ACTIVE':
                continue  # still waiting, nothing to do

            # GTT left the active state (triggered / deleted / expired) —
            # figure out whether a sell actually completed before touching
            # the sheet.
            symbol = None
            cond = detail.get('condition', {})
            symbol = cond.get('tradingsymbol') or sheet_symbol or stock.upper().replace(' ', '')
            qty = int(float(my_buy_qty)) if my_buy_qty else None

            filled_order = None
            for o in detail.get('orders', []):
                result = o.get('result')
                if result and result.get('order_id'):
                    info = get_order_status(result['order_id'], enctoken)
                    if info:
                        filled_order = info
                    break

            if not filled_order and qty:
                # Fallback: GTT result didn't carry an order_id — scan the
                # day's order book for the matching SELL order instead of
                # assuming it filled just because the GTT says "triggered".
                fo = find_sell_order_for_symbol(symbol, qty, enctoken)
                if fo:
                    filled_order = {
                        'status': fo.get('status', '').upper(),
                        'filled_qty': int(fo.get('filled_quantity', 0)),
                        'avg_price': float(fo.get('average_price', 0) or 0),
                    }

            sold_ok = bool(filled_order and filled_order['status'] == 'COMPLETE'
                            and filled_order['filled_qty'] > 0)

            if sold_ok:
                sell_price = filled_order['avg_price'] or target
                sell_qty   = filled_order['filled_qty']
                gain_loss  = (sell_price - my_buy_px) * sell_qty if my_buy_px else ''
                if DRY_RUN:
                    log(f"  [DRY RUN] Would mark {stock} Closed — sold {sell_qty} @ {sell_price}")
                else:
                    ws.update_cell(i, COL_STATUS + 1,      'Closed')
                    ws.update_cell(i, COL_TARGET_MET + 1,  'Yes')
                    ws.update_cell(i, COL_EXIT_DATE + 1,   today)
                    ws.update_cell(i, COL_SELL_DATE + 1,   today)
                    ws.update_cell(i, COL_SELL_PRICE + 1,  sell_price)
                    ws.update_cell(i, COL_SELL_QTY + 1,    sell_qty)
                    ws.update_cell(i, COL_GAIN_LOSS + 1,   gain_loss)
                    ws.update_cell(i, COL_GTT_STATUS + 1,  'TRIGGERED')
                closed += 1
                log(f"  {stock} marked Closed — GTT triggered AND sell confirmed filled")
            else:
                # GTT triggered (or expired/deleted) but the sell never
                # actually completed — recreate at the SAME target price,
                # per your instruction to retry indefinitely.
                trigger_price = round(target - GTT_OFFSET, 2)
                ltp = get_ltp(symbol, enctoken) or target
                new_retry = int(retry_cnt) + 1
                log(f"  GTT ended ({gtt_status}) with NO confirmed fill — recreating (retry #{new_retry})")
                if DRY_RUN:
                    log(f"  [DRY RUN] Would recreate GTT SELL {qty} x {symbol} @ {trigger_price}")
                else:
                    new_trig_id = place_gtt(symbol, qty, trigger_price, ltp, enctoken)
                    ws.update_cell(i, COL_GTT_ID + 1,     new_trig_id)
                    ws.update_cell(i, COL_GTT_STATUS + 1, 'RETRY')
                    ws.update_cell(i, COL_RETRY_CNT + 1,  new_retry)
                    ws.update_cell(i, COL_NOTES + 1,
                        f'GTT {gtt_status.lower()} without fill — recreated (retry #{new_retry})')
                recreated += 1

    log(f"=== GTT lifecycle complete | Placed: {placed} | Closed: {closed} | "
        f"Recreated: {recreated} | Skipped: {skipped} ===")


if __name__ == '__main__':
    run()
