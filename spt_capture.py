#!/usr/bin/env python3
"""
spt_capture.py — run this BY HAND from your own laptop (not on the VM —
that's the whole point, its IP is CloudFront-blocked on sptulsian.com).

Logs into sptulsian.com, scrapes Type/Target/Timeframe/Have-Interest for
every section, previews what would change on your open trades, and — only
after you confirm — saves it via capture_api.py running on the VM. That
API call does exactly what clicking "Save changes" on the dashboard's Set
Targets page does (db.update_trade_target()), just triggered from here
instead of the browser.

Setup (one-time): create a `.env` file next to this script with:
    SPT_USERNAME=...
    SPT_PASSWORD=...
    CAPTURE_API_URL=https://raramdas-stockbot.duckdns.org/api/spt-capture
    CAPTURE_API_KEY=...

On the VM this needs no .env of its own — it falls back to /home/ubuntu/.env,
which already carries the credentials plus SPTULSIAN_PROXY (the WARP SOCKS5
egress that gets it past CloudFront's block on the VM's IP).

Usage:
    python3 spt_capture.py            # scrape, preview, ask before saving
    python3 spt_capture.py --dry-run  # scrape + preview only, never saves
    python3 spt_capture.py --yes      # skip the confirmation prompt
"""
import os
import sys
import argparse
import requests
from dotenv import load_dotenv

# Laptop layout first (a .env beside this script), then the VM's shared
# /home/ubuntu/.env. load_dotenv doesn't overwrite already-set vars, so the
# local file wins where both define a key, and the VM needs no duplicate copy
# of the credentials.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
load_dotenv('/home/ubuntu/.env')
import spt_scraper  # noqa: E402


def fmt_price(v):
    return f"₹{v:,.2f}" if v is not None else "—"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help="Preview only, never save")
    ap.add_argument('--yes', action='store_true', help="Skip the confirmation prompt")
    args = ap.parse_args()

    username = os.environ.get('SPT_USERNAME', '')
    password = os.environ.get('SPT_PASSWORD', '')
    api_url = os.environ.get('CAPTURE_API_URL', '').rstrip('/')
    api_key = os.environ.get('CAPTURE_API_KEY', '')
    if not all([username, password, api_url, api_key]):
        sys.exit("Missing SPT_USERNAME / SPT_PASSWORD / CAPTURE_API_URL / CAPTURE_API_KEY in .env")

    print("Logging into sptulsian.com...")
    try:
        session = spt_scraper.login_session(username, password)
    except spt_scraper.SPTLoginError as e:
        sys.exit(f"Login failed: {e}")

    print("Scraping sections...")
    results = spt_scraper.scrape_all_categories(session)

    headers = {'X-API-Key': api_key, 'Content-Type': 'application/json'}
    print("Asking the dashboard to match against your open trades...")
    try:
        r = requests.post(f'{api_url}/preview', json={'results': results}, headers=headers, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        sys.exit(f"Preview request failed: {e}")
    preview = r.json()

    matches = preview['matches']
    unmatched = preview['unmatched_trades']
    cat_errors = preview['category_errors']
    changed = [m for m in matches if m['changed']]

    print(f"\n{'='*70}")
    if cat_errors:
        print("Sections that could NOT be fetched (skipped, not your fault):")
        for cat, err in cat_errors.items():
            print(f"  - {cat}: {err}")
        print()

    if not changed:
        print("No changes found — every matched open trade already has the latest "
              "target/timeframe/have-interest on file.")
    else:
        n_archived = sum(1 for m in changed if m.get('spt_source') == 'archive')
        print(f"{len(changed)} open trade(s) would be updated:")
        if n_archived:
            print(f"  NOTE: {n_archived} of these match a CLOSED (archived) SPTulsian call, "
                  f"marked [closed call] below — SPTulsian is no longer running that call, "
                  f"so check the target still makes sense before saving.")
        print()
        for m in changed:
            src = m.get('spt_source', 'unknown')
            tag = " [closed call]" if src == 'archive' else ""
            print(f"#{m['trade_id']} {m['stock_name']} ({m['category_name']}, bought {m['buy_date']}) "
                  f"[{m['match_confidence']} match, SPT call {m['spt_call_datetime']}]{tag}")
            if m.get('spt_exit_remarks'):
                print(f"    SPT exit remark: {m['spt_exit_remarks']}")
            if m['old_target'] != m['new_target']:
                print(f"    Target:        {fmt_price(m['old_target'])}  ->  {fmt_price(m['new_target'])}")
            if m['old_timeframe'] != m['new_timeframe']:
                print(f"    Timeframe:     {m['old_timeframe'] or '—'}  ->  {m['new_timeframe'] or '—'}")
            if m['old_have_interest'] != m['new_have_interest']:
                print(f"    Have Interest: {m['old_have_interest'] or '—'}  ->  {m['new_have_interest']}")
            print()

    if unmatched:
        print(f"{len(unmatched)} open trade(s) had no matching SPTulsian call found this run:")
        for u in unmatched:
            print(f"  - #{u['trade_id']} {u['stock_name']} ({u['category_name']}, bought {u['buy_date']})")
        print()

    if not changed:
        return
    if args.dry_run:
        print("(--dry-run: not saving)")
        return

    if not args.yes:
        resp = input(f"Save these {len(changed)} change(s)? [y/N] ").strip().lower()
        if resp != 'y':
            print("Not saved.")
            return

    updates = [{
        'trade_id': m['trade_id'],
        'target_price': m['new_target'],
        'have_interest': m['new_have_interest'],
        'timeframe': m['new_timeframe'],
    } for m in changed]

    try:
        r2 = requests.post(f'{api_url}/apply', json={'updates': updates}, headers=headers, timeout=30)
        r2.raise_for_status()
    except requests.RequestException as e:
        sys.exit(f"Apply request failed: {e}")
    result = r2.json()
    print(f"Saved {result['updated']} trade(s).")
    if result['errors']:
        print("Errors:")
        for e in result['errors']:
            print(f"  - trade #{e['trade_id']}: {e['error']}")


if __name__ == '__main__':
    main()
