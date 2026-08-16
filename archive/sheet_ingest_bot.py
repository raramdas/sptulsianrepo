#!/usr/bin/env python3
# sheet_ingest_bot.py  (Bot A)
# Run shortly after SPTulsian's usual email window (e.g. 10:15 AM IST).
#
# Reads today's "Call added" emails, resolves Type/Target/Timeframe/Have
# Interest via SPTulsian scraping (currently disabled — see scrape_spt_stock
# below — so those fields will come back blank until the site whitelists
# this VM's IP; fill them in manually in the sheet until then), and appends
# one row per new tip. Does NOT place any buy order — that's purchase_bot.py.

import re, imaplib, email as emaillib
from datetime import datetime, timedelta
from email.header import decode_header

from archive.kite_common import (
    log, get_sheet, GSHEET_CREDS_FILE, pad_row,
    COL_STOCK, COL_BUY_DATE, NUM_COLS,
)
import os

GMAIL_USER         = os.environ['GMAIL_USER']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']

DRY_RUN   = True   # Set to False for live
TEST_DATE = '10-Jul-2026'

from archive.kite_common import IST


def get_email_body_text(msg):
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            html = part.get_payload(decode=True).decode('utf-8', 'ignore')
            text = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            return re.sub(r'\s+', ' ', text).strip()
        if part.get_content_type() == 'text/plain':
            return part.get_payload(decode=True).decode('utf-8', 'ignore')
    return ''


def parse_todays_emails():
    tips  = []
    today = TEST_DATE if TEST_DATE else datetime.now(IST).strftime('%d-%b-%Y')

    log(f"Connecting to Gmail, searching for SPTulsian emails on {today}...")
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select('inbox')

    parsed_date = datetime.strptime(today, '%d-%b-%Y')
    next_day  = (parsed_date + timedelta(days=1)).strftime('%d-%b-%Y')
    imap_date = parsed_date.strftime('%d-%b-%Y')
    search_str = f'(FROM "sptulsian.com" SINCE {imap_date} BEFORE {next_day})'
    log(f"IMAP search: {search_str}")
    _, ids = mail.search(None, search_str)

    found = ids[0].split() if ids[0] else []
    log(f"Emails matched: {len(found)}")

    if not found:
        mail.select('"[Gmail]/All Mail"')
        _, ids2 = mail.search(None, search_str)
        found = ids2[0].split() if ids2[0] else []
        log(f"All Mail fallback matched: {len(found)}")

    for num in found:
        _, data = mail.fetch(num, '(RFC822)')
        msg     = emaillib.message_from_bytes(data[0][1])
        subj    = msg.get('Subject', '')
        date_hdr = msg.get('Date', '')
        log(f"  Email: '{subj}' | {date_hdr}")

        try:
            msg_dt   = emaillib.utils.parsedate_to_datetime(date_hdr).astimezone(IST)
            msg_mins = msg_dt.hour * 60 + msg_dt.minute
            if msg_mins < 480 or msg_mins > 600:
                log(f"  Skipping — outside time window: {msg_dt.strftime('%H:%M')} IST")
                continue
        except Exception as e:
            log(f"  Could not parse date '{date_hdr}': {e} — processing anyway")

        body = get_email_body_text(msg)
        text = subj + ' ' + body

        matches = re.findall(r'Call added[:\s]*([^(]+?)\s*\(Buy\s*@\s*([\d.]+)\)', text, re.IGNORECASE)
        log(f"  Tip matches found: {matches}")

        for stock, price_str in matches:
            stock = stock.strip()
            try:
                price = float(price_str)
            except ValueError:
                continue
            if stock and price:
                subj_words = re.sub(r'[^a-zA-Z ]', ' ', subj).split()
                category = ' '.join(subj_words[:2]) if len(subj_words) >= 2 else ''
                tips.append({'date': today, 'stock': stock, 'email_price': price, 'category': category})

    mail.logout()
    return tips


# SPTulsian scraping disabled — OCI VM IP blocked by CloudFront.
# Will be enabled once static IP is whitelisted with SPTulsian.
# Type, Target, Timeframe, Have Interest must be entered manually in the
# sheet until then. purchase_bot.py will simply skip rows where the "Have
# Interest" column is still blank, so filling these in is what unblocks a buy.
def scrape_spt_stock(stock_name, category):
    return {'type': '', 'target': '', 'timeframe': '', 'have_interest': ''}


def is_duplicate(ws, stock, date):
    """Prevent logging the same tip twice."""
    try:
        rows = ws.get_all_values()
        for r in rows[1:]:
            r = pad_row(r)
            row_date  = r[COL_BUY_DATE].strip()
            row_stock = r[COL_STOCK].strip()
            date_match = (date in row_date) or (row_date in date)
            if date_match and row_stock.lower() == stock.lower():
                return True
    except Exception as e:
        log(f"  is_duplicate error: {e}")
    return False


def log_to_sheet(ws, tip):
    today_str = datetime.now(IST).strftime('%Y-%m-%d')
    ws.append_row([
        tip.get('category', ''),       # A: Category
        tip.get('stock', ''),          # B: Stock
        '',                            # C: Symbol — filled in later, manually or by purchase_bot
        tip.get('type', ''),           # D: Type (from SPTulsian, blank for now)
        today_str,                     # E: Buy Date (recommendation date)
        tip.get('email_price', ''),    # F: Recommended Price
        tip.get('target', ''),         # G: Target (from SPTulsian, blank for now)
        tip.get('timeframe', ''),      # H: Timeframe (from SPTulsian, blank for now)
        tip.get('have_interest', ''),  # I: Have Interest — FILL IN MANUALLY (Yes/No)
        'Open',                        # J: Status
        '', '', '',                    # K,L,M: Target Met / Exit Date / Gain (unused until sold)
        '', '', '',                    # N,O,P: My Buy Date / Order Type / Buy Order ID — blank = not bought yet
        '', '', '',                    # Q,R,S: Mkt Price / My Buy Price / My Buy Qty
        '', '', '', '',                # T,U,V,W: Sell Date / Sell Price / Sell Qty / Gain-Loss
        '', '',                        # X,Y: GTT ID / GTT Status
        '',                            # Z: Notes
        '',                            # AA: Retry Count
    ])


def run():
    log("=== Sheet Ingest Bot starting ===")
    log(f"Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}")
    ws = get_sheet()

    tips = parse_todays_emails()
    if not tips:
        log("No tips found today.")
        return

    log(f"Tips found: {[t['stock'] for t in tips]}")
    logged, skipped = 0, 0
    for tip in tips:
        if is_duplicate(ws, tip['stock'], tip['date']):
            log(f"  Duplicate — skipping: {tip['stock']}")
            skipped += 1
            continue

        spt = scrape_spt_stock(tip['stock'], tip.get('category', ''))
        tip['type']          = spt['type']
        tip['target']        = spt['target']
        tip['timeframe']     = spt['timeframe']
        tip['have_interest'] = spt['have_interest']

        if DRY_RUN:
            log(f"  [DRY RUN] Would log tip: {tip['stock']} @ {tip['email_price']}")
        else:
            log_to_sheet(ws, tip)
            log(f"  Logged: {tip['stock']} @ {tip['email_price']}")
        logged += 1

    log(f"=== Ingest complete | Logged: {logged} | Skipped (dup): {skipped} ===")


if __name__ == '__main__':
    run()
