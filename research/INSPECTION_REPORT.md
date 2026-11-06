# Repository inspection and live-baseline status

Inspection date: 2026-11-06 (Asia/Kolkata).
Source repository: `raramdas/sptulsianrepo`.
Inspected base commit: `be1cad03813ae9d5531f7a3292179fc6e3d74440`.

**Live baseline: blocked by Oracle server reachability.** No Oracle rows,
historical price series, sample sizes or new predictive findings have been
obtained. This report is a repository inspection, not an investment backtest.
Synthetic test fixtures must never be interpreted as portfolio observations.

## Findings from source code

1. The scheduled root application stores advisory and execution fields together
   in TRADES. `buy_date`/`recommended_price` differ from
   `my_buy_date`/`my_buy_price`. Operational statuses include unresolved symbols,
   pending fills, skipped calls and order failures, as well as Open/Closed.
   These cannot safely serve as research win/loss labels.
2. The multi-tenant provisioning template describes a separate RECOMMENDATIONS
   table with rec_id, and optional rec_id links from TRADES. The live scheduler
   uses the single-tenant root application. Therefore table existence and links
   must be inspected rather than assumed from the unused tenant template.
3. CONVICTION_SCORES is append-oriented and stores a model identifier plus
   scored_at. The lite and full engines have different inputs. No stable model
   version is described in the inspected schema; changes within lite may still
   mix regimes even after separating model names.
4. Current targets are mutable. Migration 005 adds advisory context but does
   not reconstruct target revision dates, original fundamentals or historical
   stop losses. Historical target hits cannot be inferred from today's target.
5. `backtest_conviction.py` rebuilds reduced scores and its `_as_of` filters
   financial-statement columns by period date. A fiscal period ending before
   an investment is not proof the statement was published before that investment.
   The new pipeline uses stored scores and their actual scoring dates instead.
6. README documents a prior inconclusive backtest. Its figures were not rerun
   or verified in this task and are not presented as new evidence.
7. The two committed CSVs are order-planning/reconciliation summaries. They
   lack recommendation dates, score histories and daily prices, so they cannot
   support a historical return or conviction-predictive baseline.
8. Oracle credentials and wallet are documented as server-local, outside Git.
   The local SSH key matches the user-provided fingerprint, but the documented
   IP and hostname timed out before authentication. No reconciliation or
   provisioning script was executed.

## Implemented scope

- A read-only, allowlisted Oracle exporter that inspects actual column metadata.
- An append-preserving analytical database separated into recommendations,
  scores, execution snapshots, ledger holdings, outcomes and target observations.
- Frozen, provenance-bearing adjusted price inputs and explicit missing-data flags.
- Fixed-horizon path metrics and stored-score diagnostic analysis, including
  symbol-level samples, model separation and uncertainty.
- Deterministic reports keyed by input hashes; no live trading imports.
- Offline regression tests and a dedicated CI step.

Validation completed: 14 offline research tests, 20 existing dashboard checks
and 10 existing write-operation mock checks passed. Research modules compile.
These checks use synthetic data and mocks; they do not validate connectivity,
live schema compatibility or actual price-provider coverage.

## Outstanding validation

1. Restore a reachable Oracle/SSH route, inspect actual live schema and data,
   and extract a transactionally consistent snapshot.
2. Reconcile source coverage and recommendation identities against the complete
   advisory archive; preserve skipped, unbought and delisted securities.
3. Review symbol mappings and acquire an independently verified session calendar
   plus adjusted historical prices, preferably a matching total-return benchmark.
4. Run the reproducible baseline, inspect missingness and sample concentration,
   and report supported findings or explicit insufficiency.
5. Validate source-specific execution fields and partial-fill semantics before
   treating ledger outcomes or quantities as broker-confirmed positions.

No source database schema, live orders, scheduled jobs, score thresholds or
existing trading behavior has been modified.
