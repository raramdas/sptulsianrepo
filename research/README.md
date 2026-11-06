# SPTulsian Historical Evidence Database

This is an isolated analytical store built from read-only Oracle snapshots.
It does not import the bot, place orders, change sizing, or mutate Oracle.
The evidence database is SQLite so analysis can be reproduced offline without
production access. There is deliberately no production schema migration.

## Status

The live Oracle extraction and baseline were run on 2026-11-06. See the
[inspection report](INSPECTION_REPORT.md). Generated portfolio data and findings
remain under ignored `research/private/`; they are intentionally not published
in this public repository. Tests cover both offline calculations and the
schema/LOB issues discovered during the live run.

## Reproduce

Run from the repository root. Oracle extraction needs the existing
`oracledb` and `python-dotenv` dependencies; optional price acquisition needs
`yfinance`. The analysis and tests use Python's standard library only.

```bash
mkdir -p research/private
python3 -m research.export_oracle --env-file /home/ubuntu/.env \
  --out research/private/oracle-2026-11-06.json

python3 -m research.fetch_prices \
  --mapping research/private/instruments.json \
  --calendar research/private/calendar.json \
  --exclusions research/private/identity-exclusions.json \
  --start 2020-01-01 --end 2026-11-07 \
  --out research/private/prices-2026-11-06.json

python3 -m research.evidence \
  --snapshot research/private/oracle-2026-11-06.json \
  --prices research/private/prices-2026-11-06.json \
  --as-of 2026-11-06 \
  --database research/private/evidence.sqlite \
  --out research/private/baseline-2026-11-06

python3 -m unittest discover -s tests/research -v
```

Use the last **completed** exchange session as the as-of date. The example dates
are illustrative; choose a history start covering the oldest recommendation.
If extraction runs on the VM, execute only the export module in a reviewed
checkout, then transfer its private JSON snapshot securely for local analysis.
Do not run provisioning or reconciliation scripts as part of research.

The exporter starts with `SET TRANSACTION READ ONLY`, inspects actual columns,
then selects only an explicit field allowlist from TRADES, RECOMMENDATIONS and
CONVICTION_SCORES plus the three KITE holdings/orders/GTT snapshot tables in
that same transaction. CLOB values are materialized before closing Oracle.
It rolls back and closes. Missing
optional tables are recorded; missing TRADES fails. It does not query credentials
or tenant configuration. Oracle schema synonyms are not auto-discovered: if
shared tables live elsewhere, inspect grants and add a reviewed mapping first.

Snapshot and price filenames cannot be overwritten. Report directories must
be new. Identical input JSON and parameters produce the same run ID and no
duplicate database records. A changed snapshot or price cache creates a new
run; previous rows are retained. Input hashes and methodology version are in
the report manifest. Keep the code commit with the inputs for reproduction.
Generated data under `research/private/` is ignored by Git: the repository is
public, so never publish private holdings, order IDs, snapshots, or live reports.

## Data separation

| Table | Meaning |
|---|---|
| source_records | Allowlisted source row payloads, unchanged within each snapshot |
| recommendations | Explicit recommendation rows, or flagged trade-derived observations |
| trade_links | Only source rec_id links; never fuzzy symbol/date guesses |
| model_scores | Every stored score, model and timestamp; HC research score remains NULL |
| executions | Ledger statuses and execution fields, kept out of research return labels |
| holdings | Snapshot ledger quantities, explicitly not broker-verified |
| realized_outcomes | Fully closed, quantity-matched ledger gross returns, separately flagged |
| target_observations | Target seen at extraction time; effective date unknown |
| price_history | Frozen adjusted-close observations with provenance in the run manifest |
| research_outcomes | Per-observation 30/90/180/365-calendar-day outcome and missing-data flags |
| score_outcomes | Forward outcomes starting after the first recorded score per recommendation/model |
| runs | Input hashes, parameters, capture date and methodology version |
| broker_snapshots | Broker snapshots retained by account and source ID, with sync timestamps; never treated as live holdings |

Repeated symbol/date rows are retained, flagged and not silently collapsed.
An explicit rec_id is authoritative. Without it, the trade remains a separate
observation, even if another record looks identical. A target change between
snapshots is evidence of an observed change, **not** proof of when the advisor
revised it. Initial targets and stop losses are never reconstructed from current
target values. Source status is not converted into investment success/failure.
Missing advisory direction is flagged and evaluated as a hypothetical long-only
return proxy; explicit sell calls are excluded from long-return analysis.
`DRY_RUN_TEST` records and reviewed name/ticker conflicts are excluded from
recommendation and score outcomes while their source rows are retained.
Broker snapshot age is reported explicitly. Accounts are never combined to
infer a trade fill; the root ledger has no proven account-level link.

## Price input contract

`instruments.json` maps each **verified source symbol** to a provider identity:

```json
{
  "BSE": {"provider_symbol": "BSE.NS", "identity_verified": true, "currency": "INR"},
  "^NSEI": {"provider_symbol": "^NSEI", "identity_verified": true, "currency": "INR"}
}
```

This is a format example, not a completed mapping for the portfolio. Review
exchange, company identity, name changes, mergers and delistings. Missing or
unverified identities remain missing; no automatic `.NS` suffix inference.
An optional exclusions file is a JSON object mapping recommendation IDs such as
`trade:123` to review reasons. This handles a source row whose name conflicts
with an otherwise valid ticker. It is embedded in the frozen price payload,
hashed with the inputs and applied to both recommendation and score outcomes.

`calendar.json` contains **all** exchange sessions covering the sample:

```json
{"source": "verified exchange calendar reference and retrieval date",
 "verified": true, "sessions": ["2026-11-02", "2026-11-03", "2026-11-04"]}
```

The short list above is only a format example. Do not infer the calendar from
one stock's available bars, which would hide suspensions and missing sessions.
Do not simply use weekdays: holidays and special sessions matter.

An alternate provider can supply the same price JSON contract directly:
`basis=split_dividend_adjusted_close`, `source`, `retrieved_at`,
`calendar_source`, `calendar_verified=true`, `sessions`, and `series` keyed by
source symbol. Each series has `identity_verified=true`, `currency=INR`, and
`bars` with `date` and positive finite `adjusted_close`. Additional raw/action
fields are retained in the frozen input. Provider fetch errors are explicit.

The Yahoo adapter explicitly disables automatic OHLC adjustment and extracts
`Adj Close`; it does not mix that series with raw recommended prices or targets.
It requests corporate actions and retains them for audit. Source:
[yfinance PriceHistory documentation](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html).

## Metrics and interpretation

Research entry is the next exchange session's adjusted close after the signal
date, with a maximum seven-calendar-day lag. Exit is the first exchange session
on/after entry plus the requested calendar-day horizon, also within seven days.
Every intervening exchange session must have a positive finite price. Incomplete
horizons are right-censored; missing bars, identities and adjustments are flagged
and excluded from denominators rather than filled forward or assigned zero.

- Return: adjusted end close / adjusted entry close − 1.
- Maximum gain: maximum close / entry close − 1, including the entry.
- Maximum adverse excursion: minimum close / entry close − 1.
- Maximum drawdown: minimum(close / running peak − 1). This is a path drawdown,
  distinct from loss against entry; it is **not portfolio maximum drawdown**.
- Milestones: first closing observation reaching 1.5×, 2×, 3× or 5× and calendar
  days since entry. Rates use complete fixed-horizon observations only.
- Win rate uses returns strictly above zero. Flats remain in the denominator.
  Empirical expectancy is mean return, including flats. Average winner/loser,
  median and payoff ratio are unavailable when their denominator is empty.
- Breakdowns cover category, observed advisory interest, observed cap class,
  and first-observed versus repeated symbol calls. These source attributes may
  have been edited after the recommendation: they are descriptive, not proven
  point-in-time predictors. Sector, valuation and fundamental analyses are
  withheld until dated evidence is available.
- First-observation-per-stock sensitivity selects each stock's first eligible
  record before checking outcome maturity, to expose repeated-lot weighting.

Gross ledger returns require Closed status, positive matching buy/sell quantities,
valid prices and ordered dates; future closes relative to as-of are excluded.
They omit costs/dividends and are not a substitute for broker-confirmed cashflow
returns. Holdings reflect the extraction snapshot, not a reconstructed as-of book.
Raw execution payloads retain details needed for later partial-fill accounting.

Predictive tests use first **stored** score per recommendation/model, with the
outcome starting after scoring. Late scores cannot explain earlier returns.
Scores before recommendation date, missing model, invalid score/date and future
scores are excluded. All originals are still preserved in model_scores.
There is no reconstruction using today's fundamentals or fiscal-period dates
as if they were publication dates. Subsequent score changes remain available
for a separate future landmark study.

Lite and full are never pooled. Within each model/horizon, select the first
scored observation per symbol **before** testing outcome completeness. This
prevents repeated lots from inflating sample size and prevents replacing a
missing early outcome with a surviving later one. Report Spearman correlation,
70+/80+/90+ overlapping threshold summaries, and earlier/later calendar cohorts.
At least ten symbols are required for a deterministic 1,000-resample symbol
bootstrap interval. This does not resolve sector dependence, multiple testing,
market regimes or absent model versioning. Cohorts are descriptive stability
checks, not a purged out-of-sample validation. No weights or cutoffs are optimized.

Default benchmark `^NSEI` is NIFTY 50 **price** index, not TRI. Dividend-adjusted
stock return minus this benchmark is explicitly not matched total-return alpha.
Use `--benchmark` with a reviewed total-return series for that comparison.

No recommendation, position size, buy policy or HC filter is validated by this
implementation. Build the evidence first; preserve the distinction between
an existing model score and a future independently validated research score.
