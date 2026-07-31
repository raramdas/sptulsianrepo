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
  - Orders               live Kite order book + GTT triggers, filterable
  - Classification       AMFI cap-type lookup
  - Settings & Edits     total budget, category %, manually close a trade
"""
import os
import time
import datetime
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/.env')

import db
import theme
import kite_data

st.set_page_config(page_title="Stock Bot — Capital Ledger", page_icon="\U0001F4CA", layout="wide")
theme.inject()

# ── Simple auth with brute-force protection ──────────────────────
MAX_ATTEMPTS = 5
LOCKOUT_SECS = 300

def load_users():
    raw = os.environ.get('DASH_USERS', 'admin:changeme')
    users = {}
    for pair in raw.split(','):
        if ':' in pair:
            u, p = pair.split(':', 1)
            users[u.strip()] = p.strip()
    return users

USERS = load_users()

def check_login():
    if st.session_state.get('authenticated'):
        return True

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
            submitted = st.form_submit_button("Sign in")
            if submitted:
                if USERS.get(username) == password:
                    st.session_state['authenticated'] = True
                    st.session_state['user'] = username
                    st.session_state['failed_attempts'] = 0
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
    synced_at = kite_data.last_synced_at()
    if synced_at:
        st.caption(f"From your Zerodha account as of last sync: **{synced_at.strftime('%Y-%m-%d %H:%M:%S')}**. "
                   "This isn't live — click Sync to refresh. The budget tracking below is Capital "
                   "Ledger's own bookkeeping in Oracle and can drift from this snapshot.")
    else:
        st.caption("Not synced yet — click 'Sync Kite Data' below to pull your holdings, GTTs, "
                   "and order book from Zerodha.")

    if st.button("Sync Kite Data"):
        with st.spinner("Logging into Kite and syncing…"):
            try:
                result = kite_data.sync_now()
                st.success(f"Synced: {result['holdings']} holdings, {result['gtts']} GTTs, "
                           f"{result['orders']} orders.")
            except Exception as e:
                st.error(f"Sync failed: {e}")

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
            display_cols = ['symbol', 'quantity', 'average_price', 'last_price', 'pnl']
            money_cols = ['average_price', 'last_price', 'pnl']

            def _render_holdings(df):
                d = df[display_cols].copy()
                d['quantity'] = d['quantity'].astype(int)
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

    st.markdown(f"### Trades in {category}")
    tdf = db.trades(category=category)
    _render_trades_table(tdf)


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
    money_cols = ['my_buy_price', 'invested_amount', 'target_price', 'my_sell_price', 'my_gain_loss']
    display_cols = [c for c in tdf.columns if c not in ('trade_id',)]
    st.markdown(theme.render_table(tdf[display_cols], money_cols=money_cols, status_col='status', gain_col='my_gain_loss'),
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
    st.caption(f"{len(display)} recommendation(s)")
    st.markdown(
        theme.render_table(display[['Date', 'Stock', 'Purchase Price', 'Target Price', 'Status']],
                           money_cols=['Purchase Price', 'Target Price'], status_col='Status'),
        unsafe_allow_html=True
    )


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
        new_budget = st.number_input("Total budget (₹)", value=float(s['total_budget']),
                                     min_value=0.0, step=1000.0, format="%.2f")
        if st.form_submit_button("Update total budget"):
            rows = db.update_total_budget(new_budget)
            st.success(f"Total budget updated to {fmt(new_budget)} ({rows} row).")
            st.rerun()

    st.markdown("### Category Allocations")
    cat = db.category_status()
    if not cat.empty:
        category = st.selectbox("Category to edit", cat['category_name'].tolist())
        st_df = db.stock_type_status(category)
        crow = cat[cat['category_name'] == category].iloc[0]
        pct_by_cap = {r['cap_type']: r['pct'] for _, r in st_df.iterrows()} if not st_df.empty else {}
        with st.form("cat_form"):
            ap = st.number_input("Category allocation % of portfolio",
                                 value=float(crow['allocation_pct']), min_value=0.0, max_value=100.0, step=1.0)
            cola, colb, colc, cold = st.columns(4)
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
            choice = st.selectbox("Open trade", list(options.keys()))
            sell_price = st.number_input("Sell price (₹)", min_value=0.0, step=1.0, format="%.2f")
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
    st.title("Orders")
    synced_at = kite_data.last_synced_at()
    if synced_at:
        st.caption(f"Order book and GTT triggers as of last sync: **{synced_at.strftime('%Y-%m-%d %H:%M:%S')}** "
                   "— not live. Use 'Sync Kite Data' on Overview to refresh.")
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
                status = st.selectbox("Status", status_options, key='orders_status_filter')
                fdf = odf if status == 'All' else odf[odf['status'] == status]
                st.caption(f"{len(fdf)} order(s)")
                cols = [c for c in ['order_timestamp', 'tradingsymbol', 'transaction_type', 'order_type',
                                    'quantity', 'filled_quantity', 'price', 'average_price', 'status']
                        if c in fdf.columns]
                display = fdf[cols].rename(columns={'tradingsymbol': 'symbol'}).copy()
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
                status = st.selectbox("Status", status_options, key='gtt_status_filter')
                fdf = gdf if status == 'All' else gdf[gdf['status'] == status]
                st.caption(f"{len(fdf)} GTT trigger(s)")
                fdf = fdf.copy()
                for date_col in ('created_at', 'expires_at'):
                    if date_col in fdf.columns:
                        fdf[date_col] = fdf[date_col].astype(str).str.split(' ').str[0]
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
    "Orders",
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
        elif page == "Set Targets":
            page_set_targets()
        elif page == "Trades Explorer":
            page_trades()
        elif page == "Recommendations":
            page_recommendations()
        elif page == "Orders":
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
