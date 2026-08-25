# Stockbot / Capital Ledger

Automates trading Indian equities on SPTulsian advisory calls, via Zerodha
Kite, with budget-aware position sizing and a full ledger of what it did.

📄 **[ARCHITECTURE.md](ARCHITECTURE.md)** ([PDF](ARCHITECTURE.pdf)) — functional
and technical design, failure handling, and the reasoning behind the
non-obvious parts. Read that before changing anything load-bearing.

---

## How it runs

Five scheduled jobs on the VM. All cron times are **UTC**; the VM clock is UTC
and IST is UTC+5:30.

| Job | UTC | IST | What it does |
|---|---|---|---|
| `main_recommend.py` | `0 4` | 09:30 | Parse the advisory email, resolve symbols, scrape targets. **No orders.** |
| `main_conviction.py` | `45 4` | 10:15 | Score new recommendations on public evidence. Display only. |
| `spt_watchdog.py` | `15 5` | 10:45 | Alarm if the scraper has gone dark |
| `main.py` | `30 5` | 11:00 | Price, size against budget, place real buy orders |
| `main_gtt_oracle.py` | `30 10` | 16:00 | Place GTT sells; close trades on confirmed fills |

The 90-minute gap between recommend and buy is deliberate: it is the window in
which a human can fix a mis-resolved ticker **before** money moves. A symbol the
bot cannot resolve confidently becomes `NEEDS_REVIEW` and is never bought on a
guess.

Run on demand:

```bash
python3 main_conviction.py --all-open   # re-score every open position
python3 spt_capture.py --dry-run        # preview scraped targets, then save
python3 spt_watchdog.py --check-only    # health check, never sends mail
```

## Layout

Only entrypoints live at the repository root — one file per thing you can
run. Everything they share is in `lib/`, and superseded scripts are in
`archive/`.

```
.
├── main_recommend.py      # Phase 1: symbols + targets, no orders     ← cron
├── main.py                # Phase 2: price, size, buy                 ← cron
├── main_gtt_oracle.py     # Phase 3: GTT sells, confirm fills, close  ← cron
├── spt_watchdog.py        # Liveness alarm for the scraper            ← cron
├── main_conviction.py     # Evidence scoring (display only)
├── spt_capture.py         # Manual review-then-save of scraped targets
│
├── lib/                   # shared modules
│   ├── config.py          #   env, credentials, KITE_ACCOUNT switch
│   ├── kite_client.py     #   Kite auth, symbol resolution, orders, GTTs
│   ├── order_status.py    #   order lookup, sell reconciliation
│   ├── email_reader.py    #   Gmail IMAP -> tips
│   ├── budget_manager.py  #   budget policy; all TRADES reads/writes
│   ├── spt_scraper.py     #   portal login + two parsing strategies
│   ├── conviction.py      #   four-layer scoring engine
│   └── sheet_logger.py    #   Google Sheets mirror (legacy)
│
├── dashboard/             # Streamlit UI ("Capital Ledger")
│   ├── app.py             #   pages
│   ├── db.py              #   Oracle access
│   ├── kite_data.py       #   multi-account sync, retry-buy
│   ├── capture_api.py     #   authenticated endpoint for spt_capture.py
│   └── theme.py           #   CSS design system
│
├── archive/               # superseded / one-off — see archive/README.md
├── bot/                   # multi-tenant variant — NOT scheduled
├── provisioning/          # tenant onboarding for the bot/ tree
└── tests/                 # logic validated against SQLite mocks
```

Entrypoints stay at the root deliberately: cron invokes them by bare filename
(`cd /home/ubuntu/stock_bot_v4 && python3 main_recommend.py`), so moving them
would mean editing the crontab on a live system for no benefit.

`bot/` is a multi-tenant version (schema-per-tenant, encrypted per-tenant
credentials) that is not in the live crontab and keeps its own module copies —
it does **not** import from `lib/`. The scheduled system is the single-tenant
root tree. Keep the two in sync when changing shared logic, or they will
silently diverge.

## Deploy

Two directories on the VM track the same repo: `/home/ubuntu/stock_bot_v4`
(cron) and `/home/ubuntu/stockbot` (dashboard).

```bash
git add -A && git commit && git push
ssh -i ~/.ssh/kite_key ubuntu@140.245.226.35 '
  cd /home/ubuntu/stock_bot_v4 && git pull -q
  cd /home/ubuntu/stockbot     && git pull -q
  sudo systemctl restart stockbot-dashboard'
```

## Health check

```bash
ssh -i ~/.ssh/kite_key ubuntu@140.245.226.35 '
  curl -s ifconfig.me; echo                                    # MUST be 140.245.226.35
  curl -s --socks5-hostname 127.0.0.1:40000 ifconfig.me; echo  # MUST be Cloudflare
  sudo systemctl is-active warp-svc stockbot-dashboard stockbot-capture-api
  cd /home/ubuntu/stock_bot_v4 && python3 spt_watchdog.py --check-only'
```

**If the first command ever returns a Cloudflare address, stop.** The scraper's
proxy has escaped its scope and broker traffic is no longer leaving from the
IP Zerodha expects. See [ARCHITECTURE.md §3.2](ARCHITECTURE.md).

## Network egress

`sptulsian.com` blocks datacenter IPs at the CloudFront edge, so the VM cannot
reach it directly. Scraper traffic — and only scraper traffic — goes through a
Cloudflare WARP SOCKS5 proxy on `127.0.0.1:40000`, attached to a single
`requests.Session`. Broker traffic must keep egressing from the registered
static IP, because Zerodha binds to it.

The env var is deliberately named `SPTULSIAN_PROXY` rather than
`HTTP_PROXY`/`HTTPS_PROXY`: `requests` honours the standard names
automatically for every module in the process, which would silently route
order placement through the proxy.

## `.env`

Lives at `/home/ubuntu/.env` on the VM, `export KEY=value` format. Never in the
repo.

```
# Kite / broker
KITE_API_KEY=            KITE_API_SECRET=
ZERODHA_USER_ID=         ZERODHA_PASSWORD=       ZERODHA_TOTP_SECRET=
ZERODHA_OLD_USER_ID=     ZERODHA_OLD_PASSWORD=   ZERODHA_OLD_TOTP_SECRET=

# Advisory
GMAIL_USER=              GMAIL_APP_PASSWORD=
SPT_USERNAME=            SPT_PASSWORD=
SPTULSIAN_PROXY=socks5h://127.0.0.1:40000

# Oracle
ORACLE_USER=             ORACLE_PASSWORD=        ORACLE_DSN=
ORACLE_WALLET_DIR=       ORACLE_WALLET_PASSWORD=

# Dashboard + capture API
DASH_USERS=user:pass,...
DASH_SESSION_SECRET=     # 64-char hex; signs the "remember me" cookie
CAPTURE_API_KEY=         CAPTURE_API_URL=
```

`KITE_ACCOUNT=OLD` on a one-off run targets the pre-cutover personal Zerodha
account instead of the automation account. Cron never sets it, so scheduled
jobs always use the new account.

```bash
KITE_ACCOUNT=OLD python3 main_gtt_oracle.py
```

## Working on this safely

The invariants below hold by construction. Preserve them.

1. Broker traffic egresses from the registered static IP. Always.
2. A symbol that does not resolve cleanly is never bought — it becomes
   `NEEDS_REVIEW` and waits for a human.
3. A trade is `Closed` only on a **confirmed** sell fill, never on GTT status
   alone.
4. Conviction scores DO size and gate orders (since 2026-08-25): >85 buys
   Rs 25k, 75-85 buys Rs 10k, below 75 or unscored is not bought. A
   scoring bug is therefore a money bug. This reversed the engine's
   original display-only status deliberately.
5. Manual buy paths preview before they commit; the confirm step is the only
   thing that spends money.

Anything touching money or the ledger gets exercised first in an isolated
directory with Oracle writes, Sheet writes, and order placement monkey-patched
out, run against real live data and inspected before the real run. That
convention has caught a pooled-budget bug, a GTT close that recorded no sell
price, and a `DataFrame.__bool__` call that silently nulled every financial
statement in the conviction engine.

## Note on the conviction layer

It computes published metrics from public data and shows its working. It is
decision support, not financial advice, and not a prediction — every threshold
in it is a convention tuned against one portfolio. That is why it is
display-only, and why the dashboard shows each score alongside the checks that
produced it.
