#!/usr/bin/env python3
"""
main_conviction.py — scores today's recommendations against public evidence
and records the result. Runs between the 9:30 recommend job and the 11:00
buy job, so the assessment is on the dashboard before any money moves.

Two engines, selected with --engine:

    lite (default)  lib/conviction_lite.py — momentum, trend, upside to the
                    advisory target, liquidity. One network call per symbol,
                    under a second, no missing-filing holes. This is what new
                    recommendations get.
    full            lib/conviction.py — the four-layer fundamentals engine.
                    Slower and gappier; kept for depth on a specific name.

Scores from the two are NOT comparable and are stored with a `model` column
so the track record can tell them apart. Do not pool them in a backtest.

DISPLAY ONLY, again. This score briefly sized and gated real orders
(2026-08-25 to 2026-08-26). The first backtest found no relationship between
the full engine's composite and subsequent excess return — symbol-level
Spearman -0.127 over 37 symbols — so sizing reverted to a flat amount and the
score is informational: shown on the dashboard, recorded for the track
record, and not consulted by the buy path. The lite engine is a fresh
hypothesis and is equally unvalidated; it starts display-only too.

It writes conviction_scores and nothing else. If this job fails, buying is
unaffected; only visibility is lost. See lib/config.CONVICTION_SIZING_ENABLED
and backtest_conviction.py before wiring it back into sizing.

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
from lib import conviction, conviction_lite

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


def has_model_column(conn):
    """The `model` column distinguishes lite from full scores. It is added by
    a migration; if this runs against a database that has not had it applied
    yet, fall back rather than failing the whole job."""
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) FROM user_tab_columns
                   WHERE table_name = 'CONVICTION_SCORES' AND column_name = 'MODEL'""")
    return cur.fetchone()[0] > 0


def has_column(conn, name):
    cur = conn.cursor()
    cur.execute("""SELECT COUNT(*) FROM user_tab_columns
                   WHERE table_name = 'CONVICTION_SCORES' AND column_name = :c""",
                {'c': name.upper()})
    return cur.fetchone()[0] > 0


def save_score(conn, trade, result, with_model=True, with_z=True):
    cur = conn.cursor()
    cols = ("trade_id, symbol, stock_name, category_name, score, evidence_pct, "
            "tier, verdict, sector, reasons, warnings, layers_json")
    vals = (":trade_id, :symbol, :stock_name, :category_name, :score, :evidence_pct, "
            ":tier, :verdict, :sector, :reasons, :warnings, :layers_json")
    if with_model:
        cols += ", model"
        vals += ", :model"
    if with_z:
        cols += ", reach_z"
        vals += ", :reach_z"
    cur.execute(f"INSERT INTO conviction_scores ({cols}) VALUES ({vals})", {
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
        **({'model': result.get('model', 'full')} if with_model else {}),
        **({'reach_z': result.get('reach_z')} if with_z else {}),
    })


ENGINES = {'lite': conviction_lite, 'full': conviction}


def run(all_open=False, engine='lite', dry_run=False):
    mod = ENGINES[engine]
    log(f"=== Conviction scoring starting — engine={engine} "
        f"(display only — no orders affected) ===")
    df = trades_to_score(all_open=all_open)
    if df.empty:
        log("Nothing to score.")
        return

    log(f"Scoring {len(df)} trade(s)...")
    conn = None if dry_run else db.get_connection()
    with_model = has_model_column(conn) if conn is not None else False
    with_z = has_column(conn, 'reach_z') if conn is not None else False
    if conn is not None and not with_model:
        log("  NOTE: conviction_scores has no `model` column — storing without it. "
            "Apply the migration so lite and full scores stay distinguishable.")
    if conn is not None and not with_z:
        log("  NOTE: conviction_scores has no `reach_z` column — storing without it. "
            "Apply migrations/004 so the first-passage score can be validated later.")
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
                result = mod.score_symbol(sym, spt_target=target, log=log)
                if conn is not None:
                    save_score(conn, t, result, with_model=with_model, with_z=with_z)
                scored += 1
                score_s = 'n/a' if result['score'] is None else f"{result['score']:.0f}"
                log(f"  #{int(t['trade_id'])} {sym:14s} score={score_s:>4s} "
                    f"evidence={result['evidence_pct']:.0f} {result['tier']:>3s} "
                    f"-> {result['verdict']}")
                for r in result['reasons']:
                    log(f"       ! {r}")
                for w in result.get('warnings', []):
                    log(f"       ~ {w}")
            except Exception as e:
                # One bad symbol must not lose the rest of the run.
                failed += 1
                log(f"  #{int(t['trade_id'])} {sym}: scoring FAILED — {type(e).__name__}: {e}")
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()

    tail = " (dry run — nothing written)" if dry_run else ""
    log(f"=== Conviction scoring complete: {scored} scored, {failed} failed{tail} ===")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--all-open', action='store_true',
                    help="Score every open position, not just today's recommendations")
    ap.add_argument('--engine', choices=sorted(ENGINES), default='lite',
                    help="Scoring engine (default: lite)")
    ap.add_argument('--dry-run', action='store_true',
                    help="Score and print, but write nothing")
    args = ap.parse_args()
    run(all_open=args.all_open, engine=args.engine, dry_run=args.dry_run)
