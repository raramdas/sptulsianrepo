#!/usr/bin/env python3
"""
app.py — Streamlit dashboard for the Stock Tip Automation + Budget system.
Visual design: clean minimal (Stripe/Linear-style) — see theme.py.

Run:
    cd dashboard
    streamlit run app.py --server.port 8501 --server.address 0.0.0.0

Pages:
  - Overview            deployment gauge, KPI cards, category allocation bars
  - Category Drill-Down  category -> stock-type -> individual trades
  - Performance          realized P&L trend, win rate, category breakdown
  - Set Targets          set target price / timeframe / have-interest per trade
  - Trades Explorer      filterable, searchable, CSV export
  - Recommendations      every tip the bot has seen, bought or not
  - Needs Review          symbols the bot wouldn't guess — correct + manual retry-buy
  - Open Orders          live Kite order book + GTT triggers, filterable
  - Classification       AMFI cap-type lookup
  - Settings & Edits     total budget, category %, manually close a trade
"""
import os
import time
import datetime
import json
import streamlit as st
import pandas as pd
from dotenv import load_dotenv
from streamlit_cookies_manager import EncryptedCookieManager

load_dotenv('/home/ubuntu/.env')

import db
import theme
import kite_data

st.set_page_config(page_title="Stock Bot — Capital Ledger", page_icon="\U0001F4CA", layout="wide")
theme.inject()

# ── Simple auth with brute-force protection ──────────────────────
MAX_ATTEMPTS = 5
LOCKOUT_SECS = 300
REMEMBER_ME_DAYS = 30

def load_users():
    raw = os.environ.get('DASH_USERS', 'admin:changeme')
    users = {}
    for pair in raw.split(','):
        if ':' in pair:
            u, p = pair.split(':', 1)
            users[u.strip()] = p.strip()
    return users

USERS = load_users()

# Encrypted browser cookie so login survives iOS reclaiming a home-screen
# web app's memory (Streamlit's session_state is in-memory only and gets
# wiped whenever that happens — a plain "add to home screen" bookmark is
# not a native app, so this is the practical equivalent of "stay signed
# in," not true biometric Face ID, which would need a native app or a
# much larger WebAuthn build). The cookie itself is encrypted with
# DASH_SESSION_SECRET — unreadable/unforgeable without it — and only ever
# holds a username + expiry, never a password.
COOKIE_SECRET = os.environ.get('DASH_SESSION_SECRET', 'insecure-default-change-me')
cookies = EncryptedCookieManager(prefix="capital_ledger/", password=COOKIE_SECRET)


def check_login():
    if not cookies.ready():
        st.stop()  # first render round-trips through a hidden component to sync cookies

    if st.session_state.get('authenticated'):
        return True

    # Auto-login from a valid remember-me cookie, if present
    saved_user = cookies.get('user')
    saved_exp = cookies.get('exp')
    if saved_user and saved_exp:
        try:
            if float(saved_exp) > time.time() and saved_user in USERS:
                st.session_state['authenticated'] = True
                st.session_state['user'] = saved_user
                return True
        except ValueError:
            pass

    if 'failed_attempts' not in st.session_state:
        st.session_state['failed_attempts'] = 0
    if 'lockout_until' not in st.session_state:
        st.session_state['lockout_until'] = 0

    left, mid, right = st.columns([1, 1.3, 1])
    with mid:
        st.markdown("## Capital Ledger")
        st.caption("Stock Bot — sign in to view your portfolio.")

        now = time.time()
        if now < st.session_state['lockout_until']:
            remaining = int(st.session_state['lockout_until'] - now)
            st.error(f"Too many failed attempts. Locked out for {remaining} seconds.")
            return False

        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            remember = st.checkbox("Keep me signed in on this device", value=True)
            submitted = st.form_submit_button("Sign in")
            if submitted:
                if USERS.get(username) == password:
                    st.session_state['authenticated'] = True
                    st.session_state['user'] = username
                    st.session_state['failed_attempts'] = 0
                    if remember:
                        cookies['user'] = username
                        cookies['exp'] = str(now + REMEMBER_ME_DAYS * 86400)
                        cookies.save()
                    st.rerun()
                else:
                    st.session_state['failed_attempts'] += 1
                    attempts_left = MAX_ATTEMPTS - st.session_state['failed_attempts']
                    if attempts_left <= 0:
                        st.session_state['lockout_until'] = now + LOCKOUT_SECS
                        st.session_state['failed_attempts'] = 0
                        st.error(f"Too many failed attempts. Locked out for {LOCKOUT_SECS // 60} minutes.")
                    else:
                        st.error(f"Invalid credentials. {attempts_left} attempt(s) remaining before lockout.")
    return False


def fmt(n):
    try:
        return f"₹{float(n):,.2f}"
    except Exception:
        return n


# ── Pages ────────────────────────────────────────────────────────

def page_overview():
    st.title("Portfolio Overview")

    st.markdown("### Live Broker Snapshot (Kite)")
    sync_status = kite_data.sync_status()
    if sync_status:
        lines = [f"**{row['account_label']}**: {row['synced_at'].strftime('%Y-%m-%d %H:%M:%S')} "
                 f"({row['holdings_count']} holdings, {row['gtt_count']} GTTs, {row['order_count']} orders)"
                 for row in sync_status]
        st.caption("Last synced — " + " · ".join(lines) + ". Not live — click Sync to refresh. The "
                   "budget tracking below is Capital Ledger's own bookkeeping in Oracle and can drift "
                   "from this snapshot.")
    else:
        st.caption("Not synced yet — click 'Sync Kite Data' below to pull holdings, GTTs, "
                   "and the order book from every configured Zerodha account.")

    if st.button("Sync Kite Data"):
        with st.spinner("Logging into Kite and syncing every configured account…"):
            results = kite_data.sync_now()
            ok = {k: v for k, v in results.items() if 'error' not in v}
            failed = {k: v for k, v in results.items() if 'error' in v}
            if ok:
                st.success(" · ".join(
                    f"{label}: {r['holdings']} holdings, {r['gtts']} GTTs, {r['orders']} orders"
                    for label, r in ok.items()
                ))
            for label, r in failed.items():
                st.error(f"{label} sync failed: {r['error']}")

    open_trades_df = db.trades(status='Open')

    try:
        hs = kite_data.holdings_summary()
        gs = kite_data.gtt_summary()
        os_ = kite_data.orders_today_summary()
        open_orders = kite_data.open_orders_count()

        k1, k2, k3 = st.columns(3)
        with k1:
            st.markdown(theme.kpi_card("Holdings (live)", hs['count'], tone="accent"), unsafe_allow_html=True)
        with k2:
            st.markdown(theme.kpi_card("Invested (live)", fmt(hs['invested'])), unsafe_allow_html=True)
        with k3:
            st.markdown(theme.kpi_card("Current Value (live)", fmt(hs['current_value'])), unsafe_allow_html=True)

        k5, k6, k7 = st.columns(3)
        with k5:
            st.markdown(theme.kpi_card("Active GTTs", gs['active'], tone="accent"), unsafe_allow_html=True)
        with k6:
            st.markdown(theme.kpi_card("Open Orders", open_orders, tone="accent"), unsafe_allow_html=True)
            st.caption("Triggered GTTs + pending buy orders")
        with k7:
            order_line = " · ".join(f"{k}: {v}" for k, v in os_['by_status'].items()) or "none"
            st.markdown(theme.kpi_card("Today's Orders", os_['total']), unsafe_allow_html=True)
            st.caption(order_line)

        tagged = kite_data.tag_holdings_with_category(open_trades_df)
        with st.expander(f"Holdings detail ({hs['count']})"):
            conv_map = db.conviction_by_symbol()
            display_cols = ['symbol', 'account_label', 'quantity', 'average_price', 'last_price',
                            'current_value', 'pnl']
            money_cols = ['average_cost', 'last_price', 'current_value', 'pnl']

            def _render_holdings(df):
                d = df[display_cols].rename(columns={'average_price': 'average_cost', 'account_label': 'account'}).copy()
                d['quantity'] = d['quantity'].astype(int)
                # Holdings are per symbol, conviction is per trade — map on
                # symbol and show the most recent read on that company.
                d.insert(1, 'conviction', [
                    theme.conviction_badge(*(conv_map.get(str(s).upper()) or (None, None)))
                    for s in d['symbol']
                ])
                st.markdown(theme.render_table(d, money_cols=money_cols, gain_col='pnl'), unsafe_allow_html=True)

            if tagged.empty:
                st.info("No holdings.")
            else:
                mapped = tagged[tagged['category_name'].notna()]
                unmapped = tagged[tagged['category_name'].isna()]

                st.markdown(f"**Mapped to a category ({len(mapped)})**")
                if mapped.empty:
                    st.caption("None.")
                else:
                    for cat_name, grp in mapped.groupby('category_name'):
                        st.caption(f"{cat_name} ({len(grp)})")
                        _render_holdings(grp)

                st.markdown(f"**Unmapped — not in any open Oracle trade ({len(unmapped)})**")
                if unmapped.empty:
                    st.caption("None.")
                else:
                    st.caption("These symbols show up in your live Kite holdings but have no matching "
                               "'Open' row in Oracle's trades table — the gap between Budget Tracking "
                               "below and your real broker state.")
                    _render_holdings(unmapped)
    except Exception as e:
        st.warning(f"Couldn't read the Kite snapshot from Oracle — showing Oracle-only figures below. ({e})")

    st.divider()

    st.markdown("### Budget Tracking (Oracle)")
    st.caption("Capital Ledger's own category/budget bookkeeping — not Kite's live account state.")
    s = db.portfolio_summary()
    fy_perf = db.realized_pnl_fy()
    realized_pnl = fy_perf['total_realized']

    col_gauge, col_kpis = st.columns([1, 2])
    with col_gauge:
        st.plotly_chart(theme.render_gauge(s['utilization_pct']), use_container_width=True)
    with col_kpis:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(theme.kpi_card("Total Budget", fmt(s['total_budget']), tone="accent"), unsafe_allow_html=True)
            st.markdown(theme.kpi_card("Available", fmt(s['available']), tone="positive"), unsafe_allow_html=True)
        with c2:
            st.markdown(theme.kpi_card("Invested (Open, Oracle)", fmt(s['invested'])), unsafe_allow_html=True)
            st.markdown(theme.kpi_card("Open Positions (Oracle)", s['open_positions']), unsafe_allow_html=True)

    p1, p2, p3 = st.columns(3)
    with p1:
        tone = "positive" if realized_pnl >= 0 else "negative"
        st.markdown(theme.kpi_card(f"Realized P&L ({fy_perf['fy_label']})", fmt(realized_pnl), tone=tone), unsafe_allow_html=True)
    try:
        unrealized_pnl, unpriced_count = kite_data.unrealized_pnl_for_oracle_trades(open_trades_df)
        with p2:
            tone = "positive" if unrealized_pnl >= 0 else "negative"
            st.markdown(theme.kpi_card("Unrealized P&L (Oracle open trades, live price)", fmt(unrealized_pnl), tone=tone), unsafe_allow_html=True)
            if unpriced_count:
                st.caption(f"{unpriced_count} open trade(s) couldn't be priced (no matching live quote)")
        with p3:
            total_pnl = realized_pnl + unrealized_pnl
            tone = "positive" if total_pnl >= 0 else "negative"
            st.markdown(theme.kpi_card(f"Total P&L ({fy_perf['fy_label']} Realized + Live Unrealized)",
                                       fmt(total_pnl), tone=tone), unsafe_allow_html=True)
    except Exception as e:
        with p2:
            st.markdown(theme.kpi_card("Unrealized P&L", "—"), unsafe_allow_html=True)
            st.caption(f"Couldn't read the Kite snapshot for live pricing ({e})")
        with p3:
            st.markdown(theme.kpi_card("Total P&L", "—"), unsafe_allow_html=True)

    st.markdown("### Category Allocation")
    cat = db.category_status()
    if cat.empty:
        st.info("No category data found.")
        return

    realized_by_cat = db.category_pnl_breakdown_fy()
    realized_map = dict(zip(realized_by_cat['category_name'], realized_by_cat['realized_pnl'])) \
        if not realized_by_cat.empty else {}
    try:
        unrealized_by_cat = kite_data.unrealized_pnl_by_category(open_trades_df)
        unrealized_map = dict(zip(unrealized_by_cat['category_name'], unrealized_by_cat['unrealized_pnl'])) \
            if not unrealized_by_cat.empty else {}
    except Exception:
        unrealized_map = None

    for _, row in cat.iterrows():
        st.markdown(
            theme.category_bar(row['category_name'], float(row['category_budget']),
                               float(row['invested']), float(row['available'])),
            unsafe_allow_html=True
        )
        r_pnl = realized_map.get(row['category_name'], 0.0)
        u_pnl = unrealized_map.get(row['category_name'], 0.0) if unrealized_map is not None else None
        st.markdown(theme.category_pnl_row(r_pnl, u_pnl), unsafe_allow_html=True)


def page_drilldown():
    st.title("Category Drill-Down")
    cat = db.category_status()
    if cat.empty:
        st.info("No categories found.")
        return

    cat_col, _spacer = st.columns([1, 2])
    with cat_col:
        category = st.selectbox("Select a category", cat['category_name'].tolist())

    crow = cat[cat['category_name'] == category].iloc[0]
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(theme.kpi_card("Category Budget", fmt(crow['category_budget']), tone="accent"), unsafe_allow_html=True)
    with c2:
        st.markdown(theme.kpi_card("Invested", fmt(crow['invested'])), unsafe_allow_html=True)
    with c3:
        tone = "negative" if crow['available'] < 0 else "positive"
        st.markdown(theme.kpi_card("Available", fmt(crow['available']), tone=tone), unsafe_allow_html=True)

    st.markdown(f"### Stock-Type Breakdown — {category}")
    st_df = db.stock_type_status(category)
    if st_df.empty:
        st.info("No stock-type data.")
    else:
        for _, row in st_df.iterrows():
            st.markdown(
                theme.category_bar(f"{row['cap_type']} ({row['pct']:.0f}%)",
                                   float(row['budget']), float(row['invested']), float(row['available'])),
                unsafe_allow_html=True
            )

    # Full (unfiltered) open trades needed here — tag_holdings_with_category
    # splits a symbol's holding across categories in proportion to Oracle's
    # recorded quantity per category, which requires seeing every category's
    # trades, not just this one.
    all_open_trades = db.trades(status='Open')
    cat_open_trades = all_open_trades[all_open_trades['category_name'] == category] if not all_open_trades.empty else all_open_trades

    st.markdown(f"### Holdings — {category}")
    try:
        tagged = kite_data.tag_holdings_with_category(all_open_trades)
        cat_holdings = tagged[tagged['category_name'] == category]

        unrealized_by_cat = kite_data.unrealized_pnl_by_category(cat_open_trades)
        unrealized_pnl = float(unrealized_by_cat.iloc[0]['unrealized_pnl']) if not unrealized_by_cat.empty else 0.0
        unpriced = int(unrealized_by_cat.iloc[0]['unpriced_count']) if not unrealized_by_cat.empty else 0

        realized_by_cat = db.category_pnl_breakdown_fy()
        cat_realized = realized_by_cat[realized_by_cat['category_name'] == category] if not realized_by_cat.empty else realized_by_cat
        realized_pnl = float(cat_realized.iloc[0]['realized_pnl']) if not cat_realized.empty else 0.0

        p1, p2, p3 = st.columns(3)
        with p1:
            tone = "positive" if realized_pnl >= 0 else "negative"
            st.markdown(theme.kpi_card("Realized P&L (FY)", fmt(realized_pnl), tone=tone), unsafe_allow_html=True)
        with p2:
            tone = "positive" if unrealized_pnl >= 0 else "negative"
            st.markdown(theme.kpi_card("Unrealized P&L (live)", fmt(unrealized_pnl), tone=tone), unsafe_allow_html=True)
            if unpriced:
                st.caption(f"{unpriced} open trade(s) couldn't be priced (no matching live quote)")
        with p3:
            total_pnl = realized_pnl + unrealized_pnl
            tone = "positive" if total_pnl >= 0 else "negative"
            st.markdown(theme.kpi_card("Total P&L (FY Realized + Live Unrealized)", fmt(total_pnl), tone=tone), unsafe_allow_html=True)

        if cat_holdings.empty:
            st.info("No live holdings mapped to this category.")
        else:
            conv_map = db.conviction_by_symbol()
            display_cols = ['symbol', 'account_label', 'quantity', 'average_price', 'last_price',
                            'current_value', 'pnl']
            money_cols = ['average_cost', 'last_price', 'current_value', 'pnl']
            d = cat_holdings[display_cols].rename(columns={'average_price': 'average_cost', 'account_label': 'account'}).copy()
            d['quantity'] = d['quantity'].astype(int)
            d.insert(1, 'conviction', [
                theme.conviction_badge(*(conv_map.get(str(s).upper()) or (None, None)))
                for s in d['symbol']
            ])
            st.markdown(theme.render_table(d, money_cols=money_cols, gain_col='pnl'), unsafe_allow_html=True)
        st.caption("From the last Kite sync — click 'Sync Kite Data' on Overview to refresh. For full trade "
                   "history in this category (including closed/error/skipped), use Trades Explorer.")
    except Exception as e:
        st.warning(f"Couldn't read the Kite snapshot for holdings — showing Oracle-only figures above. ({e})")


def page_performance():
    st.title("Performance")
    st.caption("Realized P&L by category, from closed trades. Open positions aren't counted until closed.")

    summary = db.performance_summary()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        tone = "positive" if summary['total_realized'] >= 0 else "negative"
        st.markdown(theme.kpi_card("Total Realized P&L", fmt(summary['total_realized']), tone=tone), unsafe_allow_html=True)
    with c2:
        st.markdown(theme.kpi_card("Win Rate", f"{summary['win_rate']}%", tone="accent"), unsafe_allow_html=True)
    with c3:
        st.markdown(theme.kpi_card("Closed Trades", summary['trade_count']), unsafe_allow_html=True)
    with c4:
        st.markdown(theme.kpi_card("Avg Holding (days)", summary['avg_holding_days']), unsafe_allow_html=True)

    st.markdown("### Cumulative Realized P&L")
    trend = db.cumulative_pnl_by_month()
    if trend.empty:
        st.info("No closed trades yet — nothing to chart.")
    else:
        st.plotly_chart(theme.render_line_chart(trend, 'month', 'cumulative_pnl'), use_container_width=True)

    st.markdown("### Performance by Category")
    cat_perf = db.performance_by_category()
    if cat_perf.empty:
        st.info("No closed trades yet.")
    else:
        display = cat_perf.rename(columns={
            'category_name': 'Category', 'realized_pnl': 'Realized P&L',
            'trade_count': 'Closed Trades', 'win_rate': 'Win Rate %',
            'avg_holding_days': 'Avg Holding (days)',
        })
        st.markdown(
            theme.render_table(display, money_cols=['Realized P&L'], gain_col='Realized P&L'),
            unsafe_allow_html=True
        )


def _render_trades_table(tdf):
    if tdf.empty:
        st.info("No trades.")
        return
    tdf = tdf.copy()
    tdf['_status_raw'] = tdf['status']
    tdf['status'] = tdf.apply(lambda r: theme.friendly_status(r['_status_raw'], r.get('notes')), axis=1)
    if 'conviction' in tdf.columns:
        # Rendered as a badge banded to match the sizing thresholds, so the
        # number that decided the position size is visible next to the trade.
        # The engine is passed too: the bands are percentile-matched to one
        # engine's distribution, so a score from an older one must not be
        # coloured as though those bands applied to it.
        tdf['conviction'] = [
            theme.conviction_badge(s, m) for s, m in
            zip(tdf['conviction'], tdf.get('conviction_model', pd.Series(None, index=tdf.index)))
        ]
    money_cols = ['my_buy_price', 'invested_amount', 'target_price', 'my_sell_price', 'my_gain_loss']
    hidden = ('trade_id', 'notes', 'conviction_tier', 'conviction_verdict',
              'conviction_model')
    display_cols = [c for c in tdf.columns if c not in hidden]
    # Conviction sits next to the stock it describes, rather than trailing the
    # row where it reads as an afterthought.
    if 'conviction' in display_cols and 'stock_name' in display_cols:
        display_cols.remove('conviction')
        display_cols.insert(display_cols.index('stock_name') + 1, 'conviction')
    st.markdown(theme.render_table(tdf[display_cols], money_cols=money_cols, status_col='status',
                                   status_class_col='_status_raw', gain_col='my_gain_loss'),
                unsafe_allow_html=True)


def page_trades():
    st.title("Trades Explorer")
    col1, col2, col3 = st.columns([1, 1, 1.4])
    with col1:
        status = st.selectbox("Status", ['All', 'Open', 'Closed', 'SKIPPED', 'ERROR'])
    with col2:
        cats = db.category_status()
        cat_options = ['All'] + (cats['category_name'].tolist() if not cats.empty else [])
        category = st.selectbox("Category", cat_options)
    with col3:
        symbol = st.text_input("Search symbol or stock name", placeholder="e.g. RELIANCE")

    col4, col5 = st.columns(2)
    with col4:
        date_from = st.date_input("Buy date from", value=None)
    with col5:
        date_to = st.date_input("Buy date to", value=None)

    tdf = db.trades(
        status=None if status == 'All' else status,
        category=None if category == 'All' else category,
        symbol=symbol or None,
        date_from=date_from if date_from else None,
        date_to=date_to if date_to else None,
    )
    st.caption(f"{len(tdf)} trade(s) found")
    _render_trades_table(tdf)

    if not tdf.empty:
        csv = tdf.to_csv(index=False).encode('utf-8')
        st.download_button("Download results (CSV)", csv, "trades_export.csv", "text/csv")


def page_recommendations():
    st.title("Stock Recommendations")
    st.caption("Every tip the bot has seen from SPTulsian emails — bought or not. Target price "
               "is blank for all rows right now since SPTulsian's target/timeframe scraping is "
               "disabled pending IP whitelisting (spt_scraper.py).")

    df = db.all_recommendations()
    if df.empty:
        st.info("No recommendations yet.")
        return

    display = df.rename(columns={
        'buy_date': 'Date', 'stock_name': 'Stock',
        'recommended_price': 'Purchase Price', 'target_price': 'Target Price',
        'status': 'Status',
    })
    display['_status_raw'] = df['status']
    display['Status'] = df.apply(lambda r: theme.friendly_status(r['status'], r.get('notes')), axis=1)
    # Both inputs the buy gate reads, next to the outcome — so a skip can be
    # explained from the row itself rather than by opening the notes.
    display['Conviction'] = [
        theme.conviction_badge(s, m) for s, m in
        zip(df['conviction'], df.get('conviction_model', pd.Series(None, index=df.index)))
    ]
    display['SPT Interest'] = df['have_interest'].fillna('').replace('', '—')
    st.caption(f"{len(display)} recommendation(s)")
    st.markdown(
        theme.render_table(display[['Date', 'Stock', 'Conviction', 'Purchase Price',
                                    'Target Price', 'SPT Interest', 'Status', '_status_raw']],
                           money_cols=['Purchase Price', 'Target Price'], status_col='Status',
                           status_class_col='_status_raw'),
        unsafe_allow_html=True
    )


def _needs_review_reason(notes):
    """Turn the raw notes the buy bot left into a one-line explanation of
    what's actually needed, instead of making the user parse the log-style
    text themselves."""
    n = notes or ''
    if 'status=FUZZY' in n:
        return "The bot found a similar-sounding symbol but wasn't confident enough to trust it — enter the correct Kite trading symbol below."
    if 'status=NOT_FOUND' in n:
        return "The bot couldn't find any matching symbol on Kite — enter the correct Kite trading symbol below."
    return n or "Needs manual review."


def page_needs_review():
    st.title("Needs Review")
    st.caption("Tips the buy bot refused to guess a symbol for, rather than risk buying the wrong stock. "
               "Enter the correct Kite symbol, preview exactly what it would buy, then confirm to place "
               "a real order — nothing is bought until you click Confirm.")

    df = db.needs_review_trades()
    if df.empty:
        st.info("Nothing needs review right now.")
        return

    for _, row in df.iterrows():
        tid = int(row['trade_id'])
        with st.expander(f"#{tid} — {row['stock_name']} ({row['buy_date']})", expanded=True):
            st.warning(_needs_review_reason(row['notes']))
            st.caption(f"Category: {row['category_name']} · Recommended price: {fmt(row['recommended_price'])}")

            symbol_col, _spacer = st.columns([1, 2])
            with symbol_col:
                symbol = st.text_input("Correct Kite trading symbol", key=f'nr_symbol_{tid}',
                                       placeholder="e.g. VOLTAMP").strip().upper()

            preview_key = f'nr_preview_{tid}'
            col_preview, col_confirm = st.columns([1, 2])
            with col_preview:
                if st.button("Preview", key=f'nr_preview_btn_{tid}', disabled=not symbol):
                    try:
                        st.session_state[preview_key] = kite_data.preview_retry_buy(tid, symbol)
                    except Exception as e:
                        st.session_state.pop(preview_key, None)
                        st.error(str(e))

            preview = st.session_state.get(preview_key)
            if preview and preview['symbol'] != symbol:
                st.caption("Symbol changed since last preview — click Preview again before confirming.")
            elif preview:
                st.markdown(
                    f"Would place: **{preview['order_type']}** {preview['qty']} × {preview['symbol']} "
                    f"@ {fmt(preview['buy_price'])} — actual cost {fmt(preview['actual_cost'])} "
                    f"(live market price {fmt(preview['mkt_price'])}, cap type: {preview['cap_type'] or 'unknown'})"
                )
                if not preview['budget_ok']:
                    st.error("Insufficient budget in this category/stock-type — the live bot would SKIP "
                             "this trade rather than buy it, so Confirm is disabled.")
                else:
                    if st.button(f"Confirm & Buy #{tid} — places a REAL order", key=f'nr_confirm_{tid}', type="primary"):
                        try:
                            order_id = kite_data.confirm_retry_buy(preview)
                            st.success(f"Bought — order {order_id}")
                            st.session_state.pop(preview_key, None)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Buy failed: {e}")


CONVICTION_TONES = {
    'ACCEPT': ('#16A34A', 'Accept'),
    'RECOMMEND REJECT': ('#DC2626', 'Recommend reject'),
    'INSUFFICIENT EVIDENCE': ('#F59E0B', 'Insufficient evidence'),
}


def _conviction_badge(verdict):
    colour, label = CONVICTION_TONES.get(verdict, ('#6B7280', verdict or '—'))
    return (f'<span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            f'font-size:0.78rem;font-weight:600;color:{colour};'
            f'background:{colour}1A;border:1px solid {colour}33;">{label}</span>')


def _conviction_bar(pct, colour):
    pct = max(0.0, min(100.0, float(pct or 0)))
    return (f'<div style="background:#E5E7EB;border-radius:999px;height:7px;width:100%;">'
            f'<div style="width:{pct:.0f}%;background:{colour};height:7px;'
            f'border-radius:999px;"></div></div>')


def page_conviction():
    st.title("Conviction")
    st.caption("How well each recommendation is supported by public evidence — "
               "Piotroski, Altman Z\u2033-EM, Beneish, analyst consensus, trend and "
               "liquidity, NSE surveillance. This is decision support, not advice, "
               "and it does not affect what the bot buys or how much.")

    df = db.conviction_latest()
    if df.empty:
        st.info("No conviction scores yet. They are written by main_conviction.py, "
                "which runs after the 9:30 AM recommendation job.")
        return

    scored = pd.to_numeric(df['score'], errors='coerce')
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(theme.kpi_card("Scored positions", len(df)), unsafe_allow_html=True)
    with c2:
        med = scored.median()
        st.markdown(theme.kpi_card("Median score", "—" if pd.isna(med) else f"{med:.0f}"),
                    unsafe_allow_html=True)
    with c3:
        flagged = int((df['verdict'] == 'RECOMMEND REJECT').sum())
        st.markdown(theme.kpi_card("Flagged", flagged,
                                   tone="negative" if flagged else "default"),
                    unsafe_allow_html=True)
    with c4:
        thin = int(pd.to_numeric(df['evidence_pct'], errors='coerce').lt(60).sum())
        st.markdown(theme.kpi_card("Thin evidence", thin,
                                   tone="warning" if thin else "default"),
                    unsafe_allow_html=True)

    fcol, scol, _sp = st.columns([1, 1, 2])
    with fcol:
        verdicts = ['All'] + sorted(df['verdict'].dropna().unique().tolist())
        vpick = st.selectbox("Verdict", verdicts)
    with scol:
        sort_by = st.selectbox("Sort by", ["Score (high first)", "Score (low first)",
                                           "Evidence (low first)", "Most recent"])

    view = df if vpick == 'All' else df[df['verdict'] == vpick]
    if sort_by == "Score (low first)":
        view = view.sort_values('score', na_position='first')
    elif sort_by == "Evidence (low first)":
        view = view.sort_values('evidence_pct', na_position='first')
    elif sort_by == "Most recent":
        view = view.sort_values('scored_at', ascending=False)
    else:
        view = view.sort_values('score', ascending=False, na_position='last')

    st.caption(f"{len(view)} position(s)")

    for _, r in view.iterrows():
        score = None if pd.isna(r['score']) else float(r['score'])
        ev = 0 if pd.isna(r['evidence_pct']) else float(r['evidence_pct'])
        colour = ('#16A34A' if (score or 0) >= 65 else
                  '#F59E0B' if (score or 0) >= 50 else '#DC2626')
        score_txt = "n/a" if score is None else f"{score:.0f}"

        header = (f"{r['stock_name']} ({r['symbol']}) \u2014 "
                  f"{score_txt}/100 \u00b7 {r['tier']} \u00b7 {r['verdict']}")
        with st.expander(header, expanded=False):
            st.markdown(
                f"<div style='display:flex;gap:1.5rem;align-items:center;flex-wrap:wrap;'>"
                f"<div style='font-size:2rem;font-weight:700;color:{colour};'>{score_txt}"
                f"<span style='font-size:0.9rem;color:#6B7280;font-weight:400;'>/100</span></div>"
                f"<div>{_conviction_badge(r['verdict'])}</div>"
                f"<div style='color:#6B7280;font-size:0.85rem;'>trade #{int(r['trade_id'])} \u00b7 "
                f"{r['category_name']} \u00b7 {r['sector'] or 'sector n/a'}</div></div>",
                unsafe_allow_html=True)

            st.markdown(f"<div style='margin:0.6rem 0 0.2rem;color:#6B7280;font-size:0.8rem;'>"
                        f"Evidence assessed: {ev:.0f} of 100 points</div>"
                        + _conviction_bar(ev, '#2563EB'), unsafe_allow_html=True)
            if ev < 60:
                st.caption("Thin evidence — the score rests on relatively few checks. "
                           "That means less is known, not that the stock is worse.")

            for reason in json.loads(r['reasons'] or '[]'):
                st.error(reason)
            for warn in json.loads(r['warnings'] or '[]'):
                st.warning(warn)

            layers = json.loads(r['layers_json'] or '{}')
            for name, layer in layers.items():
                q = layer.get('pct')
                q_txt = "not assessed" if q is None else f"{q:.0f}%"
                st.markdown(
                    f"**{name.title()}** &nbsp;<span style='color:#6B7280;font-size:0.85rem;'>"
                    f"{layer['awarded']:.1f} of {layer['attempted']:.1f} weighted points "
                    f"(quality {q_txt}, coverage {layer.get('coverage', 0):.0f}%)</span>",
                    unsafe_allow_html=True)
                rows = []
                for c in layer.get('checks', []):
                    mark = {'OK': '', 'UNKNOWN': '?', 'NA': '—'}.get(c['status'], '')
                    pts = (f"{c['awarded']:.1f}/{c['attempted']:.0f}"
                           if c['attempted'] else mark or '—')
                    rows.append({'Check': c['name'], 'Points': pts, 'Detail': c['detail']})
                if rows:
                    st.markdown(theme.render_table(pd.DataFrame(rows)),
                                unsafe_allow_html=True)


def page_classification():
    st.title("Stock Cap Classification")
    st.caption("Source: AMFI official market-cap categorization")
    summary = db.cap_classification_summary()
    if not summary.empty:
        st.caption(f"Data period: {summary.iloc[0]['source_period']}")
        cols = st.columns(len(summary))
        for col, (_, row) in zip(cols, summary.iterrows()):
            with col:
                st.markdown(theme.kpi_card(row['cap_type'], int(row['count']), tone="accent"), unsafe_allow_html=True)

    st.markdown("### Symbol Lookup")
    sym_col, _spacer = st.columns([1, 1])
    with sym_col:
        sym = st.text_input("Search by NSE symbol or company name (e.g. RELIANCE or Zee)")
    if sym:
        res = db.lookup_symbol(sym)
        if res.empty:
            st.warning(f"No match for '{sym}' (would be treated as Micro Cap / unknown until classified).")
        else:
            st.markdown(theme.render_table(res), unsafe_allow_html=True)


def page_set_targets():
    st.title("Set Targets")
    st.caption("Set target price and have-interest for open trades, directly in the table — "
               "read by the Oracle-based GTT bot (main_gtt_oracle.py) on its next run.")

    open_trades = db.open_trades_for_targets()
    if open_trades.empty:
        st.info("No open trades.")
        return

    HAVE_INTEREST_OPTIONS = ["", "Have Interest", "No Interest"]

    base = open_trades[['trade_id', 'buy_date', 'stock_name', 'my_buy_price', 'target_price', 'have_interest']].copy()
    base['have_interest'] = base['have_interest'].fillna("")
    base['have_interest'] = base['have_interest'].where(base['have_interest'].isin(HAVE_INTEREST_OPTIONS), "")
    base['target_price'] = pd.to_numeric(base['target_price'], errors='coerce').fillna(0.0)
    base = base.set_index('trade_id')
    base.columns = ['Date', 'Script Name', 'Purchase Price', 'Target Price', 'Has Interest']

    edited = st.data_editor(
        base,
        column_config={
            'Date': st.column_config.DateColumn(disabled=True),
            'Script Name': st.column_config.TextColumn(disabled=True),
            'Purchase Price': st.column_config.NumberColumn(disabled=True, format="₹%.2f"),
            'Target Price': st.column_config.NumberColumn(format="₹%.2f", min_value=0.0, step=1.0),
            'Has Interest': st.column_config.SelectboxColumn(options=HAVE_INTEREST_OPTIONS),
        },
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        key='targets_editor',
    )

    if st.button("Save changes"):
        timeframes = open_trades.set_index('trade_id')['timeframe']
        changed = 0
        for tid, row in edited.iterrows():
            orig = base.loc[tid]
            if row['Target Price'] != orig['Target Price'] or row['Has Interest'] != orig['Has Interest']:
                db.update_trade_target(tid, row['Target Price'], row['Has Interest'], timeframes.loc[tid])
                changed += 1
        if changed:
            st.success(f"Updated {changed} trade(s). GTT bot will pick these up on its next run.")
            st.rerun()
        else:
            st.info("No changes to save.")


def page_settings():
    st.title("Settings & Edits")
    st.warning("Changes here directly affect live trading budget calculations. Edit carefully.")

    st.markdown("### Total Portfolio Budget")
    s = db.portfolio_summary()
    with st.form("budget_form"):
        budget_col, _spacer = st.columns([1, 3])
        with budget_col:
            new_budget = st.number_input("Total budget (₹)", value=float(s['total_budget']),
                                         min_value=0.0, step=1000.0, format="%.2f")
        if st.form_submit_button("Update total budget"):
            rows = db.update_total_budget(new_budget)
            st.success(f"Total budget updated to {fmt(new_budget)} ({rows} row).")
            st.rerun()

    st.markdown("### Category Allocations")
    cat = db.category_status()
    if not cat.empty:
        cat_col, _spacer = st.columns([1, 2])
        with cat_col:
            category = st.selectbox("Category to edit", cat['category_name'].tolist())
        st_df = db.stock_type_status(category)
        crow = cat[cat['category_name'] == category].iloc[0]
        pct_by_cap = {r['cap_type']: r['pct'] for _, r in st_df.iterrows()} if not st_df.empty else {}
        with st.form("cat_form"):
            ap_col, _spacer = st.columns([1, 3])
            with ap_col:
                ap = st.number_input("Category allocation % of portfolio",
                                     value=float(crow['allocation_pct']), min_value=0.0, max_value=100.0, step=1.0)
            st.caption("Max % in a single stock, by cap type")
            cola, colb, colc, cold, _spacer = st.columns([1, 1, 1, 1, 4])
            lp = cola.number_input("Large Cap %", value=float(pct_by_cap.get('Large Cap', 0)), min_value=0.0, max_value=100.0, step=1.0)
            mp = colb.number_input("Mid Cap %", value=float(pct_by_cap.get('Mid Cap', 0)), min_value=0.0, max_value=100.0, step=1.0)
            sp = colc.number_input("Small Cap %", value=float(pct_by_cap.get('Small Cap', 0)), min_value=0.0, max_value=100.0, step=1.0)
            mcp = cold.number_input("Micro Cap %", value=float(pct_by_cap.get('Micro Cap', 0)), min_value=0.0, max_value=100.0, step=1.0)
            if st.form_submit_button("Update category allocation"):
                rows = db.update_category_pct(category, ap, lp, mp, sp, mcp)
                st.success(f"'{category}' allocation updated ({rows} row).")
                st.rerun()

    st.markdown("### Manually Close a Trade")
    open_trades = db.trades(status='Open')
    if open_trades.empty:
        st.info("No open trades to close.")
    else:
        options = {f"#{r['trade_id']} {r['stock_name']} ({r['symbol']}) — {fmt(r['invested_amount'])}": r['trade_id']
                   for _, r in open_trades.iterrows()}
        with st.form("close_form"):
            trade_col, _spacer = st.columns([2, 1])
            with trade_col:
                choice = st.selectbox("Open trade", list(options.keys()))
            price_col, date_col, _spacer = st.columns([1, 1, 2])
            with price_col:
                sell_price = st.number_input("Sell price (₹)", min_value=0.0, step=1.0, format="%.2f")
            with date_col:
                sell_date = st.date_input("Sell date", value=datetime.date.today())
            if st.form_submit_button("Close trade"):
                if sell_price <= 0:
                    st.error("Enter a valid sell price.")
                else:
                    tid = options[choice]
                    rows = db.close_trade(tid, sell_price, sell_date.strftime('%Y-%m-%d'))
                    st.success(f"Trade #{tid} closed ({rows} row). Budget recycled.")
                    st.rerun()


def page_orders():
    st.title("Open Orders")
    sync_status = kite_data.sync_status()
    if sync_status:
        lines = [f"**{row['account_label']}**: {row['synced_at'].strftime('%Y-%m-%d %H:%M:%S')}" for row in sync_status]
        st.caption("Order book and GTT triggers as of last sync — " + " · ".join(lines) +
                   ". Not live — use 'Sync Kite Data' on Overview to refresh.")
    else:
        st.caption("Not synced yet — go to Overview and click 'Sync Kite Data'.")

    try:
        view_options = ["Orders", "GTT Triggers"]
        if 'orders_view' not in st.session_state:
            st.session_state['orders_view'] = view_options[0]
        view = st.radio("View", view_options, horizontal=True, key='orders_view', label_visibility="collapsed")

        if view == "Orders":
            odf = pd.DataFrame(kite_data.get_orders())
            if odf.empty:
                st.info("No orders today.")
            else:
                status_options = ['All'] + sorted(odf['status'].dropna().unique().tolist())
                if st.session_state.get('orders_status_filter') not in status_options:
                    st.session_state['orders_status_filter'] = 'All'
                status_col, _spacer = st.columns([1, 3])
                with status_col:
                    status = st.selectbox("Status", status_options, key='orders_status_filter')
                fdf = odf if status == 'All' else odf[odf['status'] == status]
                st.caption(f"{len(fdf)} order(s)")
                cols = [c for c in ['order_timestamp', 'tradingsymbol', 'transaction_type', 'order_type',
                                    'quantity', 'filled_quantity', 'price', 'average_price', 'status', 'account_label']
                        if c in fdf.columns]
                display = fdf[cols].rename(columns={'tradingsymbol': 'symbol', 'account_label': 'account'}).copy()
                if 'order_timestamp' in display.columns:
                    display['order_timestamp'] = display['order_timestamp'].astype(str).str.split(' ').str[0]
                st.markdown(theme.render_table(display, money_cols=['price', 'average_price'],
                                               status_col='status'), unsafe_allow_html=True)
        else:
            gdf = pd.DataFrame(kite_data.get_gtts())
            if gdf.empty:
                st.info("No GTT triggers.")
            else:
                status_options = ['All'] + sorted(gdf['status'].dropna().unique().tolist())
                if st.session_state.get('gtt_status_filter') not in status_options:
                    st.session_state['gtt_status_filter'] = 'All'
                status_col, _spacer = st.columns([1, 3])
                with status_col:
                    status = st.selectbox("Status", status_options, key='gtt_status_filter')
                fdf = gdf if status == 'All' else gdf[gdf['status'] == status]
                st.caption(f"{len(fdf)} GTT trigger(s)")
                fdf = fdf.copy()
                for date_col in ('created_at', 'expires_at'):
                    if date_col in fdf.columns:
                        fdf[date_col] = fdf[date_col].astype(str).str.split(' ').str[0]
                fdf = fdf.rename(columns={'account_label': 'account'})
                st.markdown(theme.render_table(fdf, money_cols=['trigger_price', 'last_price', 'sell_price'],
                                               status_col='status'), unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"Couldn't read the Kite snapshot for order data. ({e})")


# ── Main ─────────────────────────────────────────────────────────

PAGES = [
    "Overview",
    "Category Drill-Down",
    "Performance",
    "Set Targets",
    "Trades Explorer",
    "Recommendations",
    "Conviction",
    "Needs Review",
    "Open Orders",
    "Classification",
    "Settings & Edits",
]

def main():
    if not check_login():
        return

    st.sidebar.markdown("## Capital Ledger")
    st.sidebar.caption(f"Signed in as **{st.session_state.get('user')}**")
    page = st.sidebar.radio("Navigate", PAGES, label_visibility="collapsed")
    if st.sidebar.button("Sign out"):
        st.session_state.clear()
        if 'user' in cookies:
            del cookies['user']
        if 'exp' in cookies:
            del cookies['exp']
        cookies.save()
        st.rerun()

    st.sidebar.divider()
    st.sidebar.caption("Data source: Oracle Autonomous DB")

    try:
        if page == "Overview":
            page_overview()
        elif page == "Category Drill-Down":
            page_drilldown()
        elif page == "Performance":
            page_performance()
        elif page == "Conviction":
            page_conviction()
        elif page == "Set Targets":
            page_set_targets()
        elif page == "Trades Explorer":
            page_trades()
        elif page == "Recommendations":
            page_recommendations()
        elif page == "Needs Review":
            page_needs_review()
        elif page == "Open Orders":
            page_orders()
        elif page == "Classification":
            page_classification()
        elif page == "Settings & Edits":
            page_settings()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.exception(e)


if __name__ == '__main__':
    main()
