#!/usr/bin/env python3
"""
spt_scraper.py — scrapes Type/Target/Timeframe/Have Interest from sptulsian.com.

Two separate interfaces live in this file:

1. The ORIGINAL cron-facing stub (scrape_spt_stock / get_spt_page /
   quit_spt_driver) — still fully disabled and untouched. The OCI VM's IP is
   blocked by CloudFront on sptulsian.com, so main_recommend.py's automated
   9:30am run must keep no-op'ing here rather than attempting a real request.
   Left exactly as-is so the live cron path is never at risk.

2. NEW capture functions (login_session / scrape_category /
   scrape_all_categories) — used by spt_capture.py, runnable either by hand
   from a laptop or on the VM itself via the WARP proxy (SPTULSIAN_PROXY,
   see below). Deliberately NOT imported from config.py (which hardcodes
   /home/ubuntu/.env and requires a full production environment) so this
   half of the file works standalone on any machine with just
   SPT_USERNAME/SPT_PASSWORD set.

The portal serves its six sections through two different backend
controllers, so two parsing strategies are needed — see scrape_category().

Test the capture path independently:
    python3 spt_capture.py
"""
import os
import re
import time
import requests
from bs4 import BeautifulSoup

# ── Original cron-facing stub (unchanged, still disabled) ─────────────────

# SPTulsian section URL map (also used by the new capture functions below).
#
# 'regular income bluechips' is deliberately NOT scraped. That section is a
# covered-call strategy: its rows pair a share trade with an option trade, and
# its "Target" column is a TOTAL POSITION VALUE (e.g. 7,80,000) rather than a
# per-share price. Feeding those numbers into target_price would write a
# nonsense GTT trigger, so the section is excluded rather than mis-parsed.
# The bot has never traded it — open positions are only Little/Big Gems and
# Medium Term Investments.
SPT_URL_MAP = {
    'little gems':              'https://www.sptulsian.com/m/little-gems',
    'big gems':                 'https://www.sptulsian.com/m/big-gems',
    'short term investments':   'https://www.sptulsian.com/m/short-term-investments',
    'medium term investments':  'https://www.sptulsian.com/m/medium-term-investments',
    'multibagger stocks':       'https://www.sptulsian.com/m/multibagger-stocks',
}

# Kept out of SPT_URL_MAP on purpose (see above); listed so the exclusion is
# visible rather than looking like an oversight.
SPT_EXCLUDED_SECTIONS = {
    'regular income bluechips': 'covered-call section; Target is a total position value, not a per-share price',
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
# Each section's JSON controller has its own endpoint name (MW, FC, ...).
_ACTIVE_EP_RE = re.compile(r'/get([A-Za-z0-9]+?)ActiveData')
# Content-based field extraction for the HTML-rendered sections.
_TARGET_RE = re.compile(r'Target:\s*([\d,]+(?:\.\d+)?)\s*(?:\(([^)]*)\))?', re.I)
_BUY_RE = re.compile(r'Buy\s*@?\s*([\d,]+(?:\.\d+)?)\s*(?:\(([^)]*)\))?', re.I)
_INTEREST_RE = re.compile(r'\b(Have|No)\s+interest\b', re.I)

# SPTULSIAN_PROXY scopes a SOCKS5 egress to *only* the scraper's session.
#
# Why this exists: sptulsian.com sits behind CloudFront, whose WAF rejects
# datacenter IP ranges as a class — the VM gets HTTP 403 at the CDN edge and
# the request never reaches the origin at all. No user-agent, header set or
# request pacing changes that; the discriminator is the source address. A
# Cloudflare WARP proxy (consumer-registered egress) is served normally.
#
# Why it is scoped to one session rather than set system-wide: the Zerodha
# broker API is bound to this VM's registered static IP. A system-wide VPN or
# firewall-level redirect would move order placement onto the proxy egress and
# break that binding. Only the session built here is proxied; kite_client.py
# and every other module keep egressing from the static IP untouched.
#
# Note socks5h (not socks5) — the trailing 'h' resolves DNS at the proxy, so
# hostname lookups traverse Cloudflare too. Without it the request egresses
# correctly but the DNS query leaks from the blocked address.
SPTULSIAN_PROXY = os.environ.get('SPTULSIAN_PROXY', '')


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


def build_session():
    """A requests.Session with the scraper's UA, and the WARP SOCKS5 proxy
    attached IF SPTULSIAN_PROXY is set. Proxy scope stops here on purpose —
    no other module's traffic is routed through it (see SPTULSIAN_PROXY)."""
    session = requests.Session()
    session.headers.update({'User-Agent': _UA})
    if SPTULSIAN_PROXY:
        session.proxies = {'http': SPTULSIAN_PROXY, 'https': SPTULSIAN_PROXY}
    return session


def login_session(username, password, log=_clog):
    """Log into sptulsian.com and return an authenticated requests.Session.
    Raises SPTLoginError on any failure. Does not print the password."""
    if not username or not password:
        raise SPTLoginError("SPT_USERNAME/SPT_PASSWORD not set")

    session = build_session()

    r = session.get(f'{BASE_URL}/login', timeout=30)
    if r.status_code != 200:
        raise SPTLoginError(f"GET /login failed: HTTP {r.status_code}")
    m = _TOKEN_RE.search(r.text)
    if not m:
        raise SPTLoginError("Could not find CSRF token on login page — page layout may have changed")
    token = m.group(1)

    session.post(f'{BASE_URL}/loginUser', data={
        '_token': token, 'username': username, 'password': password, 'prev_url': '',
    }, headers={'Referer': f'{BASE_URL}/login'}, timeout=30)

    # Verify on SUBSTANCE, not on marker strings.
    #
    # The obvious check — look for a "logout" link or an /m/my-alerts nav
    # entry — is worthless here and silently passes while signed out: the
    # public teaser page is fully rendered and contains both markers in its
    # navigation markup. Verified empirically against an anonymous session.
    # A session is only accepted if a section actually yields parsed call
    # rows, which no signed-out response can produce.
    probe_url = SPT_URL_MAP['little gems']
    try:
        rows = scrape_category(session, probe_url, log=lambda *_: None)
    except SPTScrapeError as e:
        raise SPTLoginError(f"Login could not be verified — probe scrape failed: {e}")
    if not rows:
        raise SPTLoginError("Login did not take (probe returned no call rows) — "
                            "check SPT_USERNAME/SPT_PASSWORD")

    log("SPTulsian login OK (verified by parsing real call rows)")
    return session


def _category_slug(category_url):
    return category_url.rstrip('/').rsplit('/', 1)[-1]


def _row(stock_name, target_price, timeframe, have_interest, source,
         call_id=None, call_datetime='', buy_price=None, exit_remarks=''):
    """One normalised call row — the single shape both strategies emit."""
    return {
        'stock_name': stock_name,
        'target_price': target_price,
        'timeframe': timeframe,
        'have_interest': have_interest,
        'source': source,            # 'active' | 'archive'
        'call_id': call_id,
        'call_datetime': call_datetime,
        'buy_price': buy_price,
        'exit_remarks': exit_remarks,
    }


def _json_field_prefix(raw):
    """Each section is served by its own controller and prefixes its columns
    accordingly — Little Gems returns m7_p_stock_code, Big Gems m8_p_stock_code,
    and the active/archive tables differ again (m7_ vs m7_p_). Derive the
    prefix from whichever key ends in 'stock_code' instead of hardcoding it,
    so a new section doesn't silently parse as empty."""
    for k in raw:
        if k.endswith('stock_code'):
            return k[:-len('stock_code')]
    return None


def _parse_json_rows(data, source_key, source):
    rows = []
    for raw in (data.get(source_key) or []):
        pfx = _json_field_prefix(raw)
        if not pfx:
            continue
        name = (raw.get(f'{pfx}stock_code') or '').strip()
        if not name:
            continue
        rows.append(_row(
            stock_name=name,
            target_price=_clean_float(raw.get(f'{pfx}target_price')),
            timeframe=(raw.get(f'{pfx}time_horizon') or '').strip(),
            have_interest=bool(raw.get(f'{pfx}disclosure')),
            source=source,
            call_id=raw.get(f'{pfx}id'),
            call_datetime=(raw.get(f'{pfx}entry_datetime') or '').strip(),
            # note: '{pfx}entry_price' is the recommended buy price, while
            # '{pfx}stock_entry_price' is the market price at entry — do not
            # confuse the two, both end in 'entry_price'.
            buy_price=_clean_float(raw.get(f'{pfx}entry_price')),
            exit_remarks=(raw.get(f'{pfx}remarks') or '').strip(),
        ))
    return rows


def _parse_html_table(table, source):
    """Parse one rendered calls table. Row markup is not positionally stable
    (the same table emits 11- and 13-cell rows, because each row carries both
    desktop and mobile cells), so fields are read from the row's text by
    content rather than by column index. The stock name is the exception —
    it is reliably the first cell."""
    rows = []
    for tr in table.find_all('tr'):
        cells = tr.find_all('td')
        if not cells:
            continue  # header row
        text = re.sub(r'\s+', ' ', tr.get_text(' ', strip=True))
        tm = _TARGET_RE.search(text)
        if not tm:
            continue  # not a call row
        name = re.sub(r'\s+', ' ', cells[0].get_text(' ', strip=True))
        if not name:
            continue
        bm = _BUY_RE.search(text)
        im = _INTEREST_RE.search(text)
        rows.append(_row(
            stock_name=name,
            target_price=_clean_float(tm.group(1)),
            timeframe=(tm.group(2) or '').strip(),
            have_interest=bool(im and im.group(1).lower() == 'have'),
            source=source,
            call_datetime=((bm.group(2) or '').strip() if bm else ''),
            buy_price=(_clean_float(bm.group(1)) if bm else None),
        ))
    return rows


def _parse_html_rows(html):
    """Sections without a JSON controller render their calls straight into the
    page as two tables: the live calls, then 'Archives (last N calls)'. Only
    the archive table carries Exit Remarks / Exit Date-Time columns, so that
    header is used to tell them apart — most rows on these pages are closed
    calls, and applying a closed call's target to a live position would set a
    wrong GTT trigger."""
    soup = BeautifulSoup(html, 'html.parser')
    rows = []
    for table in soup.find_all('table'):
        headers = [re.sub(r'\s+', ' ', th.get_text(' ', strip=True)).lower()
                   for th in table.find_all('th')]
        if not headers or not any(h.startswith('stock') for h in headers):
            continue  # e.g. the DOs/DON'Ts guidelines table
        source = 'archive' if any('exit' in h for h in headers) else 'active'
        rows.extend(_parse_html_table(table, source))
    return rows


def scrape_category(session, category_url, log=_clog):
    """Fetch calls for one section, using whichever strategy that section's
    backend controller requires:

      - JSON  — the page wires up a /get<X>ActiveData endpoint (Little Gems
                uses /getMWActiveData, Big Gems /getFCActiveData). The
                endpoint name is read off the page rather than hardcoded;
                calling the wrong section's endpoint returns HTTP 500.
      - HTML  — the remaining sections ship no data endpoint at all and
                render their calls directly into the page markup.

    Returns a list of normalised rows (see _row). Raises SPTScrapeError on
    failure so callers can skip one section without losing the rest."""
    slug = _category_slug(category_url)

    page = session.get(category_url, timeout=30)
    if page.status_code != 200:
        raise SPTScrapeError(f"GET {category_url} failed: HTTP {page.status_code}")
    m = _TOKEN_RE.search(page.text)
    if not m:
        raise SPTScrapeError(f"No CSRF token found on {category_url}")
    token = m.group(1)

    endpoints = sorted(set(_ACTIVE_EP_RE.findall(page.text)))
    if endpoints:
        endpoint = f'/get{endpoints[0]}ActiveData'
        resp = session.get(f'{BASE_URL}{endpoint}',
                            params={'width': 1200, 'section_name': slug},
                            headers={'X-CSRF-TOKEN': token,
                                     'X-Requested-With': 'XMLHttpRequest',
                                     'Accept': 'application/json, text/javascript, */*; q=0.01',
                                     'Referer': category_url},
                            timeout=30)
        if resp.status_code != 200:
            raise SPTScrapeError(f"{endpoint}?section_name={slug} -> HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise SPTScrapeError(f"{endpoint}?section_name={slug} did not return JSON")
        rows = (_parse_json_rows(data, 'active_table_data', 'active')
                + _parse_json_rows(data, 'archive_table_data', 'archive'))
        strategy = f'json {endpoint}'
    else:
        rows = _parse_html_rows(page.text)
        strategy = 'html'

    n_active = sum(1 for r in rows if r['source'] == 'active')
    log(f"  {slug}: {len(rows)} call(s) [{n_active} active] via {strategy}")
    return rows


def scrape_all_categories(session, log=_clog):
    """Loop every section in SPT_URL_MAP. One section failing does not stop
    the others — each category's result is {'rows': [...], 'error': str|None}."""
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
