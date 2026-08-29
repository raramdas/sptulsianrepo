#!/usr/bin/env python3
"""
email_reader.py — connects to Gmail via IMAP, finds today's SPTulsian tip
emails within the 8-10 AM IST window, and extracts stock/price/category.

Test independently:
    python3 -c "from email_reader import parse_todays_emails; print(parse_todays_emails())"
"""
import imaplib
import email as emaillib
import re
import time
from datetime import datetime, timedelta

from lib.config import log, IST, GMAIL_USER, GMAIL_APP_PASSWORD, TEST_DATE

# Known SPTulsian categories — must match category_allocation.category_name in Oracle exactly.
# Sorted longest-first so "Medium Term Investments" is matched before a shorter partial like "Medium Term".
KNOWN_CATEGORIES = sorted([
    'Little Gems',
    'Big Gems',
    'Short Term Investments',
    'Medium Term Investments',
    'Regular Income Bluechips',
    'Multibagger Stocks',
], key=len, reverse=True)

# If an IMAP search for today's date range comes back with zero matches, we
# retry a few times with a short delay AND a fresh connection each attempt,
# rather than immediately concluding "no tips today". Confirmed root cause:
# reusing the SAME connection across retries (just sleeping + re-searching)
# returned 0 for a full 6 minutes straight on 27-Jul, while a brand-new
# connection found the same emails instantly at any point in that window —
# Gmail's IMAP can return a session-cached view of the mailbox on a
# persistent connection, so waiting alone doesn't help; a fresh
# login+SELECT per attempt is what actually forces an up-to-date view.
SEARCH_RETRY_ATTEMPTS = 3
SEARCH_RETRY_DELAY_SECONDS = 90


def extract_category(subject):
    """Match the email subject against the known category list rather than
    blindly taking the first two words (which breaks for 3-word category names
    like 'Medium Term Investments')."""
    subj_clean = re.sub(r'[^a-zA-Z ]', ' ', subject)
    subj_clean = re.sub(r'\s+', ' ', subj_clean).strip().lower()
    for category in KNOWN_CATEGORIES:
        if subj_clean.startswith(category.lower()):
            return category
    # Fallback: first two words, as before (logged so mismatches are visible)
    subj_words = subj_clean.split()
    fallback = ' '.join(w.title() for w in subj_words[:2]) if len(subj_words) >= 2 else ''
    log(f"  Category not in known list — using fallback: '{fallback}' (subject: '{subject}')")
    return fallback


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


def _connect_and_select(mailbox):
    """Fresh login + SELECT — used for every search attempt (not reused
    across retries) to guarantee an up-to-date view of the mailbox."""
    mail = imaplib.IMAP4_SSL('imap.gmail.com')
    mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    mail.select(mailbox)
    return mail


def _search_with_retry(mailbox, search_str):
    """Runs the given IMAP search, retrying with a delay AND a fresh
    connection each attempt if it comes back empty. Returns (found_ids, mail)
    — the caller uses the returned (still-open) connection to fetch the
    matched messages, avoiding yet another reconnect right after."""
    mail = None
    for attempt in range(1, SEARCH_RETRY_ATTEMPTS + 1):
        if mail:
            try:
                mail.logout()
            except Exception:
                pass
        mail = _connect_and_select(mailbox)
        _, ids = mail.search(None, search_str)
        found = ids[0].split() if ids[0] else []
        if found:
            if attempt > 1:
                log(f"  Search succeeded on attempt {attempt}/{SEARCH_RETRY_ATTEMPTS} "
                    f"with a fresh connection (earlier attempt(s) returned 0 on a "
                    f"stale connection — this is the fix, not just waiting longer)")
            return found, mail
        if attempt < SEARCH_RETRY_ATTEMPTS:
            log(f"  Search attempt {attempt}/{SEARCH_RETRY_ATTEMPTS} found 0 — "
                f"waiting {SEARCH_RETRY_DELAY_SECONDS}s before retrying with a fresh connection")
            time.sleep(SEARCH_RETRY_DELAY_SECONDS)
    return [], mail


def parse_todays_emails():
    """Returns a list of tip dicts: {date, stock, email_price, category}."""
    tips  = []
    today = TEST_DATE if TEST_DATE else datetime.now(IST).strftime('%d-%b-%Y')

    log(f"Connecting to Gmail, searching for SPTulsian emails on {today}...")

    parsed_date = datetime.strptime(today, '%d-%b-%Y')
    next_day  = (parsed_date + timedelta(days=1)).strftime('%d-%b-%Y')
    imap_date = parsed_date.strftime('%d-%b-%Y')
    search_str = f'(FROM "sptulsian.com" SINCE {imap_date} BEFORE {next_day})'
    log(f"IMAP search: {search_str}")

    found, mail = _search_with_retry('inbox', search_str)
    log(f"Emails matched: {len(found)}")

    if not found:
        mail.logout()
        found, mail = _search_with_retry('"[Gmail]/All Mail"', search_str)
        log(f"All Mail fallback matched: {len(found)}")

    for num in found:
        _, data = mail.fetch(num, '(RFC822)')
        msg      = emaillib.message_from_bytes(data[0][1])
        subj     = msg.get('Subject', '')
        date_hdr = msg.get('Date', '')
        log(f"  Email: '{subj}' | {date_hdr}")

        try:
            msg_dt   = emaillib.utils.parsedate_to_datetime(date_hdr).astimezone(IST)
            msg_mins = msg_dt.hour * 60 + msg_dt.minute
            # Window: 8:00 AM (480) to 10:00 AM (600) IST
            if msg_mins < 480 or msg_mins > 600:
                log(f"  Skipping — outside time window: {msg_dt.strftime('%H:%M')} IST")
                continue
        except Exception as e:
            log(f"  Could not parse date '{date_hdr}': {e} — processing anyway")

        body = get_email_body_text(msg)
        text = subj + ' ' + body

        # Direction is captured, not assumed. This pattern used to hardcode
        # "Buy", so a Sell call did not match and was dropped in silence —
        # never logged as a tip, never recorded. The bot would not have bought
        # it, but nothing would have told anyone the advisory had reversed on a
        # position still being held to its original target.
        matches = re.findall(
            r'Call added[:\s]*([^(]+?)\s*\((Buy|Sell)\s*@\s*([\d.]+)\)',
            text, re.IGNORECASE)
        log(f"  Tip matches found: {matches}")

        for stock, direction, price_str in matches:
            stock = stock.strip()
            direction = (direction or '').strip().title()  # 'Buy' | 'Sell'
            try:
                price = float(price_str)
            except ValueError:
                continue
            if stock and price:
                category = extract_category(subj)
                tips.append({'date': today, 'stock': stock, 'email_price': price,
                             'category': category, 'direction': direction})
                mark = '✅' if direction == 'Buy' else '🔻'
                log(f"  {mark} Tip: {stock} @ {price} [{direction}]")

    mail.logout()
    return tips


if __name__ == '__main__':
    result = parse_todays_emails()
    print(f"\nFound {len(result)} tips:")
    for t in result:
        print(f"  {t}")
