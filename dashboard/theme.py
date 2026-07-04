#!/usr/bin/env python3
"""
theme.py — visual design system for the Stock Bot dashboard.

Concept: a capital-allocation LEDGER / VAULT. SPTulsian's own category names
(Little Gems, Big Gems, Multibagger) are literally treasure names, so the
aesthetic leans into that: deep ink background, antique gold accents, and a
monospace ledger typeface for every number — this should read like a
statement of account, not a generic admin template.

Colors
  ink        #0F1620  page background
  panel      #171F2C  card / surface background
  panel-line #232D3D  borders, dividers
  gold       #C9A227  brand accent — budget, headers, active nav
  gold-dim   #8C7220  secondary gold (hover, subtle fills)
  ink-text   #E8E6DF  primary text (warm parchment white)
  muted      #8A94A6  secondary text, labels, captions
  emerald    #4C9A6A  positive (available budget, gains)
  rust       #C1553D  negative (over budget, losses, errors)

Type
  display : 'Fraunces'        — page titles / section headers only
  body    : 'Inter'           — everything else (labels, buttons, prose)
  mono    : 'JetBrains Mono'  — every number: money, qty, percentages, tables
"""
import streamlit as st

INK        = "#0F1620"
PANEL      = "#171F2C"
PANEL_LINE = "#232D3D"
GOLD       = "#C9A227"
GOLD_DIM   = "#8C7220"
TEXT       = "#E8E6DF"
MUTED      = "#8A94A6"
EMERALD    = "#4C9A6A"
RUST       = "#C1553D"


CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {{
    font-family: 'Inter', sans-serif;
}}

/* Base page + text colors — belt-and-suspenders on top of config.toml's
   base="dark" theme, so nothing ever renders dark-on-dark. */
.stApp {{
    background-color: {INK};
    color: {TEXT} !important;
}}
.stApp, .stApp p, .stApp span, .stApp label, .stApp div {{
    color: {TEXT};
}}
[data-testid="stCaptionContainer"], .stCaption, small {{
    color: {MUTED} !important;
}}

/* Hide default Streamlit chrome for a cleaner ledger feel */
#MainMenu {{visibility: hidden;}}
footer {{visibility: hidden;}}
header[data-testid="stHeader"] {{background: transparent;}}

/* Page titles use the display serif, sparingly */
h1, h2, h3 {{
    font-family: 'Fraunces', serif !important;
    color: {TEXT} !important;
    letter-spacing: -0.01em;
}}
h1 {{ font-weight: 600 !important; border-bottom: 1px solid {PANEL_LINE}; padding-bottom: 0.6rem; margin-bottom: 1.2rem; }}

/* Sidebar — dark vault panel with guaranteed-legible text */
section[data-testid="stSidebar"] {{
    background-color: {PANEL};
    border-right: 1px solid {PANEL_LINE};
}}
section[data-testid="stSidebar"] * {{
    color: {TEXT} !important;
}}
section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {{
    color: {MUTED} !important;
}}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] label {{
    padding: 0.35rem 0;
    font-size: 0.95rem;
}}

/* Buttons */
.stButton button, .stFormSubmitButton button {{
    background-color: {GOLD};
    color: {INK} !important;
    border: none;
    font-family: 'Inter', sans-serif;
    font-weight: 600;
    border-radius: 6px;
    transition: background-color 0.15s ease;
}}
.stButton button *, .stFormSubmitButton button * {{ color: {INK} !important; }}
.stButton button:hover, .stFormSubmitButton button:hover {{
    background-color: {GOLD_DIM};
}}

/* Inputs */
.stTextInput input, .stNumberInput input, .stSelectbox [data-baseweb="select"] > div, .stDateInput input {{
    background-color: {PANEL} !important;
    color: {TEXT} !important;
    border: 1px solid {PANEL_LINE} !important;
    font-family: 'JetBrains Mono', monospace !important;
}}

/* Ledger KPI cards */
.ledger-card {{
    background-color: {PANEL};
    border: 1px solid {PANEL_LINE};
    border-left: 3px solid {GOLD};
    border-radius: 8px;
    padding: 1.1rem 1.3rem;
    margin-bottom: 0.6rem;
    box-shadow: 0 2px 8px rgba(0,0,0,0.25);
}}
.ledger-card .label {{
    font-family: 'Inter', sans-serif;
    font-size: 0.72rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {MUTED} !important;
    margin-bottom: 0.4rem;
}}
.ledger-card .value {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.6rem;
    font-weight: 600;
    color: {TEXT} !important;
    word-break: break-word;
}}
.ledger-card.tone-positive .value {{ color: {EMERALD} !important; }}
.ledger-card.tone-negative .value {{ color: {RUST} !important; }}
.ledger-card.tone-gold .value {{ color: {GOLD} !important; }}

/* Category allocation bar */
.cat-row {{ margin-bottom: 1.2rem; }}
.cat-row .cat-header {{
    display: flex; justify-content: space-between; align-items: baseline;
    flex-wrap: wrap;
    gap: 0.25rem 0.75rem;
    font-family: 'Inter', sans-serif;
    margin-bottom: 0.35rem;
}}
.cat-row .cat-name {{ font-weight: 600; color: {TEXT} !important; font-size: 0.95rem; }}
.cat-row .cat-figures {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    color: {MUTED} !important;
}}
.cat-track {{
    width: 100%; height: 10px;
    background-color: {PANEL_LINE};
    border-radius: 5px;
    overflow: hidden;
}}
.cat-fill {{
    height: 100%;
    background: linear-gradient(90deg, {GOLD_DIM}, {GOLD});
    border-radius: 5px 0 0 5px;
}}
.cat-fill.over {{
    background: linear-gradient(90deg, {RUST}, #E0715A);
}}

/* Ledger table — horizontally scrollable on small screens instead of squashing */
.ledger-table-wrap {{
    overflow-x: auto;
    border-radius: 8px;
    border: 1px solid {PANEL_LINE};
}}
.ledger-table {{
    width: 100%;
    min-width: 640px;
    border-collapse: collapse;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
}}
.ledger-table th {{
    text-align: left;
    font-family: 'Inter', sans-serif;
    font-size: 0.7rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: {MUTED} !important;
    background-color: {PANEL};
    border-bottom: 1px solid {GOLD_DIM};
    padding: 0.6rem 0.7rem;
    position: sticky;
    top: 0;
}}
.ledger-table td {{
    padding: 0.55rem 0.7rem;
    border-bottom: 1px solid {PANEL_LINE};
    color: {TEXT} !important;
    white-space: nowrap;
}}
.ledger-table tr:nth-child(even) td {{ background-color: rgba(255,255,255,0.02); }}
.ledger-table td.num {{ text-align: right; }}
.ledger-table td.status-open {{ color: {EMERALD} !important; font-weight: 600; }}
.ledger-table td.status-closed {{ color: {MUTED} !important; }}
.ledger-table td.status-error, .ledger-table td.status-skipped {{ color: {RUST} !important; }}

/* Login form — st.form renders as one real container, safe to style directly */
div[data-testid="stForm"] {{
    background-color: {PANEL};
    border: 1px solid {PANEL_LINE};
    border-top: 3px solid {GOLD};
    border-radius: 10px;
    padding: 1.75rem;
    box-shadow: 0 8px 24px rgba(0,0,0,0.35);
}}

/* ── Mobile responsiveness ───────────────────────────────────── */
@media (max-width: 640px) {{
    h1 {{ font-size: 1.6rem !important; }}
    h2, h3 {{ font-size: 1.2rem !important; }}
    .ledger-card {{ padding: 0.85rem 1rem; }}
    .ledger-card .value {{ font-size: 1.25rem; }}
    .cat-row .cat-header {{ flex-direction: column; align-items: flex-start; }}
    .login-wrap {{ margin: 1rem auto 0 auto; padding: 1.5rem; }}
    div[data-testid="stForm"] {{ padding: 1.25rem; }}
    section[data-testid="stSidebar"] {{ min-width: 100% !important; }}
}}
</style>
"""


def inject():
    st.markdown(CSS, unsafe_allow_html=True)


def kpi_card(label, value, tone="default"):
    """Return HTML for a single ledger-style KPI card."""
    tone_class = f"tone-{tone}" if tone != "default" else ""
    return f"""
    <div class="ledger-card {tone_class}">
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


def render_gauge(utilization_pct, title="Portfolio Deployed"):
    """Signature element: a vault-dial style gauge for budget utilization."""
    import plotly.graph_objects as go
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=utilization_pct,
        number={'suffix': "%", 'font': {'family': 'JetBrains Mono', 'size': 36, 'color': TEXT}},
        title={'text': title, 'font': {'family': 'Inter', 'size': 14, 'color': MUTED}},
        gauge={
            'axis': {'range': [0, 100], 'tickcolor': MUTED, 'tickfont': {'color': MUTED, 'size': 10}},
            'bar': {'color': GOLD, 'thickness': 0.3},
            'bgcolor': PANEL,
            'borderwidth': 1,
            'bordercolor': PANEL_LINE,
            'steps': [
                {'range': [0, 100], 'color': INK},
            ],
        }
    ))
    fig.update_layout(
        paper_bgcolor=PANEL,
        font={'color': TEXT},
        height=260,
        margin=dict(l=30, r=30, t=50, b=20),
    )
    return fig


def render_table(df, money_cols=None, status_col=None):
    """Return HTML for a ledger-styled table from a DataFrame."""
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
