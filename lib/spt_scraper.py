#!/usr/bin/env python3
"""
spt_scraper.py — scrapes Type/Target/Timeframe/Have Interest from sptulsian.com.

The VM cannot reach sptulsian.com directly — CloudFront's WAF rejects
datacenter IP ranges at the edge (HTTP 403), before the request reaches the
origin. All traffic here therefore goes through a Cloudflare WARP SOCKS5
proxy, scoped to this module's session only; see SPTULSIAN_PROXY below.

Two interfaces live in this file:

1. scrape_spt_stock() — the per-tip lookup main_recommend.py's 9:30am cron
   calls. Logs in and scrapes all sections once per process, then serves
   every tip from that cache. Returns blanks (never raises, never returns
   stale data) if the scrape fails, so a target lookup cannot block the
   recommendation run; the failure surfaces via the liveness watermark that
   spt_watchdog.py alerts on.

2. login_session / scrape_category / scrape_all_categories — the lower-level
   capture API, used by spt_capture.py for manual review-then-save runs.

Deliberately NOT imported from config.py (which hardcodes /home/ubuntu/.env
and requires a full production environment) so this module stays usable on
any machine with just SPT_USERNAME/SPT_PASSWORD set.

The portal serves its sections through two different backend controllers, so
two parsing strategies are needed — see scrape_category().

Test independently:
    python3 spt_capture.py --dry-run
"""
import os
import re
import json
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# Written only after a genuinely parsed scrape; spt_watchdog.py alerts when
# it goes stale. Overridable via SPT_WATERMARK_PATH for testing.
DEFAULT_WATERMARK_PATH = '/home/ubuntu/spt_scrape_watermark.json'


class SPTLoginError(Exception):
    pass


class SPTScrapeError(Exception):
    pass


def _clog(msg):
    """Plain timestamped print — deliberately not config.log, so this module
    stays importable without a full production environment."""
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ── Section map ──────────────────────────────────────────────────────────

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
    """Retained for backwards compatibility; the per-section fetch now happens
    inside scrape_category()."""
    return None


# One login + one full scrape per process, reused across every tip in the run.
_run_cache = None


def _lookup_index(results):
    """(category, stock) -> row, preferring a still-running call. Mirrors the
    ranking in capture_api.build_matches; kept deliberately category-scoped,
    since the same stock carries different targets in different sections."""
    index = {}
    for category_name, r in results.items():
        cat_key = _norm_name(category_name)
        for row in r.get('rows', []):
            key = _norm_name(row['stock_name'])
            if not key:
                continue
            prev = index.get((cat_key, key))
            if prev is None or _row_rank(row) > _row_rank(prev):
                index[(cat_key, key)] = row
    return index


def _norm_name(s):
    return re.sub(r'\s+', ' ', (s or '').strip()).upper()


def _row_rank(row):
    return (0 if row.get('closed') else 1,
            1 if row.get('source') == 'active' else 0)


def refresh_spt_data(log=_clog):
    """Log in, scrape every section once, cache the result for this process,
    and write the liveness watermark if anything was genuinely read.

    main_recommend.py calls this once per run BEFORE looking at tips, so the
    watermark is a true daily heartbeat: on a market holiday no tips arrive,
    and if the watermark were only written as a side effect of a tip lookup,
    spt_watchdog.py would report a dead scraper on every holiday.

    Returns True if at least one section was read. Never raises."""
    global _run_cache

    if _run_cache is not None:
        return _run_cache['ok']

    username = os.environ.get('SPT_USERNAME', '')
    password = os.environ.get('SPT_PASSWORD', '')
    try:
        session = login_session(username, password, log=log)
        results = scrape_all_categories(session, log=log)
    except (SPTLoginError, SPTScrapeError, requests.RequestException) as e:
        log(f"  SPTulsian scrape unavailable ({e}) — continuing without "
            f"target/timeframe; spt_watchdog.py will flag this")
        _run_cache = {'index': {}, 'ok': False}
        return False

    ok_sections = [c for c, r in results.items() if not r.get('error')]
    total_rows = sum(len(r.get('rows', [])) for r in results.values())
    _run_cache = {'index': _lookup_index(results), 'ok': bool(ok_sections)}

    if ok_sections:
        # Watermark ONLY on a genuinely parsed scrape — never on a mere
        # attempt. That is the whole point: a metric a failed fetch cannot
        # satisfy. Judging freshness off the newest call instead would
        # false-alarm on a genuinely quiet week.
        write_watermark(sections_ok=ok_sections, rows=total_rows, log=log)
    else:
        log("  SPTulsian: no section could be read — watermark not written")
    return _run_cache['ok']


def scrape_spt_stock(stock_name, category, log=_clog):
    """Look up one tip's Type/Target/Timeframe/Have-Interest, from the cache
    primed by refresh_spt_data() (called lazily here if it has not run yet).

    Returns blanks rather than raising if anything fails: recording the
    recommendation and resolving its symbol matters more than the target, and
    a target can always be filled in later from the dashboard. The failure is
    still made loud — it is logged, and the liveness watermark is NOT written,
    which is what spt_watchdog.py alerts on. Never returns a stale-but-
    plausible-looking result on failure."""
    blank = {'type': '', 'target': '', 'timeframe': '', 'have_interest': '',
             'spt_market_price_at_call': None, 'spt_below_reco': None,
             'spt_direction': '', 'spt_rationale': ''}

    if not refresh_spt_data(log=log):
        return blank

    row = _run_cache['index'].get((_norm_name(category), _norm_name(stock_name)))
    if row is None:
        log(f"  SPTulsian: no call found for '{stock_name}' in '{category}'")
        return blank
    if row.get('closed'):
        # A call SPTulsian has already exited must not set a target on a
        # position being opened right now.
        log(f"  SPTulsian: only a CLOSED call found for '{stock_name}' — leaving target blank")
        return blank

    return {
        'type': '',  # cap type comes from AMFI via get_stock_cap_type(), not from here
        'target': row.get('target_price') or '',
        'timeframe': row.get('timeframe') or '',
        'have_interest': 'Have Interest' if row.get('have_interest') else 'No Interest',
        # Advisory context recorded alongside the target. Nothing scores on
        # these; they exist because scraped history cannot be backfilled.
        'spt_market_price_at_call': row.get('market_price_at_call'),
        'spt_below_reco': row.get('below_reco_flag'),
        'spt_direction': row.get('direction') or '',
        'spt_rationale': row.get('rationale') or '',
    }


def write_watermark(sections_ok, rows, path=None, log=_clog):
    """Record that a scrape genuinely succeeded. Read by spt_watchdog.py."""
    path = path or os.environ.get('SPT_WATERMARK_PATH', DEFAULT_WATERMARK_PATH)
    payload = {
        'last_success': datetime.now(timezone.utc).isoformat(),
        'sections_ok': list(sections_ok),
        'rows': rows,
    }
    try:
        with open(path, 'w') as f:
            json.dump(payload, f)
        log(f"  SPTulsian watermark written: {len(sections_ok)} section(s), {rows} row(s)")
    except OSError as e:
        log(f"  WARNING: could not write watermark to {path}: {e}")


def read_watermark(path=None):
    """Returns the watermark dict, or None if it has never been written."""
    path = path or os.environ.get('SPT_WATERMARK_PATH', DEFAULT_WATERMARK_PATH)
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def quit_spt_driver():
    """Retained so main_recommend.py's teardown call stays valid. Clears the
    per-run cache so a long-lived process re-logs-in on its next run."""
    global _run_cache
    _run_cache = None


# ── Lower-level capture API ──────────────────────────────────────────────

BASE_URL = 'https://www.sptulsian.com'
_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
_TOKEN_RE = re.compile(r'name="_token"[^>]*value="([^"]+)"')
# Each section's JSON controller has its own endpoint name (MW, FC, ...).
_ACTIVE_EP_RE = re.compile(r'/get([A-Za-z0-9]+?)ActiveData')
# Content-based field extraction for the HTML-rendered sections.
_TARGET_RE = re.compile(r'Target:\s*([\d,]+(?:\.\d+)?)\s*(?:\(([^)]*)\))?', re.I)
# Direction is captured, not assumed. This pattern hardcoded 'Buy', which meant
# a SELL row on an HTML section parsed as a buy with no price — and nothing
# recorded that the advisory had reversed. Every HTML section renders the call
# as "<Direction> @ <price> (<when>)" in its mobile cell, so one pattern reads
# direction, price and timestamp together and stays consistent across sections.
_CALL_RE = re.compile(
    r'\b(Buy|Sell)\b\s*@?\s*([\d,]+(?:\.\d+)?)\s*(?:\(([^)]*)\))?', re.I)
# Standalone direction cell, present on Short Term and Multibagger but not
# Medium Term. Used only as a fallback when the combined pattern misses.
_DIRECTION_CELL_RE = re.compile(r'^\s*(Buy|Sell)\s*$', re.I)
_INTEREST_RE = re.compile(r'\b(Have|No)\s+interest\b', re.I)
# Phrases SPTulsian uses when a call has actually been exited. Being listed
# under "Archives" is NOT itself proof of closure — Little Gems and Big Gems
# carry no active calls at all on this subscription, so even same-day calls
# appear there with no exit info. Only an exit remark means genuinely closed.
_EXIT_RE = re.compile(
    r'(target met|stop\s*loss|exited|booked|profit booked|loss booked|'
    r'sold (?:share|at|@)|partial exit)', re.I)

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
    no other module's traffic is routed through it (see SPTULSIAN_PROXY).

    Read from the environment at call time, not import time: config.py loads
    /home/ubuntu/.env, and depending on import order that can happen after
    this module is first imported."""
    proxy = os.environ.get('SPTULSIAN_PROXY', SPTULSIAN_PROXY)
    session = requests.Session()
    session.headers.update({'User-Agent': _UA})
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
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
         call_id=None, call_datetime='', buy_price=None, exit_remarks='',
         market_price_at_call=None, below_reco_flag=None, direction='',
         rationale=''):
    """One normalised call row — the single shape both strategies emit.

    'closed' is what callers should gate on, not 'source': a row is only
    treated as closed when SPTulsian left an exit remark on it. Sitting in
    the archive list is not enough (see _EXIT_RE).

    The last four fields were being received and thrown away:

      market_price_at_call  what the stock traded at when the call was made,
                            as distinct from buy_price, the price they told
                            you to pay. The gap between the two is the
                            advisory's own margin of safety and it varies —
                            BHEL was called at 434 into a 430.5 market, TD
                            Power at 741 into 752.7.
      below_reco_flag       a flag SPTulsian computes themselves.
      direction             'Buy' on everything seen so far, but a 'Sell'
                            would matter enormously and would currently be
                            parsed as a buy.
      rationale             their written reasoning. Plain text only for the
                            Medium Term section; Little Gems and Big Gems
                            ship it as a base64 PNG, so it is empty for the
                            sections covering almost every trade.

    They are captured now rather than when a use appears, because scraped
    history cannot be backfilled — the portal only shows what is live today.
    Nothing scores on them yet.
    """
    return {
        'stock_name': stock_name,
        'target_price': target_price,
        'timeframe': timeframe,
        'have_interest': have_interest,
        'source': source,            # 'active' | 'archive'
        'closed': bool((exit_remarks or '').strip()),
        'call_id': call_id,
        'call_datetime': call_datetime,
        'buy_price': buy_price,
        'exit_remarks': exit_remarks,
        'market_price_at_call': market_price_at_call,
        'below_reco_flag': below_reco_flag,
        'direction': direction,
        'rationale': rationale,
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
            market_price_at_call=_clean_float(raw.get(f'{pfx}stock_entry_price')),
            below_reco_flag=raw.get(f'{pfx}price_below_reco_price'),
            direction=(raw.get(f'{pfx}buy_sell') or '').strip(),
            # '{pfx}description' is a base64 PNG in these sections, not text —
            # ~110-160KB per call. Deliberately not carried: storing an image
            # under a field named 'rationale' would be worse than storing
            # nothing, because it reads as text everywhere downstream.
            rationale='',
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
        im = _INTEREST_RE.search(text)
        # Exit info lives in the archive table's Exit Remarks column, but the
        # cell index is not stable across rows, so read it out of the row text.
        exit_remarks = ''
        exit_span = None
        if source == 'archive':
            em = _EXIT_RE.search(text)
            if em:
                exit_remarks = text[em.start():em.start() + 90].strip()
                exit_span = (em.start(), em.start() + 90)
        # Take the first call match that does NOT sit inside the exit remark.
        # Now that the pattern matches Sell as well as Buy, a remark reading
        # "Exited: Sell @ 1,600" would otherwise be read as a sell call at the
        # exit price — inventing both a direction and an entry price out of the
        # way the position was closed. Skipping just that span, rather than
        # truncating the text at it, keeps direction on archived rows whose
        # remark happens to precede the call text.
        bm = None
        for m in _CALL_RE.finditer(text):
            if exit_span and exit_span[0] <= m.start() < exit_span[1]:
                continue
            bm = m
            break
        direction = _html_direction(cells, bm)
        rows.append(_row(
            stock_name=name,
            target_price=_clean_float(tm.group(1)),
            timeframe=(tm.group(2) or '').strip(),
            have_interest=bool(im and im.group(1).lower() == 'have'),
            source=source,
            # _CALL_RE groups: 1 = direction, 2 = price, 3 = when.
            call_datetime=((bm.group(3) or '').strip() if bm else ''),
            buy_price=(_clean_float(bm.group(2)) if bm else None),
            direction=direction,
            exit_remarks=exit_remarks,
            # These sections DO carry the reasoning as plain text — it is the
            # long free-text cell, distinguishable from the short structured
            # ones (name, prices, disclosure) by length alone. Deliberately
            # loose: it is captured for later analysis, not parsed, so a
            # missed or over-long capture costs nothing today.
            rationale=_html_rationale(cells),
        ))
    return rows


def _html_direction(cells, call_match):
    """Buy or Sell for one HTML row, or '' if the row does not say.

    Two sources, in order of trust:

      1. The combined "<Direction> @ <price>" text, which every HTML section
         renders in its mobile cell. This is also where the price comes from,
         so if it matched, its direction describes the same call.
      2. A standalone cell containing exactly "Buy" or "Sell". Short Term and
         Multibagger have one; Medium Term does not.

    Returns '' rather than guessing 'Buy'. A blank is treated downstream as
    "not stated" and does not block a purchase — most rows genuinely carry no
    direction — whereas defaulting to 'Buy' would turn a parse failure into a
    silent assertion that the advisory said buy.
    """
    if call_match and call_match.group(1):
        return call_match.group(1).strip().title()
    for td in cells:
        t = re.sub(r'\s+', ' ', td.get_text(' ', strip=True))
        m = _DIRECTION_CELL_RE.match(t)
        if m:
            return m.group(1).title()
    return ''


_RATIONALE_MIN_CHARS = 60
_RATIONALE_SKIP = re.compile(r'^(buy|sell|target|have interest|no interest|read more)\b', re.I)


def _html_rationale(cells):
    """Longest free-text cell in a row, if it looks like prose.

    Medium Term Investments renders the analyst's reasoning as a normal cell.
    Short Term and Multibagger have no such cell, and Little/Big Gems serve it
    as an image, so this returns '' far more often than not — that is expected,
    not a parse failure."""
    best = ''
    for td in cells:
        t = re.sub(r'\s+', ' ', td.get_text(' ', strip=True))
        t = re.sub(r'\s*Read More\s*$', '', t, flags=re.I).strip()
        if len(t) < _RATIONALE_MIN_CHARS or _RATIONALE_SKIP.match(t):
            continue
        # Structured cells are mostly digits and punctuation; prose is not.
        letters = sum(c.isalpha() or c.isspace() for c in t)
        if letters / len(t) < 0.75:
            continue
        if len(t) > len(best):
            best = t
    return best[:2000]


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
