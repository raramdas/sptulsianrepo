#!/usr/bin/env python3
"""
app.py — Streamlit dashboard for the Stock Tip Automation + Budget system.
Visual design: capital-allocation ledger / vault (see theme.py for rationale).

Run:
    cd /home/ubuntu/stock_bot_v4/dashboard
    streamlit run app.py --server.port 8501 --server.address 0.0.0.0

Features:
  - Login (basic, credentials from .env), brute-force lockout
  - Overview: deployment gauge, ledger KPI cards, category allocation bars
  - Drill-down: category -> stock-type -> individual trades
  - Trades explorer with filters
  - Cap classification lookup
  - Edit: total budget, category %, manually close a trade
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

st.set_page_config(page_title="Stock Bot — Capital Ledger", page_icon="\U0001F48E", layout="wide")
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
        st.markdown("## \U0001F48E Capital Ledger")
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
    s = db.portfolio_summary()

    col_gauge, col_kpis = st.columns([1, 2])
    with col_gauge:
        st.plotly_chart(theme.render_gauge(s['utilization_pct']), use_container_width=True)
    with col_kpis:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(theme.kpi_card("Total Budget", fmt(s['total_budget']), tone="gold"), unsafe_allow_html=True)
            st.markdown(theme.kpi_card("Available", fmt(s['available']), tone="positive"), unsafe_allow_html=True)
        with c2:
            st.markdown(theme.kpi_card("Invested (Open)", fmt(s['invested'])), unsafe_allow_html=True)
            st.markdown(theme.kpi_card("Open Positions", s['open_positions']), unsafe_allow_html=True)

    st.markdown("### Category Allocation")
    cat = db.category_status()
    if cat.empty:
        st.info("No category data found.")
        return

    for _, row in cat.iterrows():
        st.markdown(
            theme.category_bar(row['category_name'], float(row['category_budget']),
                               float(row['invested']), float(row['available'])),
            unsafe_allow_html=True
        )


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
        st.markdown(theme.kpi_card("Category Budget", fmt(crow['category_budget']), tone="gold"), unsafe_allow_html=True)
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


def _render_trades_table(tdf):
    if tdf.empty:
        st.info("No trades.")
        return
    money_cols = ['my_buy_price', 'invested_amount', 'target_price', 'my_sell_price', 'my_gain_loss']
    display_cols = [c for c in tdf.columns if c not in ('trade_id',)]
    st.markdown(theme.render_table(tdf[display_cols], money_cols=money_cols, status_col='status'),
                unsafe_allow_html=True)


def page_trades():
    st.title("Trades Explorer")
    col1, col2 = st.columns(2)
    with col1:
        status = st.selectbox("Status", ['All', 'Open', 'Closed', 'SKIPPED', 'ERROR'])
    with col2:
        cats = db.category_status()
        cat_options = ['All'] + (cats['category_name'].tolist() if not cats.empty else [])
        category = st.selectbox("Category", cat_options)

    tdf = db.trades(
        status=None if status == 'All' else status,
        category=None if category == 'All' else category,
    )
    st.caption(f"{len(tdf)} trade(s) found")
    _render_trades_table(tdf)


def page_classification():
    st.title("Stock Cap Classification")
    st.caption("Source: AMFI official market-cap categorization")
    summary = db.cap_classification_summary()
    if not summary.empty:
        st.caption(f"Data period: {summary.iloc[0]['source_period']}")
        cols = st.columns(len(summary))
        for col, (_, row) in zip(cols, summary.iterrows()):
            with col:
                st.markdown(theme.kpi_card(row['cap_type'], int(row['count']), tone="gold"), unsafe_allow_html=True)

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
    st.caption("Set the target price, timeframe, and have-interest flag for open trades — "
               "read directly by the Oracle-based GTT bot (main_gtt_oracle.py).")

    open_trades = db.open_trades_for_targets()
    if open_trades.empty:
        st.info("No open trades.")
        return

    options = {}
    for _, r in open_trades.iterrows():
        tgt = fmt(r['target_price']) if pd.notna(r['target_price']) else "— not set —"
        options[f"#{r['trade_id']} {r['stock_name']} ({r['symbol']}) · buy {fmt(r['my_buy_price'])} · target {tgt}"] = r['trade_id']

    choice = st.selectbox("Open trade", list(options.keys()))
    tid = options[choice]
    row = open_trades[open_trades['trade_id'] == tid].iloc[0]

    with st.form("target_form"):
        target_price = st.number_input(
            "Target price (₹)",
            value=float(row['target_price']) if pd.notna(row['target_price']) else 0.0,
            min_value=0.0, step=1.0, format="%.2f",
        )
        timeframe = st.text_input("Timeframe (e.g. '3 Months')", value=row['timeframe'] or "")
        have_interest = st.selectbox(
            "Have Interest (SPTulsian disclosure)",
            ["", "Have Interest", "No Interest"],
            index=["", "Have Interest", "No Interest"].index(row['have_interest']) if row['have_interest'] in ["", "Have Interest", "No Interest"] else 0,
        )
        if st.form_submit_button("Save target"):
            if target_price <= 0:
                st.error("Enter a target price greater than 0.")
            else:
                rows = db.update_trade_target(tid, target_price, have_interest, timeframe)
                st.success(f"Trade #{tid} updated ({rows} row). GTT bot will pick this up on its next run.")
                st.rerun()


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


# ── Main ─────────────────────────────────────────────────────────

def main():
    if not check_login():
        return

    st.sidebar.markdown("## \U0001F48E Capital Ledger")
    st.sidebar.caption(f"Signed in as **{st.session_state.get('user')}**")
    page = st.sidebar.radio("Navigate", [
        "\U0001F4CA Overview",
        "\U0001F50D Category Drill-Down",
        "\U0001F3AF Set Targets",
        "\U0001F4D6 Trades Explorer",
        "\U0001F3F7\uFE0F Classification",
        "\u2699\uFE0F Settings & Edits",
    ], label_visibility="collapsed")
    page = page.split(" ", 1)[1]  # strip icon prefix for routing
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
        elif page == "Set Targets":
            page_set_targets()
        elif page == "Trades Explorer":
            page_trades()
        elif page == "Classification":
            page_classification()
        elif page == "Settings & Edits":
            page_settings()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.exception(e)


if __name__ == '__main__':
    main()
