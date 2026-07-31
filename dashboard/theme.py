#!/usr/bin/env python3
"""
theme.py — visual design system for the Capital Ledger dashboard.

Concept: clean, minimal, data-forward — Stripe/Linear aesthetic. White
background, dark text, blue accent reserved for interactive elements and
brand marks, color on numbers reserved strictly for positive/negative
signal (never decorative). Tabular numerals throughout so money/qty columns
align vertically.

This replaces the earlier gold/vault ("ledger") direction — Rajesh picked
the clean minimal option when shown three style directions.

Colors
  bg          #FFFFFF   page background
  surface     #F8FAFC   subtle panel background (sidebar, hover states)
  border      #E2E8F0   card borders, dividers
  ink         #0F172A   primary text
  muted       #64748B   secondary text, labels, captions
  accent      #2563EB   brand / interactive accent (buttons, active nav, links)
  accent-dim  #DBEAFE   accent tint (active nav background, subtle fills)
  positive    #16A34A   gains, available budget
  negative    #DC2626   losses, over-budget, errors

Type
  body : 'Inter'      — everything (headers included, just heavier weight)
  mono : tabular-nums  — applied via font-feature-settings on Inter itself,
                         not a separate monospace face — keeps the clean
                         look while still aligning digits in tables/KPIs.
"""
import streamlit as st

BG        = "#FFFFFF"
SURFACE   = "#F8FAFC"
BORDER    = "#E2E8F0"
INK       = "#0F172A"
MUTED     = "#64748B"
ACCENT    = "#2563EB"
ACCENT_DIM = "#DBEAFE"
POSITIVE  = "#16A34A"
NEGATIVE  = "#DC2626"


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', -apple-system, sans-serif;
}}

.stApp {{
    background-color: {BG};
    color: {INK} !important;
}}
.stApp, .stApp p, .stApp span, .stApp label, .stApp div {{
    color: {INK};
}}
[data-testid="stCaptionContainer"], .stCaption, small {{
    color: {MUTED} !important;
}}

#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
header[data-testid="stHeader"] {{background: transparent;}}

h1, h2, h3 {{
    font-family: 'Inter', sans-serif !important;
    color: {INK} !important;
    letter-spacing: -0.01em;
    font-weight: 700 !important;
}}
h1 {{ border-bottom: 1px solid {BORDER}; padding-bottom: 0.6rem; margin-bottom: 1.2rem; font-size: 1.7rem !important; }}

/* Sidebar — light surface, blue active state */
section[data-testid="stSidebar"] {{
    background-color: {SURFACE};
    border-right: 1px solid {BORDER};
}}
section[data-testid="stSidebar"] * {{
    color: {INK} !important;
}}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{
    color: {MUTED} !important;
}}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] label {{
    padding: 0.4rem 0.6rem;
    border-radius: 6px;
    font-size: 0.92rem;
    margin-bottom: 0.1rem;
}}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] label:has(input:checked) {{
    background-color: {ACCENT_DIM};
}}

/* Buttons */
.stButton button, .stFormSubmitButton button, .stDownloadButton button {{
    background-color: {INK};
    color: #FFFFFF !important;
    border: 1px solid {INK};
    font-family: 'Inter', sans-serif;
    font-weight: 600;
    border-radius: 6px;
    transition: background-color 0.15s ease;
}}
.stButton button *, .stFormSubmitButton button *, .stDownloadButton button * {{ color: #FFFFFF !important; }}
.stButton button:hover, .stFormSubmitButton button:hover, .stDownloadButton button:hover {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}

/* Inputs */
.stTextInput input, .stNumberInput input, .stSelectbox [data-baseweb="select"] > div, .stDateInput input {{
    background-color: {BG} !important;
    color: {INK} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 6px !important;
    font-variant-numeric: tabular-nums;
}}
.stTextInput input:focus, .stNumberInput input:focus {{
    border-color: {ACCENT} !important;
    box-shadow: 0 0 0 1px {ACCENT} !important;
}}

/* KPI cards */
.kpi-card {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 0.6rem;
}}
.kpi-card .label {{
    font-family: 'Inter', sans-serif;
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: {MUTED} !important;
    margin-bottom: 0.4rem;
}}
.kpi-card .value {{
    font-size: 1.6rem;
    font-weight: 700;
    color: {INK} !important;
    font-variant-numeric: tabular-nums;
    word-break: break-word;
}}
.kpi-card.tone-positive .value {{ color: {POSITIVE} !important; }}
.kpi-card.tone-negative .value {{ color: {NEGATIVE} !important; }}
.kpi-card.tone-accent .value {{ color: {ACCENT} !important; }}

/* Category allocation bar */
.cat-row {{ margin-bottom: 1.2rem; }}
.cat-row .cat-header {{
    display: flex; justify-content: space-between; align-items: baseline;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    margin-bottom: 0.35rem;
}}
.cat-row .cat-name {{ font-weight: 600; color: {INK} !important; font-size: 0.95rem; }}
.cat-row .cat-figures {{
    font-size: 0.8rem;
    color: {MUTED} !important;
    font-variant-numeric: tabular-nums;
}}
.cat-track {{
    width: 100%; height: 8px;
    background-color: {BORDER};
    border-radius: 4px;
    overflow: hidden;
}}
.cat-fill {{
    height: 100%;
    background-color: {ACCENT};
    border-radius: 4px 0 0 4px;
}}
.cat-fill.over {{
    background-color: {NEGATIVE};
}}

/* Table */
.ledger-table-wrap {{
    overflow-x: auto;
    border-radius: 8px;
    border: 1px solid {BORDER};
}}
.ledger-table {{
    width: 100%;
    min-width: 640px;
    border-collapse: collapse;
    font-size: 0.85rem;
    font-variant-numeric: tabular-nums;
}}
.ledger-table th {{
    text-align: left;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: {MUTED} !important;
    background-color: {SURFACE};
    border-bottom: 1px solid {BORDER};
    padding: 0.6rem 0.7rem;
    position: sticky;
    top: 0;
}}
.ledger-table td {{
    padding: 0.55rem 0.7rem;
    border-bottom: 1px solid {BORDER};
    color: {INK} !important;
    white-space: nowrap;
}}
.ledger-table tr:nth-child(even) td {{ background-color: {SURFACE}; }}
.ledger-table td.num {{ text-align: right; }}
.ledger-table td.status-open {{ color: {POSITIVE} !important; font-weight: 600; }}
.ledger-table td.status-closed {{ color: {MUTED} !important; }}
.ledger-table td.status-error, .ledger-table td.status-skipped {{ color: {NEGATIVE} !important; }}
.ledger-table td.gain-pos {{ color: {POSITIVE} !important; font-weight: 600; }}
.ledger-table td.gain-neg {{ color: {NEGATIVE} !important; font-weight: 600; }}

/* Login form */
div[data-testid="stForm"] {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-top: 3px solid {ACCENT};
    border-radius: 10px;
    padding: 1.75rem;
    box-shadow: 0 4px 16px rgba(15,23,42,0.06);
}}

/* Simple badge/pill, used for coverage status etc. */
.pill {{
    display: inline-block;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
}}
.pill.pill-positive {{ background-color: #DCFCE7; color: {POSITIVE}; }}
.pill.pill-negative {{ background-color: #FEE2E2; color: {NEGATIVE}; }}
.pill.pill-muted {{ background-color: {SURFACE}; color: {MUTED}; }}

/* ── Mobile responsiveness ───────────────────────────────────── */
@media (max-width: 640px) {{
    h1 {{ font-size: 1.4rem !important; }}
    h2, h3 {{ font-size: 1.1rem !important; }}
    .kpi-card {{ padding: 0.85rem 1rem; }}
    .kpi-card .value {{ font-size: 1.25rem; }}
    .cat-row .cat-header {{ flex-direction: column; align-items: flex-start; }}
    div[data-testid="stForm"] {{ padding: 1.25rem; }}
    section[data-testid="stSidebar"] {{ min-width: 100% !important; }}
}}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def kpi_card(label, value, tone="default"):
    """Return HTML for a single KPI card."""
    tone_class = f"tone-{tone}" if tone != "default" else ""
    return f"""
    <div class="kpi-card {tone_class}">
        <div class="label">{label}</div>
        <div class="value">{value}</div>
    </div>
    """


def category_bar(name, budget, invested, available):
    """Return HTML for a single category allocation bar."""
    pct = min(invested / budget * 100, 100) if budget else 0
    over = invested > budget
    fill_class = "cat-fill over" if over else "cat-fill"
    return f"""
    <div class="cat-row">
        <div class="cat-header">
            <span class="cat-name">{name}</span>
            <span class="cat-figures">₹{invested:,.0f} / ₹{budget:,.0f} · {pct:.0f}% · avail ₹{available:,.0f}</span>
        </div>
        <div class="cat-track">
            <div class="{fill_class}" style="width:{pct}%;"></div>
        </div>
    </div>
    """


def category_performance_card(name, invested, current_value, pnl, realized_pnl=None):
    """Per-category live performance card — invested/current value plus
    unrealized (and optionally realized) P&L, mirroring the portfolio-level
    KPIs but scoped to one category. Unlike category_bar (budget vs spend),
    P&L can go negative, so this isn't a progress bar."""
    pnl_color = POSITIVE if pnl >= 0 else NEGATIVE
    realized_html = ""
    if realized_pnl is not None:
        r_color = POSITIVE if realized_pnl >= 0 else NEGATIVE
        realized_html = f'<span class="cat-figures">Realized: <b style="color:{r_color};">₹{realized_pnl:,.0f}</b></span>'
    return f"""
    <div class="cat-row">
        <div class="cat-header">
            <span class="cat-name">{name}</span>
            <span class="cat-figures">Invested ₹{invested:,.0f} · Current ₹{current_value:,.0f}</span>
        </div>
        <div class="cat-header" style="margin-top:0.15rem;">
            <span class="cat-figures">Unrealized: <b style="color:{pnl_color};">₹{pnl:,.0f}</b></span>
            {realized_html}
        </div>
    </div>
    """


def pill(text, tone="muted"):
    return f'<span class="pill pill-{tone}">{text}</span>'


def render_gauge(utilization_pct, title="Portfolio Deployed"):
    """Signature element: a clean radial gauge for budget utilization."""
    import plotly.graph_objects as go
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=utilization_pct,
        number={'suffix': "%", 'font': {'family': 'Inter', 'size': 36, 'color': INK}},
        title={'text': title, 'font': {'family': 'Inter', 'size': 14, 'color': MUTED}},
        gauge={
            'axis': {'range': [0, 100], 'tickcolor': MUTED, 'tickfont': {'color': MUTED, 'size': 10}},
            'bar': {'color': ACCENT, 'thickness': 0.3},
            'bgcolor': BG,
            'borderwidth': 1,
            'bordercolor': BORDER,
            'steps': [
                {'range': [0, 100], 'color': SURFACE},
            ],
        }
    ))
    fig.update_layout(
        paper_bgcolor=BG,
        font={'color': INK},
        height=260,
        margin=dict(l=30, r=30, t=50, b=20),
    )
    return fig


def render_line_chart(df, x, y, title=None):
    """Clean line chart for cumulative P&L / trends over time."""
    import plotly.graph_objects as go
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df[x], y=df[y], mode='lines+markers',
        line=dict(color=ACCENT, width=2.5),
        marker=dict(size=5, color=ACCENT),
        fill='tozeroy', fillcolor='rgba(37,99,235,0.08)',
    ))
    fig.update_layout(
        title={'text': title, 'font': {'family': 'Inter', 'size': 14, 'color': MUTED}} if title else None,
        paper_bgcolor=BG, plot_bgcolor=BG,
        font={'color': INK, 'family': 'Inter'},
        margin=dict(l=40, r=20, t=40, b=30),
        height=300,
        xaxis=dict(showgrid=False, color=MUTED),
        yaxis=dict(showgrid=True, gridcolor=BORDER, color=MUTED, zeroline=True, zerolinecolor=BORDER),
    )
    return fig


def render_table(df, money_cols=None, status_col=None, gain_col=None):
    """Return HTML for a styled table from a DataFrame."""
    money_cols = money_cols or []
    cols = list(df.columns)
    header = "".join(f"<th>{c.replace('_', ' ').title()}</th>" for c in cols)
    rows_html = []
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            val = row[c]
            css_class = "num" if c in money_cols else ""
            if status_col and c == status_col:
                status_val = str(val).lower()
                css_class += f" status-{status_val}"
            if gain_col and c == gain_col and val not in (None, ''):
                try:
                    css_class += " gain-pos" if float(val) >= 0 else " gain-neg"
                except (ValueError, TypeError):
                    pass
            if c in money_cols and val not in (None, ''):
                try:
                    val = f"₹{float(val):,.2f}"
                except (ValueError, TypeError):
                    pass
            cells.append(f'<td class="{css_class}">{val if val is not None else ""}</td>')
        rows_html.append(f"<tr>{''.join(cells)}</tr>")
    return f"""
    <div class="ledger-table-wrap">
    <table class="ledger-table">
        <thead><tr>{header}</tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
    </table>
    </div>
    """
