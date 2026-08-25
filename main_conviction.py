#!/usr/bin/env python3
"""
main_conviction.py — scores today's recommendations against public evidence
and records the result. Runs between the 9:30 recommend job and the 11:00
buy job, so the assessment is on the dashboard before any money moves.

THIS SCORE NOW MOVES MONEY. It was display-only until 2026-08-25; the buy
path (main.py) now reads it to decide both WHETHER to buy and HOW MUCH:

    score > 85        Rs 25,000
    75 <= score <= 85 Rs 10,000
    score < 75        not bought
    no score at all   not bought

So a scoring bug, a data-source outage, or a silently wrong metric is a
money bug, not a cosmetic one. This job still only writes conviction_scores
— it never touches trades — but main.py will refuse to buy anything this
job failed to score. If it does not run, nothing is bought that day.

By default it scores trades awaiting purchase (PENDING_BUY, NEEDS_REVIEW,
PENDING_FILL) from the last few days, so retries are re-scored rather than
sized on a stale number. Pass --all-open to score every open position
instead (useful for a one-off backfill).

Run directly:
    python3 main_conviction.py
    python3 main_conviction.py --all-open
"""
import os
import sys
import json
import argparse
from datetime import datetime

from lib.config import log, IST
from lib import conviction

# The dashboard package owns the Oracle access layer. On the VM it is deployed
# to its own directory; in a checkout it sits alongside this file. Try the
# local copy first so the script is runnable (and testable) off the VM.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _candidate in (os.path.join(_HERE, 'dashboard'), '/home/ubuntu/stockbot/dashboard'):
    if os.path.isdir(_candidate):
        sys.path.insert(0, _candidate)
        break
import db  # noqa: E402


def trades_to_score(all_open=False):
    """Today's freshly-recommended trades, or every open position."""
    if all_open:
        return db._df("""
            SELECT trade_id, symbol, stock_name, category_name, target_price
            FROM trades
            WHERE status = 'Open' AND symbol IS NOT NULL
            ORDER BY trade_id DESC
        """)
    # Includes trades requeued for a retry, whose buy_date is the ORIGINAL
    # recommendation date — a today-only filter would miss them, and the buy
    # run sizes on this score, so a stale one would size a real position.
    return db._df("""
        SELECT trade_id, symbol, stock_name, category_name, target_price
        FROM trades
        WHERE status IN ('PENDING_BUY', 'NEEDS_REVIEW', 'PENDING_FILL')
          AND buy_date >= TRUNC(SYSDATE) - 4
        ORDER BY trade_id DESC
    """)


def save_score(conn, trade, result):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO conviction_scores
            (trade_id, symbol, stock_name, category_name, score, evidence_pct,
             tier, verdict, sector, reasons, warnings, layers_json)
        VALUES
            (:trade_id, :symbol, :stock_name, :category_name, :score, :evidence_pct,
             :tier, :verdict, :sector, :reasons, :warnings, :layers_json)
    """, {
        'trade_id': int(trade['trade_id']),
        'symbol': trade['symbol'],
        'stock_name': trade['stock_name'],
        'category_name': trade['category_name'],
        'score': result['score'],
        'evidence_pct': result['evidence_pct'],
        'tier': result['tier'],
        'verdict': result['verdict'],
        'sector': result.get('sector'),
        'reasons': json.dumps(result['reasons']),
        'warnings': json.dumps(result.get('warnings', [])),
        # Store the full working, so the dashboard can show why a score is
        # what it is rather than asking the reader to trust a number.
        'layers_json': json.dumps(result['layers'], default=str),
    })


def run(all_open=False):
    log("=== Conviction scoring starting (display only — no orders affected) ===")
    df = trades_to_score(all_open=all_open)
    if df.empty:
        log("Nothing to score.")
        return

    log(f"Scoring {len(df)} trade(s)...")
    conn = db.get_connection()
    scored = failed = 0
    try:
        for _, t in df.iterrows():
            sym = (t['symbol'] or '').strip()
            if not sym:
                continue
            try:
                target = float(t['target_price']) if t['target_price'] is not None else None
            except (TypeError, ValueError):
                target = None
            try:
                result = conviction.score_symbol(sym, spt_target=target, log=log)
                save_score(conn, t, result)
                scored += 1
                score_s = 'n/a' if result['score'] is None else f"{result['score']:.0f}"
                log(f"  #{int(t['trade_id'])} {sym:14s} score={score_s:>4s} "
                    f"evidence={result['evidence_pct']:.0f} {result['tier']:>3s} "
                    f"-> {result['verdict']}")
                for r in result['reasons']:
                    log(f"       ! {r}")
            except Exception as e:
                # One bad symbol must not lose the rest of the run.
                failed += 1
                log(f"  #{int(t['trade_id'])} {sym}: scoring FAILED — {type(e).__name__}: {e}")
        conn.commit()
    finally:
        conn.close()

    log(f"=== Conviction scoring complete: {scored} scored, {failed} failed ===")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--all-open', action='store_true',
                    help="Score every open position, not just today's recommendations")
    args = ap.parse_args()
    run(all_open=args.all_open)
