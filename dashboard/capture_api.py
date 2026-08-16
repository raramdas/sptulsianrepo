#!/usr/bin/env python3
"""
capture_api.py — tiny stdlib-only HTTP service that lets spt_capture.py
(run by hand from a laptop that isn't CloudFront-blocked) save SPTulsian
target/timeframe/have-interest data into the same `trades` table the Set
Targets page reads and writes.

Deliberately not Flask/FastAPI — this is two endpoints, single caller,
low volume, and avoiding a new dependency on the production VM kept the
footprint small. http.server's ThreadingHTTPServer is plenty for this.

Endpoints (both require header 'X-API-Key: <CAPTURE_API_KEY>'):
  POST /api/spt-capture/preview
      body: {"results": {<category>: {"rows": [...], "error": str|None}, ...}}
      (this is exactly what spt_scraper.scrape_all_categories() returns)
      -> {"matches": [...], "unmatched_trades": [...], "category_errors": {...}}
      No DB writes.

  POST /api/spt-capture/apply
      body: {"updates": [{"trade_id":, "target_price":, "have_interest":, "timeframe":}, ...]}
      -> {"updated": N, "errors": [...]}
      Calls db.update_trade_target() — the exact function the Set Targets
      page's Save button calls.

Run directly:
    python3 capture_api.py
"""
import os
import re
import json
import datetime
import pandas as pd
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')
import db  # noqa: E402  (after dotenv load, matches db.py's own pattern)

CAPTURE_API_KEY = os.environ['CAPTURE_API_KEY']
PORT = int(os.environ.get('CAPTURE_API_PORT', '8600'))

HAVE_INTEREST_TRUE = 'Have Interest'
HAVE_INTEREST_FALSE = 'No Interest'


def _norm(s):
    return re.sub(r'\s+', ' ', (s or '').strip().upper())


def _log(msg):
    ts = datetime.datetime.now().strftime('%H:%M:%S')
    print(f"[{ts}] {msg}", flush=True)


def build_matches(results):
    """results: {category: {rows: [...], error: str|None}} as produced by
    spt_scraper.scrape_all_categories(). Returns (matches, unmatched_trades,
    category_errors)."""
    category_errors = {cat: r['error'] for cat, r in results.items() if r.get('error')}

    # Flatten all scraped rows across categories, one row per stock.
    #
    # A LIVE call always beats an archived one. Most rows on the HTML-rendered
    # sections are closed calls (e.g. 40 of 41 on Medium Term Investments), and
    # letting a closed call win would write its stale target onto an open
    # position — i.e. arm a GTT at the wrong price. Within the same source,
    # first-seen wins, and both strategies emit newest-first.
    scraped_by_stock = {}
    for cat, r in results.items():
        for row in r.get('rows', []):
            key = _norm(row['stock_name'])
            if not key:
                continue
            existing = scraped_by_stock.get(key)
            if existing is None:
                scraped_by_stock[key] = row
            elif existing.get('source') != 'active' and row.get('source') == 'active':
                scraped_by_stock[key] = row

    open_trades = db.open_trades_for_capture()

    matches = []
    unmatched_trades = []
    for _, t in open_trades.iterrows():
        key = _norm(t['stock_name'])
        row = scraped_by_stock.get(key)
        confidence = 'exact'
        if row is None:
            # weak fallback: substring containment either direction
            for skey, srow in scraped_by_stock.items():
                if key and (key in skey or skey in key):
                    row = srow
                    confidence = 'fuzzy'
                    break
        if row is None:
            unmatched_trades.append({
                'trade_id': int(t['trade_id']), 'stock_name': t['stock_name'],
                'category_name': t['category_name'], 'buy_date': str(t['buy_date']),
            })
            continue

        old_target = float(t['target_price']) if pd.notna(t['target_price']) else None
        new_target = row['target_price']
        old_interest = t['have_interest'] if pd.notna(t['have_interest']) else ''
        new_interest = HAVE_INTEREST_TRUE if row['have_interest'] else HAVE_INTEREST_FALSE
        old_timeframe = t['timeframe'] if pd.notna(t['timeframe']) else ''
        new_timeframe = row['timeframe'] or ''

        changed = (old_target != new_target) or (old_interest != new_interest) or (old_timeframe != new_timeframe)

        matches.append({
            'trade_id': int(t['trade_id']),
            'stock_name': t['stock_name'],
            'category_name': t['category_name'],
            'buy_date': str(t['buy_date']),
            'match_confidence': confidence,
            'spt_call_datetime': row.get('call_datetime', ''),
            'spt_source': row.get('source', 'unknown'),
            'spt_exit_remarks': row.get('exit_remarks', ''),
            'old_target': old_target, 'new_target': new_target,
            'old_have_interest': old_interest, 'new_have_interest': new_interest,
            'old_timeframe': old_timeframe, 'new_timeframe': new_timeframe,
            'changed': changed,
        })

    return matches, unmatched_trades, category_errors


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        return self.headers.get('X-API-Key') == CAPTURE_API_KEY

    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length) if length else b'{}'
        return json.loads(raw or b'{}')

    def do_POST(self):
        if not self._authorized():
            self._send_json(401, {'error': 'unauthorized'})
            return
        try:
            body = self._read_json_body()
        except Exception as e:
            self._send_json(400, {'error': f'bad json: {e}'})
            return

        if self.path == '/api/spt-capture/preview':
            try:
                matches, unmatched, cat_errors = build_matches(body.get('results', {}))
                self._send_json(200, {
                    'matches': matches, 'unmatched_trades': unmatched,
                    'category_errors': cat_errors,
                })
                _log(f"preview: {len(matches)} matched, {len(unmatched)} unmatched")
            except Exception as e:
                _log(f"preview ERROR: {e}")
                self._send_json(500, {'error': str(e)})

        elif self.path == '/api/spt-capture/apply':
            updates = body.get('updates', [])
            updated, errors = 0, []
            for u in updates:
                try:
                    db.update_trade_target(u['trade_id'], u.get('target_price'),
                                            u.get('have_interest'), u.get('timeframe'))
                    updated += 1
                except Exception as e:
                    errors.append({'trade_id': u.get('trade_id'), 'error': str(e)})
            _log(f"apply: {updated} updated, {len(errors)} error(s)")
            self._send_json(200, {'updated': updated, 'errors': errors})

        else:
            self._send_json(404, {'error': 'not found'})

    def log_message(self, fmt, *args):
        _log(fmt % args)


def run():
    server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    _log(f"capture_api listening on 127.0.0.1:{PORT}")
    server.serve_forever()


if __name__ == '__main__':
    run()
