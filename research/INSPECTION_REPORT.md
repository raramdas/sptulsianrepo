# Repository inspection and live-baseline status

Inspection date: 2026-11-06 (Asia/Kolkata).
Source repository: `raramdas/sptulsianrepo`.
Inspected base commit: `be1cad03813ae9d5531f7a3292179fc6e3d74440`.

**Live baseline completed on 2026-11-06.** Oracle access was restored after
initial SSH timeouts. The actual schema was inspected and a read-only snapshot
was extracted. Historical adjusted prices were acquired using reviewed NSE
instrument identities and the official NSE session calendar. Private outputs
are kept under ignored `research/private/`; no portfolio rows or performance
figures are published in this public repository.

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
   IP and hostname initially timed out before authentication; the route later
   became reachable. No reconciliation or provisioning script was executed.

## Implemented scope

- A read-only, allowlisted Oracle exporter that inspects actual column metadata.
- An append-preserving analytical database separated into recommendations,
  scores, execution snapshots, ledger holdings, outcomes and target observations.
- Frozen, provenance-bearing adjusted price inputs and explicit missing-data flags.
- Fixed-horizon path metrics and stored-score diagnostic analysis, including
  symbol-level samples, model separation and uncertainty.
- Deterministic reports keyed by input hashes; no live trading imports.
- Offline regression tests and a dedicated CI step.

Validation completed: 17 offline research tests, 20 existing dashboard checks
and 10 existing write-operation mock checks passed. Research modules compile.
The tests use synthetic data and mocks. Separately, the exporter was exercised
against live Oracle and the baseline was built from actual frozen data.

## Live-schema findings

- The separate RECOMMENDATIONS table is empty and the live TRADES table lacks
  rec_id. The evidence base therefore uses explicitly flagged trade-derived
  observations, rather than claiming a complete advisory archive.
- Broker holdings, orders and GTT snapshot tables exist. They are captured
  separately by account with sync timestamps; stale snapshots are flagged.
- CLOB score evidence must be read before closing the Oracle connection.
  The exporter and regression test now enforce this.
- Name/ticker conflicts and test records are present in the source ledger.
  Reviewed exclusions apply to recommendation and predictive outcomes without
  editing or deleting source records.
- Some Closed rows lack exits or contain unequal buy/sell quantities. These
  are retained and flagged, with realized returns left unavailable.
- History is too short for 180/365-day recommendation outcomes or 90-day stored
  score outcomes. These remain right-censored rather than scored as failures.
- No source database DDL/DML was executed; all Oracle reads used read-only
  transactions. The separate local evidence database contains the results.

## Remaining evidence limitations

1. The complete historical SPTulsian archive is not represented in the live
   trade-derived sample. Longer histories, omitted recommendations and delisted
   names need separate verified sources before a multi-year strategy is tested.
2. Original target revision dates, historical valuation/fundamentals, stop losses
   and stable model version identifiers remain unavailable.
3. The default benchmark is a price index, not total return. The report labels
   this mismatch explicitly and makes no matched-alpha claim.
4. Gross ledger returns and broker snapshots do not constitute a reconciled
   cashflow-based portfolio backtest. Partial fills and account linkage need
   additional verified evidence.
5. Existing-score diagnostics are exploratory. No HC scoring weights, position
   sizes or investment policy were optimized or validated by this run.

No source database schema, live orders, scheduled jobs, score thresholds or
existing trading behavior has been modified.
