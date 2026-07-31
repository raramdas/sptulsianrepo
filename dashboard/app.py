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
  - GTT Coverage         open trades with a target but no GTT placed yet
  - Set Targets          set target price / timeframe / have-interest per trade
  - Trades Explorer      filterable, searchable, CSV export
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
    st.caption("Fetched directly from your Zerodha account — holdings, GTTs, and today's order "
               "book. This is the actual broker state; the budget tracking below is Capital "
               "Ledger's own bookkeeping in Oracle and can drift from it.")

    if st.button("Refresh Kite data"):
        kite_data.get_holdings.clear()
        kite_data.get_gtts.clear()
        kite_data.get_orders.clear()
        st.rerun()

    open_trades_df = db.trades(status='Open')
    realized_pnl = db.performance_summary()['total_realized']

    try:
        hs = kite_data.holdings_summary()
        gs = kite_data.gtt_summary()
        os_ = kite_data.orders_today_summary()

        k1, k2, k3 = st.columns(3)
        with k1:
            st.markdown(theme.kpi_card("Holdings (live)", hs['count'], tone="accent"), unsafe_allow_html=True)
        with k2:
            st.markdown(theme.kpi_card("Invested (live)", fmt(hs['invested'])), unsafe_allow_html=True)
        with k3:
            st.markdown(theme.kpi_card("Current Value (live)", fmt(hs['current_value'])), unsafe_allow_html=True)

        p1, p2, p3 = st.columns(3)
        with p1:
            tone = "positive" if realized_pnl >= 0 else "negative"
            st.markdown(theme.kpi_card("Realized P&L (all-time)", fmt(realized_pnl), tone=tone), unsafe_allow_html=True)
        with p2:
            tone = "positive" if hs['pnl'] >= 0 else "negative"
            st.markdown(theme.kpi_card("Unrealized P&L (live)", fmt(hs['pnl']), tone=tone), unsafe_allow_html=True)
        with p3:
            total_pnl = realized_pnl + hs['pnl']
            tone = "positive" if total_pnl >= 0 else "negative"
            st.markdown(theme.kpi_card("Total P&L (Realized + Unrealized)", fmt(total_pnl), tone=tone), unsafe_allow_html=True)

        k5, k6, k7 = st.columns(3)
        with k5:
            st.markdown(theme.kpi_card("Active GTTs", gs['active'], tone="accent"), unsafe_allow_html=True)
        with k6:
            st.markdown(theme.kpi_card("Total GTTs (any status)", gs['total']), unsafe_allow_html=True)
        with k7:
            order_line = " · ".join(f"{k}: {v}" for k, v in os_['by_status'].items()) or "none"
            st.markdown(theme.kpi_card("Today's Orders", os_['total']), unsafe_allow_html=True)
            st.caption(order_line)

        with st.expander(f"Holdings detail ({hs['count']})"):
            hdf = pd.DataFrame(kite_data.get_holdings())
            if not hdf.empty:
                cols = [c for c in ['tradingsymbol', 'quantity', 'average_price', 'last_price', 'pnl']
                        if c in hdf.columns]
                st.markdown(theme.render_table(hdf[cols], money_cols=['average_price', 'last_price', 'pnl'],
                                               gain_col='pnl'), unsafe_allow_html=True)

        st.markdown("### Category Performance (Live)")
        st.caption("Live Kite holdings attributed to each category via Oracle's category tagging, "
                   "plus that category's all-time realized P&L. Categories with no live holdings "
                   "mapped to them are omitted here (see Budget Tracking below for those).")
        cat_live, unmapped = kite_data.live_category_breakdown(open_trades_df)
        realized_by_cat = db.category_pnl_breakdown()
        if not cat_live.empty:
            realized_map = dict(zip(realized_by_cat['category_name'], realized_by_cat['realized_pnl'])) \
                if not realized_by_cat.empty else {}
            for _, row in cat_live.iterrows():
                st.markdown(
                    theme.category_performance_card(
                        row['category_name'], row['invested'], row['current_value'], row['pnl'],
                        realized_pnl=realized_map.get(row['category_name'])
                    ), unsafe_allow_html=True
                )
        else:
            st.info("No live holdings could be matched to a category yet.")

        if not unmapped.empty:
            unmapped_value = unmapped['current_value'].sum()
            with st.expander(f"⚠ Unmapped holdings — in Kite but not in any open Oracle trade "
                              f"({len(unmapped)}, {fmt(unmapped_value)} current value)"):
                st.caption("These symbols show up in your live Kite holdings but have no matching "
                           "'Open' row in Oracle's trades table — this is exactly the gap between "
                           "Budget Tracking below and your real broker state. Reconcile by adding/"
                           "correcting the corresponding trade rows.")
                st.markdown(theme.render_table(
                    unmapped, money_cols=['average_price', 'last_price', 'invested', 'current_value', 'pnl'],
                    gain_col='pnl'), unsafe_allow_html=True)
    except Exception as e:
        st.warning(f"Couldn't reach Kite for live data — showing Oracle-only figures below. ({e})")

    st.divider()

    st.markdown("### Budget Tracking (Oracle)")
    st.caption("Capital Ledger's own category/budget bookkeeping — not Kite's live account state.")
    s = db.portfolio_summary()

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
    st.caption("Realized P&L from closed trades. Open positions aren't counted until closed.")

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

    best, worst = summary['best_trade'], summary['worst_trade']
    if best is not None and worst is not None:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(theme.kpi_card(
                f"Best Trade — {best['stock_name']} ({best['symbol']})",
                fmt(best['my_gain_loss']), tone="positive"), unsafe_allow_html=True)
        with c2:
            st.markdown(theme.kpi_card(
                f"Worst Trade — {worst['stock_name']} ({worst['symbol']})",
                fmt(worst['my_gain_loss']), tone="negative"), unsafe_allow_html=True)

    st.markdown("### Cumulative Realized P&L")
    trend = db.cumulative_pnl_by_month()
    if trend.empty:
        st.info("No closed trades yet — nothing to chart.")
    else:
        st.plotly_chart(theme.render_line_chart(trend, 'month', 'cumulative_pnl'), use_container_width=True)

    st.markdown("### Realized P&L by Category")
    cat_pnl = db.category_pnl_breakdown()
    if cat_pnl.empty:
        st.info("No closed trades yet.")
    else:
        st.markdown(
            theme.render_table(cat_pnl.rename(columns={'realized_pnl': 'realized_pnl'}),
                               money_cols=['realized_pnl'], gain_col='realized_pnl'),
            unsafe_allow_html=True
        )

    st.markdown("### Closed Trades")
    closed = db.realized_performance()
    if closed.empty:
        st.info("No closed trades yet.")
    else:
        money_cols = ['my_buy_price', 'invested_amount', 'my_sell_price', 'my_gain_loss']
        display_cols = [c for c in closed.columns if c != 'trade_id']
        st.markdown(theme.render_table(closed[display_cols], money_cols=money_cols, gain_col='my_gain_loss'),
                    unsafe_allow_html=True)


def page_gtt_coverage():
    st.title("GTT Coverage")
    st.caption("Open trades with a target price set: which ones already have a GTT sell order placed, "
               "and which are still missing one.")

    cov = db.gtt_coverage()
    missing_target = db.open_trades_missing_target()

    covered = cov[cov['gtt_id'].notna()] if not cov.empty else cov
    missing_gtt = cov[cov['gtt_id'].isna()] if not cov.empty else cov

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(theme.kpi_card("GTT Placed", len(covered), tone="positive"), unsafe_allow_html=True)
    with c2:
        tone = "negative" if len(missing_gtt) > 0 else "default"
        st.markdown(theme.kpi_card("Missing GTT (target set)", len(missing_gtt), tone=tone), unsafe_allow_html=True)
    with c3:
        st.markdown(theme.kpi_card("No Target Set Yet", len(missing_target)), unsafe_allow_html=True)

    st.markdown("### Missing GTT — needs an order placed")
    if missing_gtt.empty:
        st.success("Every open trade with a target price has a GTT order placed.")
    else:
        money_cols = ['my_buy_price', 'invested_amount', 'target_price']
        display_cols = [c for c in missing_gtt.columns if c != 'trade_id']
        st.markdown(theme.render_table(missing_gtt[display_cols], money_cols=money_cols), unsafe_allow_html=True)
        csv = missing_gtt.to_csv(index=False).encode('utf-8')
        st.download_button("Download missing-GTT list (CSV)", csv, "missing_gtt.csv", "text/csv")

    st.markdown("### GTT Already Placed")
    if covered.empty:
        st.info("No trades with a GTT placed yet.")
    else:
        money_cols = ['my_buy_price', 'invested_amount', 'target_price']
        display_cols = [c for c in covered.columns if c != 'trade_id']
        st.markdown(theme.render_table(covered[display_cols], money_cols=money_cols), unsafe_allow_html=True)

    if not missing_target.empty:
        with st.expander(f"Open trades with no target set yet ({len(missing_target)}) — set on the Set Targets page"):
            money_cols = ['my_buy_price', 'invested_amount']
            display_cols = [c for c in missing_target.columns if c != 'trade_id']
            st.markdown(theme.render_table(missing_target[display_cols], money_cols=money_cols), unsafe_allow_html=True)


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

PAGES = [
    "Overview",
    "Category Drill-Down",
    "Performance",
    "GTT Coverage",
    "Set Targets",
    "Trades Explorer",
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
        elif page == "GTT Coverage":
            page_gtt_coverage()
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
