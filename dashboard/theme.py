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
  bg          #FAFAFA   page canvas — cards sit ON this, giving depth
                        without borders doing all the work
  card        #FFFFFF   card/panel surface — KPI cards, forms, table wraps
  surface     #F8FAFC   sidebar background (nearly the same tone as bg,
                        so nav recedes and cards are what pop)
  border      #E5E7EB   card borders, dividers — soft, near-invisible;
                        cards read mainly through a subtle shadow, not a line
  ink         #111827   primary text
  muted       #6B7280   secondary text, labels, captions
  accent      #2563EB   brand / interactive accent (buttons, active nav, links)
  accent-dim  #DBEAFE   accent tint (active nav background, subtle fills)
  positive    #16A34A   gains, available budget
  negative    #DC2626   losses, over-budget, errors
  warning     #F59E0B   deferred/caution states (e.g. SKIPPED — budget-blocked,
                        not actually broken like ERROR)

Type
  body : 'Inter'      — everything (headers included, just heavier weight)
  mono : tabular-nums  — applied via font-feature-settings on Inter itself,
                         not a separate monospace face — keeps the clean
                         look while still aligning digits in tables/KPIs.
"""
import streamlit as st
import pandas as pd

BG        = "#FAFAFA"
CARD      = "#FFFFFF"
SURFACE   = "#F8FAFC"
BORDER    = "#E5E7EB"
INK       = "#111827"
MUTED     = "#6B7280"
ACCENT    = "#2563EB"
ACCENT_DIM = "#DBEAFE"
POSITIVE  = "#16A34A"
NEGATIVE  = "#DC2626"
WARNING   = "#F59E0B"
SHADOW    = "0 1px 2px rgba(17,24,39,0.04), 0 1px 6px rgba(17,24,39,0.04)"


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
/* Notion-style rhythm — every section header gets the same generous
   breathing room, so the page reads in distinct beats instead of a
   continuous wall of content. */
h2, h3 {{ margin-top: 1.9rem !important; margin-bottom: 0.9rem !important; }}

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
    padding: 0.45rem 0.7rem 0.45rem 0.6rem;
    border-radius: 6px;
    font-size: 0.92rem;
    margin-bottom: 0.15rem;
    border-left: 3px solid transparent;
    transition: background-color 0.12s ease, border-left-color 0.12s ease;
}}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] label:hover {{
    background-color: rgba(37, 99, 235, 0.06);
}}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] label:has(input:checked) {{
    background-color: {ACCENT_DIM};
    border-left-color: {ACCENT};
}}
section[data-testid="stSidebar"] .stRadio [role="radiogroup"] label:has(input:checked) p {{
    font-weight: 600 !important;
}}

/* Buttons */
.stButton button, .stFormSubmitButton button, .stDownloadButton button {{
    background-color: {INK};
    color: #FFFFFF !important;
    border: 1px solid {INK};
    font-family: 'Inter', sans-serif;
    font-weight: 600;
    border-radius: 6px;
    transition: background-color 0.15s ease, border-color 0.15s ease, transform 0.1s ease;
}}
.stButton button *, .stFormSubmitButton button *, .stDownloadButton button * {{ color: #FFFFFF !important; }}
.stButton button:hover, .stFormSubmitButton button:hover, .stDownloadButton button:hover {{
    background-color: {ACCENT};
    border-color: {ACCENT};
}}
.stButton button:active, .stFormSubmitButton button:active, .stDownloadButton button:active {{
    transform: scale(0.98);
}}

/* Inputs */
.stTextInput input, .stNumberInput input, .stSelectbox [data-baseweb="select"] > div, .stDateInput input {{
    background-color: {CARD} !important;
    color: {INK} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 6px !important;
    font-variant-numeric: tabular-nums;
    transition: border-color 0.12s ease, box-shadow 0.12s ease;
}}
.stTextInput input:focus, .stNumberInput input:focus {{
    border-color: {ACCENT} !important;
    box-shadow: 0 0 0 1px {ACCENT} !important;
}}

/* Expanders (Needs Review page) — Streamlit's native header background
   doesn't match this app's light theme, and the broad `.stApp * {{ color:
   {INK} }}` rule above then renders dark text on that same dark background.
   Force both explicitly so the header stays readable regardless of
   Streamlit's own theme defaults. */
[data-testid="stExpander"] summary {{
    background-color: {SURFACE} !important;
    color: {INK} !important;
    border-radius: 8px;
}}
[data-testid="stExpander"] summary p {{
    color: {INK} !important;
    font-weight: 600;
}}
[data-testid="stExpander"] summary svg {{
    fill: {INK} !important;
}}

/* KPI cards */
.kpi-card {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-top: 3px solid transparent;
    border-radius: 12px;
    box-shadow: {SHADOW};
    padding: 1.2rem 1.4rem;
    margin-bottom: 0.6rem;
    transition: box-shadow 0.15s ease, transform 0.15s ease;
}}
.kpi-card:hover {{
    box-shadow: 0 2px 4px rgba(17,24,39,0.06), 0 6px 16px rgba(17,24,39,0.08);
    transform: translateY(-1px);
}}
.kpi-card.tone-positive {{ border-top-color: {POSITIVE}; }}
.kpi-card.tone-negative {{ border-top-color: {NEGATIVE}; }}
.kpi-card.tone-accent   {{ border-top-color: {ACCENT}; }}
.kpi-card.tone-warning  {{ border-top-color: {WARNING}; }}
.kpi-card .label {{
    font-family: 'Inter', sans-serif;
    font-size: 0.72rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: {MUTED} !important;
    margin-bottom: 0.45rem;
}}
.kpi-card .value {{
    font-size: 1.85rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: {INK} !important;
    font-variant-numeric: tabular-nums;
    word-break: break-word;
}}
.kpi-card.tone-positive .value {{ color: {POSITIVE} !important; }}
.kpi-card.tone-negative .value {{ color: {NEGATIVE} !important; }}
.kpi-card.tone-accent .value {{ color: {ACCENT} !important; }}
.kpi-card.tone-warning .value {{ color: {WARNING} !important; }}

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
    transition: width 0.4s ease;
}}
.cat-fill.over {{
    background-color: {NEGATIVE};
}}

/* Table */
.ledger-table-wrap {{
    overflow-x: auto;
    border-radius: 10px;
    border: 1px solid {BORDER};
    box-shadow: {SHADOW};
    background-color: {CARD};
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
    padding: 0.65rem 0.75rem;
    position: sticky;
    top: 0;
}}
.ledger-table td {{
    padding: 0.6rem 0.75rem;
    border-bottom: 1px solid {BORDER};
    color: {INK} !important;
    white-space: nowrap;
}}
.ledger-table tr:last-child td {{ border-bottom: none; }}
.ledger-table tr:nth-child(even) td {{ background-color: {SURFACE}; }}
.ledger-table tbody tr {{ transition: background-color 0.12s ease; }}
.ledger-table tbody tr:hover td {{ background-color: rgba(37, 99, 235, 0.05) !important; }}
.ledger-table td.num {{ text-align: right; }}
.ledger-table td.gain-pos {{ color: {POSITIVE} !important; font-weight: 600; }}
.ledger-table td.gain-neg {{ color: {NEGATIVE} !important; font-weight: 600; }}

/* Status pills — a colored dot + tinted background badge instead of plain
   colored text, so status reads at a glance the way it does in Linear/
   Notion rather than blending into the rest of the row. */
.status-pill {{
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.2rem 0.65rem;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 600;
    line-height: 1.5;
    white-space: nowrap;
}}
.status-pill::before {{
    content: '';
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background-color: currentColor;
    flex-shrink: 0;
}}
.status-pill-open          {{ color: {POSITIVE}; background-color: #DCFCE7; }}
.status-pill-closed        {{ color: {MUTED};    background-color: {SURFACE}; }}
.status-pill-error         {{ color: {NEGATIVE}; background-color: #FEE2E2; }}
.status-pill-skipped       {{ color: {WARNING};  background-color: #FEF3C7; }}
.status-pill-needs_review  {{ color: {ACCENT};   background-color: {ACCENT_DIM}; }}
.status-pill-pending_fill  {{ color: {ACCENT};   background-color: {ACCENT_DIM}; }}
.status-pill-pending_buy  {{ color: {ACCENT};   background-color: {ACCENT_DIM}; }}

/* Login form */
div[data-testid="stForm"] {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-top: 3px solid {ACCENT};
    border-radius: 12px;
    box-shadow: 0 4px 16px rgba(15,23,42,0.06);
    padding: 1.75rem;
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


def category_pnl_row(realized_pnl, unrealized_pnl=None):
    """Small Realized / Unrealized / Total P&L line rendered directly under
    a category's budget bar (category_bar). unrealized_pnl=None means the
    Kite snapshot couldn't be read — shown as '—' rather than a wrong 0."""
    r_color = POSITIVE if realized_pnl >= 0 else NEGATIVE
    if unrealized_pnl is None:
        return f"""
        <div class="cat-figures" style="margin: -0.5rem 0 1.1rem 0;">
            Realized: <b style="color:{r_color};">₹{realized_pnl:,.0f}</b> · Unrealized: <b>—</b>
        </div>
        """
    u_color = POSITIVE if unrealized_pnl >= 0 else NEGATIVE
    total = realized_pnl + unrealized_pnl
    t_color = POSITIVE if total >= 0 else NEGATIVE
    return f"""
    <div class="cat-figures" style="margin: -0.5rem 0 1.1rem 0;">
        Realized: <b style="color:{r_color};">₹{realized_pnl:,.0f}</b> ·
        Unrealized: <b style="color:{u_color};">₹{unrealized_pnl:,.0f}</b> ·
        Total: <b style="color:{t_color};">₹{total:,.0f}</b>
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


def friendly_status(status, notes):
    """Turn a raw trade status + its notes into something readable at a
    glance, instead of a bare 'ERROR' that needs a click into notes to
    understand. The underlying status word still drives the color (via
    status_class_col in render_table) — only the displayed text changes."""
    s = (status or '').upper()
    n = notes or ''
    nl = n.lower()
    if s == 'ERROR':
        if 'cancelled' in nl:
            return 'Cancelled — never filled'
        if 'rejected' in nl:
            return 'Rejected by broker'
        return f'Error — {n}' if n else 'Error'
    if s == 'SKIPPED':
        return f'Skipped — {n}' if n else 'Skipped'
    if s == 'NEEDS_REVIEW':
        return 'Needs Review'
    if s == 'PENDING_BUY':
        return 'Awaiting buy'
    if s == 'PENDING_FILL':
        # Order is live at the broker but has not filled — usually a LIMIT at
        # the recommended price while the market sits above it. Distinct from
        # Open: nothing has been bought and no capital is committed yet.
        return f'Order placed — awaiting fill ({n})' if n else 'Order placed — awaiting fill'
    return status


def render_table(df, money_cols=None, status_col=None, gain_col=None, status_class_col=None):
    """Return HTML for a styled table from a DataFrame. status_class_col
    (optional) lets the pill color be derived from a different, raw column
    than the one actually displayed in status_col — e.g. showing
    'Cancelled — never filled' as text while still coloring it via the
    underlying 'ERROR' status. That raw column is used for lookup only and
    is not rendered as its own visible column.

    The status column renders as a pill (colored dot + tinted background)
    rather than plain colored text — reads at a glance the way status does
    in Linear/Notion, instead of blending into the row."""
    money_cols = money_cols or []
    cols = [c for c in df.columns if c != status_class_col]
    header = "".join(f"<th>{c.replace('_', ' ').title()}</th>" for c in cols)
    rows_html = []
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            val = row[c]
            css_class = "num" if c in money_cols else ""
            is_status = bool(status_col) and c == status_col
            status_val = None
            if is_status:
                class_source = row[status_class_col] if status_class_col else val
                status_val = str(class_source).lower()
            blank = val is None or val == '' or pd.isna(val)
            if not blank and hasattr(val, 'strftime'):
                # Timestamp/datetime/date — every date column in this app is
                # a calendar date (buy/sell date), never a meaningful
                # intraday time, so always show just YYYY-MM-DD.
                val = val.strftime('%Y-%m-%d')
            if gain_col and c == gain_col and not blank:
                try:
                    css_class += " gain-pos" if float(val) >= 0 else " gain-neg"
                except (ValueError, TypeError):
                    pass
            if c in money_cols and not blank:
                try:
                    val = f"₹{float(val):,.2f}"
                except (ValueError, TypeError):
                    pass
            if is_status and not blank:
                content = f'<span class="status-pill status-pill-{status_val}">{val}</span>'
            else:
                content = "" if blank else val
            cells.append(f'<td class="{css_class}">{content}</td>')
        rows_html.append(f"<tr>{''.join(cells)}</tr>")
    return f"""
    <div class="ledger-table-wrap">
    <table class="ledger-table">
        <thead><tr>{header}</tr></thead>
        <tbody>{''.join(rows_html)}</tbody>
    </table>
    </div>
    """
