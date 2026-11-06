"""Capital Ledger mobile web app. No broker order placement endpoints.

Run: uvicorn mobile_app:app --host 127.0.0.1 --port 8502 (from dashboard/).
Uses the existing Oracle ledger and account snapshots; broker refresh is explicit.
"""
from contextlib import closing
import hashlib
import hmac
import importlib
import json
import logging
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv('/home/ubuntu/.env')
HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))
COOKIE = 'capital_session'
log = logging.getLogger('capital-ledger')


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)
    remember: bool = True


class Target(BaseModel):
    target_price: float = Field(gt=0, allow_inf_nan=False)
    have_interest: str = Field(max_length=30)
    timeframe: str = Field(max_length=50)
    expected_target: float | None = Field(default=None, allow_inf_nan=False)
    expected_interest: str | None = None
    expected_timeframe: str | None = None


class Budget(BaseModel):
    amount: float = Field(gt=0, le=1e12, allow_inf_nan=False)
    expected_amount: float = Field(ge=0, allow_inf_nan=False)


def clean(value):
    """Portable JSON: preserve absent values and localise Oracle wall times."""
    if value is None or type(value).__name__ in ('NaTType', 'NAType'):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or IST).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if hasattr(value, 'to_dict'):
        return clean(value.to_dict('records') if hasattr(value, 'columns') else value.to_dict())
    if hasattr(value, 'item'):
        return clean(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


class OracleStore:
    def __init__(self):
        self.db = importlib.import_module('db')

    def snapshot(self):
        d = self.db
        return clean(dict(accounts=[label for label, prefix in [('NEW','ZERODHA'),('OLD','ZERODHA_OLD')] if os.getenv(prefix+'_USER_ID')], holdings=d.get_kite_holdings(), gtts=d.get_kite_gtts(),
            orders=d.get_kite_orders(), sync=d.get_kite_sync_status(),
            budget=d.portfolio_summary(), categories=d.category_status(),
            trades=d.trades(), ideas=d.all_recommendations(),
            performance=d.performance_summary(), history=d.cumulative_pnl_by_month(),
            scores=d.conviction_by_symbol()))

    def refresh(self):
        # This existing adapter only reads the broker and replaces snapshot rows.
        result = importlib.import_module('kite_data').sync_now()
        return clean({account: ({'error': 'Refresh failed. Your previous snapshot is preserved.'}
                               if 'error' in value else value) for account, value in result.items()})

    def target(self, tid, change):
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute('SELECT target_price, have_interest, timeframe, status FROM trades WHERE trade_id = :id FOR UPDATE', {'id': tid})
            row = cur.fetchone()
            if not row or row[3] != 'Open':
                raise HTTPException(409, 'This trade is no longer open. Refresh and try again.')
            current = (float(row[0]) if row[0] is not None else None, row[1] or '', row[2] or '')
            expected = (change.expected_target, change.expected_interest or '', change.expected_timeframe or '')
            if current != expected:
                raise HTTPException(409, 'This target changed since you opened it. Refresh before saving.')
            cur.execute("UPDATE trades SET target_price=:price, have_interest=:interest, timeframe=:timeframe, updated_at=SYSTIMESTAMP WHERE trade_id=:id AND status='Open'",
                        dict(price=change.target_price, interest=change.have_interest, timeframe=change.timeframe, id=tid))
            if cur.rowcount != 1:
                raise HTTPException(409, 'The trade changed. Refresh before saving.')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def budget(self, change):
        conn = self.db.get_connection()
        try:
            cur = conn.cursor()
            cur.execute("UPDATE portfolio_budget SET total_budget=:amount WHERE is_active='Y' AND total_budget=:expected", dict(amount=change.amount, expected=change.expected_amount))
            if cur.rowcount != 1:
                raise HTTPException(409, 'Your budget changed. Refresh before saving.')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def create_app(store=None, users=None, secret=None, state_dir=None, secure=True):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    user_map = users if users is not None else {u.strip(): p.strip() for u, p in (pair.split(':', 1) for pair in os.getenv('DASH_USERS', '').split(',') if ':' in pair)}
    key = secret or os.getenv('DASH_SESSION_SECRET', '')
    state = Path(state_dir or os.getenv('DASH_STATE_DIR', str(Path.home() / '.local/state/capital-ledger')))
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    session_db = state / 'sessions.sqlite3'
    with closing(sqlite3.connect(session_db)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, username TEXT, expires REAL, csrf TEXT, fingerprint TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS attempts (name TEXT PRIMARY KEY, count INTEGER, until_time REAL)')
    session_db.chmod(0o600)
    app.state.store = store
    refresh_lock = threading.Lock()

    def configured():
        if not user_map or len(key) < 24 or key == 'insecure-default-change-me' or user_map.get('admin') == 'changeme':
            raise HTTPException(503, 'Sign-in is not configured. Please contact the administrator.')

    def fingerprint(user):
        return hmac.new(key.encode(), user_map.get(user, '').encode(), hashlib.sha256).hexdigest()

    def data_store():
        if app.state.store is None:
            app.state.store = OracleStore()
        return app.state.store

    def identity(request):
        configured()
        raw = request.cookies.get(COOKIE, '')
        token = hashlib.sha256(raw.encode()).hexdigest()
        with closing(sqlite3.connect(session_db)) as conn, conn:
            row = conn.execute('SELECT username, expires, csrf, fingerprint FROM sessions WHERE token=?', (token,)).fetchone()
        if not row or row[1] <= time.time() or row[0] not in user_map or not hmac.compare_digest(row[3], fingerprint(row[0])):
            raise HTTPException(401, 'Please sign in to continue.')
        return dict(username=row[0], expires=row[1], csrf=row[2], token=token)

    def mutation(request):
        user = identity(request)
        if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), user['csrf']):
            raise HTTPException(403, 'Your session changed. Refresh and try again.')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'Please open this action within Capital Ledger.')
        return user

    @app.middleware('http')
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api/') else 'no-cache'
        return response

    @app.exception_handler(Exception)
    async def error(request, exc):
        log.exception('Dashboard request failed: %s', request.url.path)
        return JSONResponse(status_code=503, content={'detail': 'We could not load this information. Please try again.'}, headers={'Cache-Control': 'no-store'})

    @app.get('/health')
    def health():
        return {'status': 'ok', 'app': 'capital-ledger-mobile'}

    @app.post('/api/login')
    def login(body: Login, request: Request, response: Response):
        configured()
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'Please sign in from Capital Ledger.')
        user = body.username.strip()
        now = time.time()
        # Persist rate limits across restarts; both per-user and per-client.
        buckets = ['user:' + user, 'client:' + (request.client.host if request.client else 'unknown')]
        with closing(sqlite3.connect(session_db, timeout=10)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            for bucket in buckets:
                row = conn.execute('SELECT count, until_time FROM attempts WHERE name=?', (bucket,)).fetchone()
                if row and row[0] >= 5 and row[1] > now:
                    raise HTTPException(429, 'Too many attempts. Please try again in five minutes.')
            good = user in user_map and hmac.compare_digest(body.password.encode(), user_map[user].encode())
            if not good:
                for bucket in buckets:
                    conn.execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(name) DO UPDATE SET count=CASE WHEN until_time<? THEN 1 ELSE count+1 END, until_time=?', (bucket, now+300, now, now+300))
                conn.commit()
                raise HTTPException(401, 'Username or password is incorrect.')
            for bucket in buckets:
                conn.execute('DELETE FROM attempts WHERE name=?', (bucket,))
            raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            ttl = 30*86400 if body.remember else 12*3600
            conn.execute('DELETE FROM sessions WHERE expires<?', (now,))
            conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', (hashlib.sha256(raw.encode()).hexdigest(), user, now+ttl, csrf, fingerprint(user)))
        response.set_cookie(COOKIE, raw, max_age=ttl if body.remember else None,
                            httponly=True, secure=secure, samesite='strict', path='/')
        return dict(username=user, csrf=csrf, expires=now+ttl)

    @app.get('/api/session')
    def session(request: Request):
        user = identity(request)
        return {k: user[k] for k in ('username','csrf','expires')}

    @app.post('/api/logout')
    def logout(request: Request, response: Response):
        user = mutation(request)
        with closing(sqlite3.connect(session_db)) as conn, conn:
            conn.execute('DELETE FROM sessions WHERE token=?', (user['token'],))
        response.delete_cookie(COOKIE, path='/', secure=secure, httponly=True, samesite='strict')
        return {'ok': True}

    @app.get('/api/dashboard')
    def dashboard(request: Request):
        identity(request)
        from lib.bands import CONVICTION_MIN_SCORE, CONVICTION_SIZING, SIZING_MODEL
        return dict(**data_store().snapshot(), policy=dict(minimum=CONVICTION_MIN_SCORE, bands=CONVICTION_SIZING, model=SIZING_MODEL), fetched_at=datetime.now(IST).isoformat())

    @app.post('/api/refresh')
    def refresh(request: Request):
        mutation(request)
        if not refresh_lock.acquire(blocking=False):
            raise HTTPException(409, 'An account refresh is already running.')
        try:
            return {'accounts': data_store().refresh()}
        finally:
            refresh_lock.release()

    @app.put('/api/trades/{tid}/target')
    def target(tid: int, body: Target, request: Request):
        mutation(request)
        if tid <= 0 or body.have_interest not in ('','Have Interest','No Interest'):
            raise HTTPException(422, 'Choose a valid advisory interest value.')
        data_store().target(tid, body)
        return {'ok': True}

    @app.put('/api/budget')
    def budget(body: Budget, request: Request):
        mutation(request)
        data_store().budget(body)
        return {'ok': True}

    @app.get('/')
    def index():
        return FileResponse(HERE / 'web/index.html')

    @app.get('/sw.js')
    def worker():
        return FileResponse(HERE / 'web/sw.js', media_type='application/javascript', headers={'Service-Worker-Allowed': '/'})

    app.mount('/assets', StaticFiles(directory=HERE / 'web'), name='assets')
    return app


# Make the shared, credential-free sizing constants importable under uvicorn.
import sys
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))
app = create_app()
