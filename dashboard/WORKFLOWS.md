# Workflow restoration and GTT reconciliation

## Where to find the old functionality

| Workflow | New app location |
|---|---|
| Confirm stock symbol | Needs attention → Confirm stock symbols; also More and each unresolved trade |
| Check company/classification | More → Company & classification lookup |
| Detailed model evidence/history | Open an idea/trade → View score evidence & history |
| Holdings/GTT reconciliation | Needs attention or More → Reconcile holdings & sell triggers → Check now |
| Individual target edit | Open an open trade → Edit target |
| Bulk target editor | More → Bulk target editor (opens the existing advanced page directly) |
| Category / stock-type drill-down | More → Category breakdown (advanced page) |
| Category allocations and manual historical close | More → Allocation settings & record corrections (advanced page) |
| Compare all conviction scores | More → Compare all conviction scores (advanced page) |
| Holdings/trades export, broker orders/GTTs, performance, total budget | Portfolio, Activity and More |

Symbol confirmation is a two-step user action. The preview checks the exact NSE
equity entry in Kite's current instrument file and displays the company name.
A signed, session-bound, ten-minute preview is required to confirm. The save only
resolves the identity and returns a NEEDS_REVIEW record to normal scheduled gates;
it does not place an order. Scores attached to a different ticker cannot size a
newly resolved record. Preview and all tests leave actual records untouched.

## Read-only reconciliation

The explicit Check now action reads holdings, positions, pending orders and GTTs
from each account, then compares them with the ledger. It does not refresh the
legacy snapshot tables or change broker orders. Failed account reads are reported
as unavailable, never zero holdings. The report can be downloaded as JSON.

Exact cash-equity symbols can match holdings across NSE and BSE: the displayed
exchange does not split demat inventory. Series-suffix aliases require review;
they are not treated as orphan orders. Duplicate holding entries are rejected by
the placement guard until identity is reconciled. OCO branches reserve their
maximum quantity once. Intraday activity, settlement, discrepancies, excess
reservations, missing targets and stale ledger quantities remain explicit flags.

Addition candidates only carry forward recorded targets where the NEW account's
aggregate open ledger quantity matches holdings and linked GTT quantities agree.
The report does not assign NEW ledger targets to the OLD personal account, invent
missing targets, close unmatched lots or guess which excess trigger to remove.
A trigger below a quote is an intent review (it may be a stop-loss), not an
automatic cancellation recommendation. Broker add/edit/delete actions remain
user actions in Kite; this app has no one-click execution endpoint.

## Tests

```
python3 tests/test_trading_safety.py
python3 tests/test_gtt_reconciliation.py
python3 dashboard/mobile_tests/test_app.py
```

Synthetic browser tests are in `dashboard/mobile_tests/browser_workflows.py`.
Run against a fresh preview on port 8767. For the phone variant, use a fresh
preview on 8768 and set `MOBILE_QA=1 QA_URL=http://127.0.0.1:8768`.

Sources: [Zerodha's display-exchange explanation](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-holdings/articles/default-exchange-on-kite),
[Kite GTT/OCO documentation](https://kite.trade/docs/connect/v3/gtt/).
