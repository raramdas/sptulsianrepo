# Capital Ledger mobile workspace

The main dashboard is a responsive web app with an iPhone Home Screen experience.
It uses the existing Oracle ledger and broker snapshots. The trading entrypoints,
sizing rules and GTT safeguards are unchanged.

## Everyday use

- **Overview:** saved holdings value, capital plan and review inbox.
- **Portfolio:** account filter, company search, holding details and CSV export.
- **Ideas:** dated recommendations, advisory interest, model scores and record details.
- **Activity:** ledger records, broker orders and saved sell triggers.
- **More:** budget, recorded performance, current sizing bands, connected accounts,
  iPhone instructions and sign-out. Advanced tools remain at `/classic/`.

Every broker view shows its snapshot age. Loading the app never refreshes broker
accounts or places trades. **Refresh accounts** is explicit; it reads Kite and
updates only the existing snapshot tables. Review flags are conservative
comparisons, not confirmed execution outcomes. Open ledger records are never
presented as confirmed broker holdings.

Target edits and total-budget edits require an explicit save, use session-bound
CSRF protection, and reject stale values. Target changes do not modify an already
active broker GTT. No order placement or cancellation endpoints exist in this app.

## iPhone

Open the existing HTTPS dashboard address in Safari → Share → Add to Home Screen.
Keep “Open as Web App” enabled if offered. Open the new Capital icon and sign in
with the existing dashboard username/password. Select **Remember this device**
for a 30-day session. Safari and Home Screen apps can have separate cookie stores,
so signing in once again after installation is normal. This is a web app, not an
App Store binary or Face ID integration. A network connection is required for
private account information.

## Authentication and privacy

Opaque random session IDs are stored in Secure, HttpOnly, SameSite=Strict cookies.
The server stores only session-token hashes in a private SQLite file outside the
repository. Remembered sessions survive restarts; logout revokes the server-side
session. Password/secret changes invalidate sessions after the service restarts.
Login rate limits persist across restarts. Missing or insecure default credentials
fail closed. Set `DASH_USERS` and a strong `DASH_SESSION_SECRET` in the existing
`/home/ubuntu/.env`; no secrets belong in source control.

The service worker caches only the public shell and icons. API responses have
`Cache-Control: no-store`. Portfolio data and credentials are not written to
localStorage, IndexedDB or service-worker caches.

## Deployment

1. Merge the tested change and fast-forward **both** server checkouts from Git.
2. From `/home/ubuntu/stockbot`, run `bash provisioning/deploy_mobile.sh`.
3. The script installs the pinned web dependencies in a separate virtual
   environment and starts `capital-ledger-mobile` on loopback port 8502.
4. Streamlit remains on loopback 8501 with `baseUrlPath=classic`. The existing
   `/api/spt-capture/*` route stays on port 8600.
5. Caddy continues to serve the same HTTPS hostname. Its default route now goes
   to the mobile app; `/classic/*` goes to Streamlit. Health checks and config
   validation precede routing changes. A failed deployment restores old routing.

No trading job is started or restarted by this deployment. The script creates a
private timestamped backup of the prior Caddy and Streamlit override configuration.
To roll back, restore those files, run `systemctl daemon-reload`, restart
`stockbot-dashboard`, and reload Caddy. The separate mobile service can then be
stopped. Do not copy application source to production outside Git.

## Verification

```
python3 dashboard/mobile_tests/test_app.py
python3 tests/test_trading_safety.py
python3 tests/test_dashboard_logic.py
python3 tests/test_write_operations.py
```

The app tests use synthetic data and temporary sessions. They cover sign-in,
remembered sessions, restart persistence, CSRF, logout revocation, credential
rotation, rate limiting, read-only views, write validation and optimistic updates.

For visual and interaction checks, install Playwright and run the synthetic
preview on localhost:

```
uvicorn preview:app --app-dir dashboard/mobile_tests --host 127.0.0.1 --port 8765
python3 dashboard/mobile_tests/browser_check.py
```

The fixture credentials and data exist only in the test server. Production runs
`mobile_app:app`, which has no demo mode. Browser checks exercise desktop Chromium
and 390/375-pixel iPhone WebKit layouts, navigation, filtering, details, target
edits, remembered sign-in and logout. Actual Home Screen installation still needs
an iPhone; emulation does not certify every iOS version.
