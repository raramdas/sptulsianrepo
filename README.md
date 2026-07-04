# StockBot — Multi-Tenant Automated Stock Tip Trading System

Automates buying/selling Indian equities based on a daily SPTulsian advisory
email, via Zerodha Kite, with budget-aware allocation across categories and
market-cap types (Large/Mid/Small/Micro, per AMFI classification). Supports
multiple independent tenants, each with their own Kite account and fully
isolated Oracle database schema.

## Structure

```
stockbot/
├── bot/                    # Core automation (multi-tenant aware)
│   ├── config.py           # Shared config: Oracle admin conn, Gmail, constants
│   ├── crypto_utils.py     # Encrypt/decrypt tenant secrets (Fernet)
│   ├── tenant_manager.py   # Discovers active tenants, opens per-tenant connections
│   ├── kite_client.py      # Kite auth, symbol resolution, pricing, orders, GTT
│   ├── email_reader.py     # Parses the shared daily advisory email
│   ├── spt_scraper.py      # SPTulsian site scraping (currently disabled)
│   ├── order_status.py     # Kite order/holdings lookup
│   ├── budget_manager.py   # Category/stock-type budget checks (per-tenant)
│   ├── main.py             # Buy bot — loops over all active tenants
│   └── main_gtt_oracle.py  # Sell/GTT bot — loops over all active tenants
│
├── provisioning/           # Tenant onboarding (run manually, once per new tenant)
│   ├── setup_shared_schema.py      # One-time: creates tenant_config + recommendations
│   ├── tenant_schema_template.py   # DDL template for a single tenant's schema
│   ├── provision_tenant.py         # Creates a new tenant (interactive)
│   ├── provision_from_env.py       # Creates a new tenant using .env credentials
│   ├── generate_master_key.py      # One-time: generates the encryption master key
│   └── crypto_utils.py
│
├── dashboard/               # Streamlit dashboard ("Capital Ledger" design)
│   ├── app.py
│   ├── db.py
│   ├── theme.py
│   ├── requirements.txt
│   ├── run_dashboard.sh
│   ├── DEPLOYMENT_GUIDE.md  # Domain + Caddy HTTPS + systemd setup
│   └── .streamlit/config.toml
│
└── tests/                   # Logic validated against SQLite mocks
    ├── test_dashboard_logic.py
    └── test_write_operations.py
```

## Architecture

**Multi-tenant isolation: schema-per-tenant, not row-level.** Each tenant gets
their own Oracle DB user/schema (`trades`, `portfolio_budget`,
`category_allocation`, budget views) — no `tenant_id` column anywhere. A bug
or bad query for one tenant cannot structurally reach another tenant's data,
enforced by the database engine itself.

**Shared data** (one ADMIN schema): `tenant_config` (encrypted credentials
per tenant), `recommendations` (planned — shared advisory feed),
`stock_cap_classification` (AMFI market-cap reference data, refreshed every
~6 months).

**Secrets**: every tenant's Kite login, Kite TOTP secret, and own DB password
are Fernet-encrypted in `tenant_config`. The one master key lives only in
`.env` on the server — never in the database, never in this repo.

## Setup (new environment)

```bash
# 1. One-time: generate the master encryption key
python3 provisioning/generate_master_key.py
# add the printed key to .env as MASTER_ENCRYPTION_KEY=...

# 2. One-time: create the shared/control schema
python3 provisioning/setup_shared_schema.py

# 3. Provision each tenant
python3 provisioning/provision_from_env.py "Tenant Name" --budget 200000

# 4. Run the bots (loops over all active tenants automatically)
python3 bot/main.py            # buy bot, ~9:20 AM
python3 bot/main_gtt_oracle.py # GTT/sell bot, ~4:00 PM

# 5. Dashboard
cd dashboard && pip3 install -r requirements.txt
streamlit run app.py
```

## Required `.env` variables

```
KITE_API_KEY=...
KITE_API_SECRET=...
GMAIL_USER=...
GMAIL_APP_PASSWORD=...
ORACLE_USER=...              # ADMIN schema (shared/control)
ORACLE_PASSWORD=...
ORACLE_DSN=...
ORACLE_WALLET_DIR=...
ORACLE_WALLET_PASSWORD=...
MASTER_ENCRYPTION_KEY=...    # from generate_master_key.py
DASH_USERS=user:pass,...     # dashboard login
```

Per-tenant Kite/DB credentials are NOT in `.env` — they live encrypted in
`tenant_config`, added via the provisioning scripts.
