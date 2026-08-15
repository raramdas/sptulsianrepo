#!/usr/bin/env python3
"""
spt_scraper.py — scrapes Type/Target/Timeframe/Have Interest from sptulsian.com.

Two separate interfaces live in this file:

1. The ORIGINAL cron-facing stub (scrape_spt_stock / get_spt_page /
   quit_spt_driver) — still fully disabled and untouched. The OCI VM's IP is
   blocked by CloudFront on sptulsian.com, so main_recommend.py's automated
   9:30am run must keep no-op'ing here rather than attempting a real request.
   Left exactly as-is so the live cron path is never at risk.

2. NEW manual-capture functions (login_session / scrape_category /
   scrape_all_categories) — used only by spt_capture.py, run by hand from a
   laptop whose IP isn't blocked. Deliberately NOT imported from config.py
   (which hardcodes /home/ubuntu/.env and requires a full production
   environment) so this half of the file works standalone on any machine
   with just SPT_USERNAME/SPT_PASSWORD set.

Test the manual-capture path independently:
    python3 spt_capture.py
"""
import os
import re
import time
import requests

# ── Original cron-facing stub (unchanged, still disabled) ─────────────────

# SPTulsian section URL map (also used by the new capture functions below)
SPT_URL_MAP = {
    'little gems':              'https://www.sptulsian.com/m/little-gems',
    'big gems':                 'https://www.sptulsian.com/m/big-gems',
    'short term investments':   'https://www.sptulsian.com/m/short-term-investments',
    'medium term investments':  'https://www.sptulsian.com/m/medium-term-investments',
    'regular income bluechips': 'https://www.sptulsian.com/m/regular-income-bluechips',
    'multibagger stocks':       'https://www.sptulsian.com/m/multibagger-stocks',
}


def get_spt_page():
    """Stub — returns None until IP whitelisting is confirmed with SPTulsian."""
    return None


def scrape_spt_stock(stock_name, category):
    """Stub — returns empty result until scraping is re-enabled. Called by
    main_recommend.py's cron run; must stay a safe no-op (see module docstring)."""
    return {'type': '', 'target': '', 'timeframe': '', 'have_interest': ''}


def quit_spt_driver():
    """No-op until SPTulsian scraping is re-enabled."""
    pass


# ── New: manual-capture functions (used only by spt_capture.py) ───────────

BASE_URL = 'https://www.sptulsian.com'
_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_TOKEN_RE = re.compile(r'name="_token"[^>]*value="([^"]+)"')


class SPTLoginError(Exception):
    pass


class SPTScrapeError(Exception):
    pass


def _clog(msg):
    """Plain timestamped print — deliberately not config.log (see module docstring)."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _clean_float(val):
    """'1,935' / '412' -> float. Returns None if unparseable."""
    if val is None:
        return None
    try:
        return float(str(val).replace(',', '').strip())
    except (TypeError, ValueError):
        return None


def login_session(username, password, log=_clog):
    """Log into sptulsian.com and return an authenticated requests.Session.
    Raises SPTLoginError on any failure. Does not print the password."""
    if not username or not password:
        raise SPTLoginError("SPT_USERNAME/SPT_PASSWORD not set")

    session = requests.Session()
    session.headers.update({'User-Agent': _UA})

    r = session.get(f'{BASE_URL}/login', timeout=15)
    if r.status_code != 200:
        raise SPTLoginError(f"GET /login failed: HTTP {r.status_code}")
    m = _TOKEN_RE.search(r.text)
    if not m:
        raise SPTLoginError("Could not find CSRF token on login page — page layout may have changed")
    token = m.group(1)

    r2 = session.post(f'{BASE_URL}/loginUser', data={
        '_token': token, 'username': username, 'password': password, 'prev_url': '',
    }, headers={'Referer': f'{BASE_URL}/login'}, timeout=15)

    # Confirm login actually took by checking for a logged-in-only marker
    r3 = session.get(f'{BASE_URL}/m/little-gems', timeout=15)
    if 'ogout' not in r3.text and 'my-alerts' not in r3.text.lower():
        raise SPTLoginError("Login did not take — check SPT_USERNAME/SPT_PASSWORD")

    log("SPTulsian login OK")
    return session


def _category_slug(category_url):
    return category_url.rstrip('/').rsplit('/', 1)[-1]


def scrape_category(session, category_url, log=_clog):
    """Fetch active+recent-archive calls for one section. Returns a list of
    dicts: {stock_name, target_price, timeframe, have_interest, call_id,
    call_datetime, buy_price}. Raises SPTScrapeError on failure — callers
    should catch this per-category so one broken section doesn't block the
    others (see scrape_all_categories)."""
    slug = _category_slug(category_url)

    page = session.get(category_url, timeout=15)
    if page.status_code != 200:
        raise SPTScrapeError(f"GET {category_url} failed: HTTP {page.status_code}")
    m = _TOKEN_RE.search(page.text)
    if not m:
        raise SPTScrapeError(f"No CSRF token found on {category_url}")
    token = m.group(1)

    headers = {
        'X-CSRF-TOKEN': token,
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': category_url,
    }
    resp = session.get(f'{BASE_URL}/getMWActiveData', params={'width': 1200, 'section_name': slug},
                        headers=headers, timeout=15)
    if resp.status_code != 200:
        raise SPTScrapeError(f"/getMWActiveData?section_name={slug} -> HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        raise SPTScrapeError(f"/getMWActiveData?section_name={slug} did not return JSON")

    raw_rows = list(data.get('active_table_data') or []) + list(data.get('archive_table_data') or [])

    rows = []
    for r in raw_rows:
        stock_name = (r.get('m7_p_stock_code') or '').strip()
        if not stock_name:
            continue
        rows.append({
            'stock_name': stock_name,
            'target_price': _clean_float(r.get('m7_p_target_price')),
            'timeframe': (r.get('m7_p_time_horizon') or '').strip(),
            'have_interest': bool(r.get('m7_p_disclosure')),
            'call_id': r.get('m7_p_id'),
            'call_datetime': (r.get('m7_p_entry_datetime') or '').strip(),
            'buy_price': _clean_float(r.get('m7_p_entry_price')),
        })
    log(f"  {slug}: {len(rows)} call(s)")
    return rows


def scrape_all_categories(session, log=_clog):
    """Loop every section in SPT_URL_MAP. One section failing (e.g. the known
    Big Gems / Medium Term Investments server error) does not stop the others
    — each category's result is {'rows': [...], 'error': str|None}."""
    results = {}
    for category_name, url in SPT_URL_MAP.items():
        try:
            rows = scrape_category(session, url, log=log)
            results[category_name] = {'rows': rows, 'error': None}
        except SPTScrapeError as e:
            log(f"  {category_name}: FAILED — {e}")
            results[category_name] = {'rows': [], 'error': str(e)}
        time.sleep(1.5)  # be gentle between requests
    return results


if __name__ == '__main__':
    result = scrape_spt_stock('Zee Ent', 'Little Gems')
    print(f"scrape_spt_stock result (should be blank while disabled): {result}")
