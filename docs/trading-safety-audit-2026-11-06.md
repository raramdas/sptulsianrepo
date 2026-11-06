# Trading rule audit — 6 November 2026

## Failure and correction

A stale open ledger lot could repeatedly recreate a sell GTT despite the account
having no corresponding shares. A terminal trigger was treated as permission to
retry unless a sale could be found. Placement also substituted a synthetic last
price when the target had already been crossed.

The scheduled root entrypoints now require fresh broker inventory before a new
GTT, account for active GTT and outstanding sell reservations, and refuse missing
or crossed market prices. Intraday delivery activity or uncertain inventory is
held for reconciliation. Unconfirmed terminal triggers are **never automatically
recreated**. Existing active broker triggers are not cancelled by this patch.

## Rule checks

| Rule | Result / correction |
| --- | --- |
| Advisory SELL must not become a purchase | Existing end-to-end tests pass |
| Explicit Have Interest required | Existing sizing tests pass; recent excluded recommendations remain excluded |
| Score model matches sizing calibration | Query now restricts to `SIZING_MODEL` (`lite`) |
| Sizing thresholds | Below 69: no buy; 69–92: ₹10,000; above 92: ₹25,000; boundaries tested |
| Invalid scores/prices | Non-finite, missing or invalid inputs cannot size/place a new order |
| Category and individual-stock caps | Actual order estimate is checked against both; missing classification or Oracle errors block buying |
| Unfilled order budget | Category is held while `PENDING_FILL` exists, because legacy rows lack reliable reservation amounts |
| Buy retries | Only explicit CANCELLED/REJECTED with zero fill may requeue; unknown, open, partial and holdings-inferred outcomes remain pending |
| Fill accounting | Actual linked broker quantity/price required; no recommendation-price substitute when reconciling |
| Sell inventory | Fresh holdings, positions, orders and active GTTs checked; zero inventory blocks placement |
| Sell closure | Exact linked SELL/CNC symbol, exchange and full lot quantity; actual fill date; ambiguous updates roll back |
| Repeat closure | A GTT already attached to a closed row cannot close another lot |
| Failed sell trigger | Requires review; no symbol-only fallback and no automatic recreation |
| Dry runs | Scheduled buy/GTT dry runs do not write orders or trade records |
| Watchdog/self-heal | Existing isolated outcome and self-heal tests pass |

The existing rule allowing a minimum one-share purchase above the nominal sizing
band is unchanged; actual estimated cost must still fit budget and stock caps.
Market execution prices can differ from estimates.

## Reproduction

Offline, without credentials:

```
python3 tests/test_trading_safety.py
python3 tests/test_dashboard_logic.py
python3 tests/test_write_operations.py
```

Existing sizing, advisory SELL, fill-reconciliation, NaN, badge, watchdog and
self-heal tests were also run in an isolated temporary checkout with HTTP and
Oracle connection tripwires. Do not run the live trading entrypoints as tests.
The safety suite is included in GitHub Actions.

On the configured host, `python3 tools/audit_trading_state.py` performs broker
GET requests after login and an Oracle READ ONLY transaction. Keep its JSON
output private: it contains position and trigger information. The audit marks
conservative reconciliation candidates, not definitive instructions to cancel
all flagged orders. Exchange aliases, settlement and intraday activity can
require manual interpretation.

## Remaining operational work and scope

The audit found stale ledger positions and existing active triggers inconsistent
with current broker inventory. They require actual historical execution evidence
before changing quantities, exit dates or realised P&L. This patch does not invent
sales, close those rows, release their capital, or cancel broker triggers.
Unresolved pending orders can now hold a category until reviewed; this is
intentional and may reduce new purchases.

Scope is the root entrypoints used by the live cron and their `lib/` modules.
The unscheduled legacy `bot/` multi-tenant fork and `archive/` entrypoints are not
certified by this audit and must not be scheduled without porting these guards.
The audit is not a guarantee of every external failure mode: broker placement
and database recording are not one atomic transaction, and simultaneous manual
broker changes can race inventory reads. No live order was submitted to test
these fixes. Deploy only from Git with a clean fast-forward pull.
