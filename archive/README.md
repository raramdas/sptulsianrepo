# archive/

Superseded and one-off scripts, kept for reference and occasional recovery
work. **Nothing here is scheduled** — see the crontab table in the root
[README](../README.md) for what actually runs.

Run them as modules from the repository root, so their `lib.*` imports
resolve:

```bash
python3 -m archive.retry_failed_gtt_placements
```

| Script | Status |
|---|---|
| `main_gtt.py` | Superseded by `main_gtt_oracle.py` (2026-08-01), which reads Oracle rather than the Sheet |
| `sheet_ingest_bot.py`, `sheet_gtt_updater.py` | Google Sheets era, before Oracle became the source of truth |
| `purchase_bot.py`, `gtt_lifecycle_bot.py` | Earlier monolithic buy/sell bots, replaced by the three-phase split |
| `kite_common.py` | Shared helpers for the scripts above |
| `create_missing_gtts.py` | One-off: place GTTs for trades that missed them |
| `retry_failed_gtt_placements.py` | One-off recovery after the last_price/trigger_price fix |
| `retry_needs_review_buys.py` | One-off: re-attempt NEEDS_REVIEW buys, now handled in the dashboard |
| `crypto_utils.py` | Fernet helpers for the multi-tenant `bot/` tree |
| `test_tenant_dry_run.py` | Multi-tenant dry-run harness |

These import shared modules from `lib/`, so they stay working as that code
evolves — but they are not exercised by any test and have not been run
recently. Read before trusting.
