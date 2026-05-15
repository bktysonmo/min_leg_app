"""
components/ui.py — Shared Streamlit UI helpers.
Design direction: editorial intelligence — dark navy spine, gold accents,
IBM Plex Mono for data, Crimson Pro for prose. Dense but breathable.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from typing import Optional


# ── Palette (matches IBM Plex + Crimson editorial look) ───────────────────
NAVY   = "#0f1923"
GOLD   = "#c8a96e"
SLATE  = "#1e2d3d"
GHOST  = "#d4d8e4"
MUTED  = "#6b7590"
RED    = "#c95454"
GREEN  = "#4caf7d"
BLUE   = "#7ba7d8"
YELLOW = "#e8c84a"
PURPLE = "#9b7ed8"

PARTY_COLORS = {"R": RED, "D": BLUE, "I": YELLOW, "Unknown": MUTED}
VOTE_COLORS  = {
    "yes":     GREEN,
    "no":      RED,
    "present": YELLOW,
    "absent":  MUTED,
    "NV":      MUTED,
}


def inject_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;600&family=Crimson+Pro:ital,wght@0,300;0,400;0,600;1,300;1,400&display=swap');

    html, body, [class*="css"] { font-family: 'Crimson Pro', Georgia, serif; }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background: #0f1923 !important;
        border-right: 2px solid #c8a96e;
    }
    section[data-testid="stSidebar"] * { color: #d4d8e4 !important; }
    section[data-testid="stSidebar"] .stRadio label {
        font-family: 'IBM Plex Mono', monospace !important;
        font-size: 0.72rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }

    /* Main */
    .main .block-container { max-width: 1500px; padding-top: 1.5rem; }

    /* Page header */
    .page-header {
        display: flex; align-items: baseline; gap: 1.2rem;
        border-bottom: 2px solid #c8a96e;
        padding-bottom: 0.6rem; margin-bottom: 1.5rem;
    }
    .page-header h1 {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 1.35rem; font-weight: 600;
        color: #0f1923; margin: 0; letter-spacing: 0.04em;
    }
    .page-header p {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.68rem; color: #6b7590; margin: 0;
        letter-spacing: 0.08em; text-transform: uppercase;
    }

    /* Stat cards */
    .stat-row { display: flex; gap: 0.75rem; flex-wrap: wrap; margin-bottom: 1.2rem; }
    .stat-card {
        background: #0f1923; color: #d4d8e4;
        padding: 0.85rem 1.1rem; border-radius: 3px;
        min-width: 110px; flex: 1;
        border-left: 3px solid #c8a96e;
    }
    .stat-card .slabel {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.6rem; letter-spacing: 0.14em;
        text-transform: uppercase; color: #c8a96e; margin-bottom: 0.25rem;
    }
    .stat-card .svalue {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 1.5rem; font-weight: 600; color: #fff;
        line-height: 1.1;
    }
    .stat-card .sdelta {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.65rem; color: #6b7590; margin-top: 0.15rem;
    }

    /* Section label */
    .sec-label {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.62rem; letter-spacing: 0.14em;
        text-transform: uppercase; color: #c8a96e;
        border-bottom: 1px solid #e5e7eb;
        padding-bottom: 0.25rem; margin: 1.2rem 0 0.65rem;
    }

    /* Badges */
    .badge {
        display: inline-block; padding: 0.1rem 0.45rem;
        border-radius: 2px;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.64rem; font-weight: 600;
        letter-spacing: 0.04em; text-transform: uppercase;
    }
    .badge-R { background: #fee2e2; color: #991b1b; }
    .badge-D { background: #dbeafe; color: #1e3a8a; }
    .badge-I { background: #f3f4f6; color: #374151; }
    .badge-yes { background: #d1fae5; color: #065f46; }
    .badge-no  { background: #fee2e2; color: #991b1b; }
    .badge-present { background: #fef3c7; color: #92400e; }
    .badge-absent  { background: #f3f4f6; color: #6b7280; }

    /* Info grid */
    .info-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
        gap: 0.65rem; margin-bottom: 1rem;
    }
    .info-item .ikey {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.58rem; letter-spacing: 0.1em;
        text-transform: uppercase; color: #9ca3af;
    }
    .info-item .ival {
        font-size: 0.92rem; font-weight: 600; color: #111827;
    }

    /* Callout blocks */
    .callout {
        border-left: 3px solid #c8a96e;
        background: #faf9f6;
        padding: 0.75rem 1rem;
        margin: 0.6rem 0;
        font-size: 0.88rem;
    }
    .callout.warn { border-color: #e8c84a; background: #fffbeb; }
    .callout.info { border-color: #7ba7d8; background: #eff6ff; }
    .callout.danger { border-color: #c95454; background: #fef2f2; }

    /* Language fragment highlight */
    .frag-block {
        background: #1e2d3d; color: #d4d8e4;
        border-radius: 3px; padding: 0.85rem 1.1rem;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.74rem; line-height: 1.65;
        margin: 0.5rem 0;
        border-left: 3px solid #c8a96e;
        white-space: pre-wrap;
        word-break: break-word;
    }
    .frag-block .frag-meta {
        color: #6b7590; font-size: 0.6rem;
        letter-spacing: 0.08em; text-transform: uppercase;
        margin-bottom: 0.5rem;
    }
    .frag-highlight { background: #c8a96e33; color: #fff; padding: 0 2px; border-radius: 1px; }

    /* Vote bar */
    .vote-bar-outer {
        background: #e5e7eb; border-radius: 2px; height: 8px;
        overflow: hidden; display: flex; width: 100%;
    }
    .vb-yes { background: #4caf7d; height: 100%; }
    .vb-no  { background: #c95454; height: 100%; }
    .vb-pres { background: #e8c84a; height: 100%; }
    .vb-abs  { background: #9ca3af; height: 100%; }

    /* Search panel */
    .search-panel {
        background: #f8f7f4; border: 1px solid #e5e7eb;
        border-left: 3px solid #c8a96e;
        padding: 1rem 1.2rem 0.6rem; margin-bottom: 1rem;
        border-radius: 2px;
    }

    /* Tabs override */
    .stTabs [data-baseweb="tab"] {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.7rem; letter-spacing: 0.06em;
        text-transform: uppercase;
    }

    /* Expanders */
    .streamlit-expanderHeader {
        font-family: 'IBM Plex Mono', monospace !important;
        font-size: 0.75rem !important;
    }

    /* Dataframe */
    .dataframe { font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem; }

    /* Selectbox / input */
    .stSelectbox label, .stTextInput label, .stMultiSelect label {
        font-family: 'IBM Plex Mono', monospace !important;
        font-size: 0.65rem !important; letter-spacing: 0.08em;
        text-transform: uppercase; color: #6b7590 !important;
    }
    </style>
    """, unsafe_allow_html=True)


def page_header(title: str, subtitle: str = ""):
    st.markdown(f"""
    <div class="page-header">
        <h1>{title}</h1>
        {f'<p>{subtitle}</p>' if subtitle else ''}
    </div>
    """, unsafe_allow_html=True)


def stat_card(label: str, value, delta: str = ""):
    return f"""
    <div class="stat-card">
        <div class="slabel">{label}</div>
        <div class="svalue">{value}</div>
        {f'<div class="sdelta">{delta}</div>' if delta else ''}
    </div>"""


def stat_row(cards: list[tuple]):
    """cards = list of (label, value) or (label, value, delta)"""
    html = '<div class="stat-row">'
    for c in cards:
        label = c[0]; value = c[1]; delta = c[2] if len(c) > 2 else ""
        html += stat_card(label, value, delta)
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def sec_label(text: str):
    st.markdown(f'<div class="sec-label">{text}</div>', unsafe_allow_html=True)


def badge(text: str, kind: str = ""):
    cls = f"badge-{kind}" if kind else "badge"
    return f'<span class="badge {cls}">{text}</span>'


def party_badge(p: str) -> str:
    p = (p or "").upper()
    return badge(p, p if p in ("R","D","I") else "I")


def vote_badge(v: str) -> str:
    v = (v or "").lower()
    return badge(v, v if v in ("yes","no","present","absent") else "")


def vote_bar_html(yes: int, no: int, present: int = 0, absent: int = 0) -> str:
    total = max(yes + no + present + absent, 1)
    yp = yes / total * 100
    np = no / total * 100
    pp = present / total * 100
    ap = absent / total * 100
    return f"""
    <div class="vote-bar-outer">
        <div class="vb-yes"  style="width:{yp:.1f}%"></div>
        <div class="vb-no"   style="width:{np:.1f}%"></div>
        <div class="vb-pres" style="width:{pp:.1f}%"></div>
        <div class="vb-abs"  style="width:{ap:.1f}%"></div>
    </div>
    <span style="font-family:'IBM Plex Mono',monospace;font-size:0.68rem;color:#6b7590">
        {yes}Y · {no}N · {present}P · {absent}A
    </span>"""


def callout(text: str, kind: str = ""):
    cls = f"callout {kind}" if kind else "callout"
    st.markdown(f'<div class="{cls}">{text}</div>', unsafe_allow_html=True)


def info_grid(items: dict):
    html = '<div class="info-grid">'
    for k, v in items.items():
        html += f'<div class="info-item"><div class="ikey">{k}</div><div class="ival">{v or "—"}</div></div>'
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def fragment_block(text: str, meta: str = "", highlight: str = ""):
    safe_text = text
    if highlight:
        import html as htmllib
        safe_text = htmllib.escape(text).replace(
            htmllib.escape(highlight),
            f'<span class="frag-highlight">{htmllib.escape(highlight)}</span>'
        )
    st.markdown(f"""
    <div class="frag-block">
        {f'<div class="frag-meta">{meta}</div>' if meta else ''}
        {safe_text}
    </div>
    """, unsafe_allow_html=True)


# ── Plotly theme ──────────────────────────────────────────────────────────
PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="IBM Plex Mono, monospace", size=11, color="#374151"),
    margin=dict(l=40, r=20, t=40, b=40),
    colorway=[BLUE, RED, GREEN, GOLD, PURPLE, YELLOW, MUTED],
    legend=dict(
        bgcolor="rgba(255,255,255,0.9)",
        bordercolor="#e5e7eb",
        borderwidth=1,
        font=dict(family="IBM Plex Mono, monospace", size=10),
    ),
    xaxis=dict(gridcolor="#f3f4f6", linecolor="#e5e7eb"),
    yaxis=dict(gridcolor="#f3f4f6", linecolor="#e5e7eb"),
)


def apply_theme(fig: go.Figure) -> go.Figure:
    fig.update_layout(**PLOTLY_LAYOUT)
    return fig


def empty_state(msg: str = "No data available."):
    st.markdown(f"""
    <div style="text-align:center;padding:3rem 1rem;color:#9ca3af;
                font-family:'IBM Plex Mono',monospace;font-size:0.8rem">
        {msg}
    </div>
    """, unsafe_allow_html=True)