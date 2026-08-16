#!/usr/bin/env python3
"""
main_conviction.py — scores today's recommendations against public evidence
and records the result. Runs between the 9:30 recommend job and the 11:00
buy job, so the assessment is on the dashboard before any money moves.

DISPLAY ONLY. This writes to conviction_scores and nothing else. It does not
touch the trades table, does not size positions, and cannot stop a buy. The
score is there to inform the human during the review window, and to build a
track record that can be checked against outcomes before anyone considers
wiring it into sizing.

By default it scores today's PENDING_BUY and NEEDS_REVIEW trades. Pass
--all-open to score every open position instead (useful for a one-off
backfill).

Run directly:
    python3 main_conviction.py
    python3 main_conviction.py --all-open
"""
import sys
import json
import argparse
from datetime import datetime

from config import log, IST
import conviction

sys.path.insert(0, '/home/ubuntu/stockbot/dashboard')
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
    return db._df("""
        SELECT trade_id, symbol, stock_name, category_name, target_price
        FROM trades
        WHERE status IN ('PENDING_BUY', 'NEEDS_REVIEW')
          AND TRUNC(buy_date) = TRUNC(SYSDATE)
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
