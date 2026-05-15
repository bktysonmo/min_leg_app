"""
Missouri Legislative Intelligence Platform  —  mo_leg_app.py  (v2 — Actionable)
Single-file Streamlit app.  Run:  streamlit run mo_leg_app.py

Connects to mo_votes.db (SQLite).
Set MO_VOTES_DB env var or place mo_votes.db in the same directory.

REDESIGN PHILOSOPHY (v2):
  The original app was built for data journalists — it surfaced WHAT happened.
  This version is built for legislators, lobbyists, advocates, and campaign staff
  who need to know WHAT TO DO NEXT.

  Every page answers a specific operational question:
    Command Center  → "What's happening RIGHT NOW and what needs my attention?"
    Bill Tracker    → "Will this bill pass? Who do I need to move?"
    Member Intel    → "How do I persuade this person? What's their record?"
    Coalition Builder → "Who are my potential allies and opponents?"
    Whip Count      → "Do I have the votes? Where am I short?"
    Opposition Research → "What's the full record on this member or bill?"
    Language Lineage → "Where did this bill come from? Is it a zombie?"
    Field Reports / PDF → "Build a leave-behind or briefing doc"
"""

import sqlite3, os, re, json, html as htmllib
from pathlib import Path
from datetime import datetime, date

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

import urllib.request

# ═══════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ═══════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="MO Leg Intel",
    page_icon="🏛",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════════════════════
# DESIGN TOKENS  — refined dark-government aesthetic
# ═══════════════════════════════════════════════════════════════════════════
NAVY    = "#0b1622"
NAVY2   = "#152235"
GOLD    = "#c8a450"
GOLD2   = "#e8c87a"
SLATE   = "#1c2e42"
GHOST   = "#cdd3de"
MUTED   = "#5a6580"
RED     = "#d05050"
RED2    = "#f87171"
GREEN   = "#3da86a"
GREEN2  = "#6ee7a0"
BLUE    = "#5a8fc0"
BLUE2   = "#93c5fd"
AMBER   = "#d97706"
AMBER2  = "#fcd34d"
PURPLE  = "#7c5cb8"

PARTY_COLORS = {"R": RED, "D": BLUE, "I": AMBER, "Unknown": MUTED}
VOTE_COLORS  = {"yes": GREEN, "no": RED, "present": AMBER, "absent": MUTED, "NV": MUTED}

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="'Roboto Mono', monospace", size=11, color="#cbd5e1"),
    margin=dict(l=40, r=20, t=36, b=36),
    colorway=[BLUE, RED, GREEN, GOLD, PURPLE, AMBER, MUTED],
    legend=dict(bgcolor="rgba(21,34,53,0.95)", bordercolor="#2d4057",
                borderwidth=1, font=dict(size=10)),
    xaxis=dict(gridcolor="#1c2e42", linecolor="#2d4057", zeroline=False, tickfont=dict(size=10)),
    yaxis=dict(gridcolor="#1c2e42", linecolor="#2d4057", zeroline=False, tickfont=dict(size=10)),
)

def _theme(fig):
    fig.update_layout(**PLOTLY_LAYOUT)
    return fig

# ── GLOBAL CSS ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@300;400;600;700&family=DM+Serif+Display:ital@0;1&family=Inter:wght@300;400;500;600&display=swap');

/* ── base ── */
html, body, [class*="css"] {
  font-family: 'Inter', sans-serif;
  background: #0b1622;
  color: #cbd5e1;
}
.main .block-container { max-width: 1680px; padding: 1rem 2rem 2rem; }
.stApp { background: #0b1622; }

/* ── sidebar ── */
section[data-testid="stSidebar"] {
  background: #060e18 !important;
  border-right: 1px solid #1c3050;
  width: 230px !important;
}
section[data-testid="stSidebar"] * { color: #94a3b8 !important; }
section[data-testid="stSidebar"] .stRadio label {
  font-family: 'Roboto Mono', monospace !important;
  font-size: .68rem !important;
  letter-spacing: .06em;
  text-transform: uppercase;
  padding: .18rem 0;
}
section[data-testid="stSidebar"] .stRadio [data-checked="true"] label {
  color: #c8a450 !important;
}

/* ── page header ── */
.ph {
  display: flex; align-items: baseline; gap: 1.2rem;
  border-bottom: 1px solid #1c3050;
  padding-bottom: .7rem; margin-bottom: 1.6rem;
}
.ph h1 {
  font-family: 'DM Serif Display', serif;
  font-size: 1.6rem; font-weight: 400;
  color: #e2e8f0; margin: 0; letter-spacing: .01em;
}
.ph .ph-sub {
  font-family: 'Roboto Mono', monospace;
  font-size: .6rem; color: #5a6580;
  letter-spacing: .12em; text-transform: uppercase;
}
.ph .ph-q {
  font-family: 'Inter', sans-serif;
  font-size: .82rem; color: #c8a450;
  font-style: italic; margin-left: auto;
}

/* ── section label ── */
.slbl {
  font-family: 'Roboto Mono', monospace;
  font-size: .58rem; letter-spacing: .16em; text-transform: uppercase;
  color: #c8a450; border-bottom: 1px solid #1c3050;
  padding-bottom: .2rem; margin: 1.1rem 0 .65rem;
}

/* ── stat cards ── */
.stat-row { display: flex; gap: .6rem; flex-wrap: wrap; margin-bottom: 1.2rem; }
.sc {
  background: #0f1e2e; border: 1px solid #1c3050; border-top: 2px solid #c8a450;
  padding: .75rem 1rem; border-radius: 4px; flex: 1; min-width: 100px;
}
.sc .sl {
  font-family: 'Roboto Mono', monospace; font-size: .55rem;
  letter-spacing: .14em; text-transform: uppercase; color: #5a6580; margin-bottom: .2rem;
}
.sc .sv {
  font-family: 'Roboto Mono', monospace; font-size: 1.5rem;
  font-weight: 700; color: #e2e8f0; line-height: 1;
}
.sc .sd { font-family: 'Roboto Mono', monospace; font-size: .6rem; color: #5a6580; margin-top: .12rem; }
.sc.alert { border-top-color: #d05050; }
.sc.good  { border-top-color: #3da86a; }
.sc.warn  { border-top-color: #d97706; }

/* ── action cards (vote path, whip count) ── */
.action-card {
  background: #0f1e2e; border: 1px solid #1c3050; border-radius: 5px;
  padding: 1rem 1.2rem; margin-bottom: .7rem;
}
.action-card .ac-title {
  font-family: 'DM Serif Display', serif; font-size: 1rem; color: #e2e8f0; margin-bottom: .3rem;
}
.action-card .ac-meta {
  font-family: 'Roboto Mono', monospace; font-size: .6rem; color: #5a6580; letter-spacing: .08em;
}
.action-card .ac-insight {
  font-size: .85rem; color: #94a3b8; margin-top: .5rem; line-height: 1.5;
}

/* ── callout blocks ── */
.callout {
  border-left: 3px solid #c8a450; background: #0f1e2e;
  padding: .65rem .95rem; margin: .5rem 0; font-size: .86rem;
  border-radius: 0 4px 4px 0; color: #cbd5e1;
}
.callout.warn    { border-color: #d97706; }
.callout.danger  { border-color: #d05050; }
.callout.success { border-color: #3da86a; }
.callout.info    { border-color: #5a8fc0; }
.callout strong  { color: #e2e8f0; }

/* ── risk/status badges ── */
.badge {
  display: inline-block; padding: .1rem .5rem; border-radius: 3px;
  font-family: 'Roboto Mono', monospace; font-size: .6rem;
  font-weight: 700; letter-spacing: .05em; text-transform: uppercase;
}
.b-R       { background: #2d1515; color: #f87171; }
.b-D       { background: #0f1f33; color: #93c5fd; }
.b-I       { background: #2d200a; color: #fcd34d; }
.b-yes     { background: #0f2a1a; color: #6ee7a0; }
.b-no      { background: #2d1515; color: #f87171; }
.b-present { background: #2d200a; color: #fcd34d; }
.b-absent  { background: #1a1a2e; color: #94a3b8; }
.b-pass    { background: #0f2a1a; color: #6ee7a0; }
.b-fail    { background: #2d1515; color: #f87171; }
.b-risk    { background: #2d200a; color: #fcd34d; }
.b-watch   { background: #1a1030; color: #c4b5fd; }

/* ── member chip grid ── */
.chip-grid { display: flex; flex-wrap: wrap; gap: .3rem; margin: .4rem 0; }
.chip {
  display: inline-block; padding: .1rem .45rem; border-radius: 3px;
  font-family: 'Roboto Mono', monospace; font-size: .63rem;
  border: 1px solid transparent; cursor: default;
}
.chip-R { background: #2d1515; color: #f87171; border-color: #4a1f1f; }
.chip-D { background: #0f1f33; color: #93c5fd; border-color: #1a3050; }
.chip-I { background: #2d200a; color: #fcd34d; border-color: #4a350a; }
.chip-G { background: #0f2a1a; color: #6ee7a0; border-color: #1a4028; }
.chip-N { background: #2d1515; color: #f87171; border-color: #4a1f1f; }

/* ── vote bar ── */
.vbar-outer {
  background: #1c2e42; border-radius: 3px; height: 10px;
  overflow: hidden; display: flex; width: 100%; margin: .4rem 0;
}
.vb-yes  { background: #3da86a; height: 100%; }
.vb-no   { background: #d05050; height: 100%; }
.vb-pres { background: #d97706; height: 100%; }
.vb-abs  { background: #2d4057; height: 100%; }

/* ── path-to-passage steps ── */
.path-step {
  display: flex; align-items: flex-start; gap: .8rem;
  padding: .6rem 0; border-bottom: 1px solid #1c3050;
}
.path-step:last-child { border-bottom: none; }
.ps-num {
  width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  font-family: 'Roboto Mono', monospace; font-size: .65rem;
  font-weight: 700; flex-shrink: 0; margin-top: .1rem;
}
.ps-done { background: #3da86a; color: #fff; }
.ps-next { background: #c8a450; color: #000; }
.ps-todo { background: #1c2e42; color: #5a6580; border: 1px solid #2d4057; }
.ps-body { flex: 1; }
.ps-label { font-size: .88rem; color: #e2e8f0; font-weight: 500; }
.ps-detail { font-family: 'Roboto Mono', monospace; font-size: .62rem; color: #5a6580; margin-top: .1rem; }

/* ── persuadability gauge ── */
.persuade-bar {
  height: 8px; border-radius: 4px;
  background: linear-gradient(90deg, #3da86a, #d97706, #d05050);
  margin: .25rem 0; position: relative;
}
.persuade-needle {
  position: absolute; top: -3px; width: 3px; height: 14px;
  background: #fff; border-radius: 2px; transform: translateX(-50%);
}

/* ── intel card (member/bill profile) ── */
.intel-header {
  background: #0f1e2e; border: 1px solid #1c3050; border-radius: 5px;
  padding: 1.1rem 1.4rem; margin-bottom: 1rem;
  border-left: 4px solid #c8a450;
}
.ih-name  { font-family: 'DM Serif Display', serif; font-size: 1.3rem; color: #e2e8f0; }
.ih-meta  { font-family: 'Roboto Mono', monospace; font-size: .62rem; color: #5a6580;
            letter-spacing: .08em; margin-top: .2rem; }
.ih-party-R { border-left-color: #d05050; }
.ih-party-D { border-left-color: #5a8fc0; }

/* ── fragment / text blocks ── */
.frag {
  background: #0f1e2e; border: 1px solid #1c3050; border-left: 3px solid #c8a450;
  border-radius: 0 4px 4px 0; padding: .75rem 1rem;
  font-family: 'Roboto Mono', monospace; font-size: .72rem;
  line-height: 1.7; white-space: pre-wrap; word-break: break-word;
  color: #94a3b8; margin: .4rem 0;
}
.frag .fm { color: #5a6580; font-size: .57rem; letter-spacing: .1em; text-transform: uppercase; margin-bottom: .35rem; }
.fhl { background: #c8a45033; color: #e8c87a; padding: 0 2px; border-radius: 1px; }

/* ── whip count table ── */
.whip-yes  { color: #6ee7a0 !important; font-weight: 600; }
.whip-no   { color: #f87171 !important; font-weight: 600; }
.whip-lean-yes { color: #86efac !important; }
.whip-lean-no  { color: #fca5a5 !important; }
.whip-undecided { color: #fcd34d !important; }

/* ── data tables (st.dataframe overrides) ── */
[data-testid="stDataFrame"] { border-radius: 4px; }

/* ── tabs ── */
.stTabs [data-baseweb="tab"] {
  font-family: 'Roboto Mono', monospace !important;
  font-size: .65rem !important; letter-spacing: .07em !important;
  text-transform: uppercase !important;
  color: #5a6580 !important;
}
.stTabs [aria-selected="true"] { color: #c8a450 !important; }

/* ── selectbox / input labels ── */
.stSelectbox label, .stTextInput label, .stMultiSelect label, .stSlider label, .stCheckbox label {
  font-family: 'Roboto Mono', monospace !important;
  font-size: .6rem !important; letter-spacing: .1em !important;
  text-transform: uppercase !important; color: #5a6580 !important;
}
.stSelectbox > div > div, .stTextInput > div > div {
  background: #0f1e2e !important; border-color: #1c3050 !important; color: #cbd5e1 !important;
}
.stButton > button {
  font-family: 'Roboto Mono', monospace; font-size: .68rem;
  letter-spacing: .04em; background: #152235; border: 1px solid #2d4057;
  color: #94a3b8; border-radius: 3px;
}
.stButton > button:hover { background: #1c2e42; border-color: #c8a450; color: #e2e8f0; }
.stButton [data-testid="baseButton-primary"] {
  background: #c8a450 !important; color: #000 !important; border-color: #c8a450 !important;
}

.streamlit-expanderHeader {
  font-family: 'Roboto Mono', monospace !important; font-size: .7rem !important;
  color: #94a3b8 !important; background: #0f1e2e !important;
}
div[data-testid="stMetricValue"] { font-family: 'Roboto Mono', monospace !important; }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════

DB_URL = "https://github.com/bktysonmo/min_leg_app/releases/download/db/mo_votes.db"

def _resolve_db():
    db_path = Path("/tmp/mo_votes.db")

    # Reuse existing downloaded DB
    if db_path.exists():
        return str(db_path)

    # Download DB from GitHub Releases
    try:
        print("Downloading database...")
        urllib.request.urlretrieve(DB_URL, db_path)
    except Exception as e:
        st.error(f"Failed to download database: {e}")
        st.stop()

    return str(db_path)

DB_PATH = _resolve_db()

@st.cache_resource
def _conn():
    if not Path(DB_PATH).exists():
        st.error(f"Database not found: {DB_PATH}")
        st.stop()

    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn

def dbq(sql, params=()):
    try:
        return pd.read_sql_query(sql, _conn(), params=params)
    except Exception as e:
        st.warning(f"Query error: {e}")
        return pd.DataFrame()

def scalar(sql, params=()):
    try:
        cur = _conn().execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None

def tbl_exists(name):
    return scalar("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)) is not None

def tbl_names():
    df = dbq("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return df["name"].tolist() if not df.empty else []

def row_count(t):
    n = scalar(f'SELECT COUNT(*) FROM "{t}"')
    return int(n) if n is not None else 0

def col_names(t):
    df = dbq(f'PRAGMA table_info("{t}")')
    return df["name"].tolist() if not df.empty else []

_STRIP_SUB = re.compile(r"^(?:(?:HCS|SCS|SS|CCS|HS)(?:\s*#\s*\d+)?\s+)+", re.IGNORECASE)
def norm_bill(bn):
    if not bn: return ""
    s = _STRIP_SUB.sub("", str(bn).strip()).upper().strip()
    s = re.sub(r"^([A-Z]+)(\d+)$", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()

# ── safe string coercion helper ─────────────────────────────────────────────
def _s(val, default=""):
    """Coerce val to str, treating None/NaN as default."""
    if val is None:
        return default
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        pass
    return str(val)

# ═══════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def page_header(title, question="", subtitle=""):
    q_html = f'<span class="ph-q">"{question}"</span>' if question else ""
    sub_html = f'<span class="ph-sub">{subtitle}</span>' if subtitle else ""
    st.markdown(
        f'<div class="ph"><h1>{title}</h1>{sub_html}{q_html}</div>',
        unsafe_allow_html=True,
    )

def stat_row(cards):
    html = '<div class="stat-row">'
    for c in cards:
        lbl, val = c[0], c[1]
        delta = c[2] if len(c) > 2 else ""
        klass = c[3] if len(c) > 3 else ""
        html += (f'<div class="sc {klass}"><div class="sl">{lbl}</div>'
                 f'<div class="sv">{val}</div>'
                 + (f'<div class="sd">{delta}</div>' if delta else "") + "</div>")
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

def slbl(text):
    st.markdown(f'<div class="slbl">{text}</div>', unsafe_allow_html=True)

def badge(text, kind=""):
    return f'<span class="badge b-{kind}">{text}</span>'

def pbadge(p):
    p = (_s(p)).upper()
    return badge(p, p if p in ("R","D","I") else "I")

def vbadge(v):
    v = (_s(v)).lower()
    mapped = {"yea":"yes","aye":"yes","nay":"no"}
    v2 = mapped.get(v, v)
    return badge(v2.upper(), v2 if v2 in ("yes","no","present","absent") else "")

def callout(text, kind=""):
    st.markdown(f'<div class="callout {kind}">{text}</div>', unsafe_allow_html=True)

def vbar(yes, no, present=0, absent=0):
    total = max(yes + no + present + absent, 1)
    return (f'<div class="vbar-outer">'
            f'<div class="vb-yes"  style="width:{yes/total*100:.1f}%"></div>'
            f'<div class="vb-no"   style="width:{no/total*100:.1f}%"></div>'
            f'<div class="vb-pres" style="width:{present/total*100:.1f}%"></div>'
            f'<div class="vb-abs"  style="width:{absent/total*100:.1f}%"></div>'
            f'</div>'
            f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.65rem;color:#5a6580">'
            f'✓{yes} · ✗{no} · ○{present} · –{absent}</span>')

def chip(name, party):
    cls = {"R":"chip-R","D":"chip-D","I":"chip-I"}.get((_s(party)).upper(), "chip-G")
    return f'<span class="chip {cls}">{name}</span>'

def frag_block(text, meta="", highlight=""):
    safe = htmllib.escape(_s(text))
    if highlight:
        safe = safe.replace(htmllib.escape(highlight),
                            f'<span class="fhl">{htmllib.escape(highlight)}</span>')
    st.markdown(
        f'<div class="frag">' + (f'<div class="fm">{meta}</div>' if meta else "") + safe + "</div>",
        unsafe_allow_html=True)

def empty_msg(msg="No data available."):
    st.markdown(
        f'<div style="text-align:center;padding:2rem;color:#5a6580;'
        f'font-family:\'Roboto Mono\',monospace;font-size:.75rem">{msg}</div>',
        unsafe_allow_html=True)

def nav_bill(bill_pk, bill_label=""):
    st.session_state["nav_bill_pk"]    = bill_pk
    st.session_state["nav_bill_label"] = bill_label
    st.session_state["_page"] = "Bill Tracker"
    st.rerun()

def nav_member(member_id):
    st.session_state["nav_member_id"] = member_id
    st.session_state["_page"] = "Member Intel"
    st.rerun()

# ═══════════════════════════════════════════════════════════════════════════
# CACHED QUERIES
# ═══════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def get_sessions():
    return dbq("SELECT session_id, label, year FROM sessions ORDER BY year DESC, session_code")

@st.cache_data(ttl=300)
def get_members(active_only=True):
    w = "WHERE active=1" if active_only else ""
    return dbq(f"""
        SELECT member_id, full_name, chamber, party, district, county_list, email, phone, active
        FROM members {w}
        ORDER BY chamber, full_name
    """)

@st.cache_data(ttl=300)
def get_all_bills(session_id=None):
    w = "WHERE b.session_id=?" if session_id else ""
    p = (session_id,) if session_id else ()
    return dbq(f"""
        SELECT b.bill_pk, b.bill_label, b.bill_type, b.bill_number,
               b.chamber, b.session_id, b.title, b.short_desc,
               b.current_status, b.introduced_date, b.last_action_date,
               bm.action_count, bm.floor_vote_count,
               bm.sponsor_count, bm.rsmo_citation_count, bm.version_count
        FROM bills b
        LEFT JOIN bill_metrics bm ON bm.bill_pk = b.bill_pk
        {w}
        ORDER BY b.session_id DESC, b.bill_label
    """, p)

@st.cache_data(ttl=120)
def recent_activity_q(n=20):
    return dbq(f"""
        SELECT ba.action_date, ba.chamber, ba.action_text,
               b.bill_label, b.bill_pk, b.title, b.current_status
        FROM bill_actions ba
        JOIN bills b ON b.bill_pk = ba.bill_pk
        ORDER BY ba.action_date DESC, ba.action_id DESC
        LIMIT {n}
    """)

@st.cache_data(ttl=120)
def upcoming_floor_q():
    return dbq("""
        SELECT b.bill_label, b.bill_pk, b.title, b.chamber, b.current_status,
               b.last_action_date,
               bm.floor_vote_count, bm.sponsor_count
        FROM bills b
        LEFT JOIN bill_metrics bm ON bm.bill_pk = b.bill_pk
        WHERE LOWER(b.current_status) LIKE '%calendar%'
           OR LOWER(b.current_status) LIKE '%third reading%'
           OR LOWER(b.current_status) LIKE '%perfected%'
           OR LOWER(b.current_status) LIKE '%reported%'
        ORDER BY b.last_action_date DESC
        LIMIT 30
    """)

@st.cache_data(ttl=300)
def member_floor_votes_q(member_id):
    return dbq("""
        SELECT rc.roll_call_id, rc.vote_date, b.bill_label, b.bill_pk,
               b.title, b.short_desc, rc.motion_text, rc.reading_stage,
               rc.yes_count, rc.no_count, rc.present_count, rc.absent_count,
               rc.passed, mv.vote_cast
        FROM member_votes mv
        JOIN roll_calls rc ON rc.roll_call_id = mv.roll_call_id
        LEFT JOIN bills b ON b.bill_pk = rc.bill_pk
        WHERE mv.member_id=? AND rc.vote_context='floor'
        ORDER BY rc.vote_date DESC
    """, (member_id,))

@st.cache_data(ttl=300)
def member_sponsored_q(member_id):
    return dbq("""
        SELECT b.bill_pk, b.bill_label, b.bill_type, b.chamber, b.session_id,
               b.title, b.current_status, b.introduced_date, b.last_action_date,
               bs.sponsor_type, bm.floor_vote_count, bm.action_count
        FROM bill_sponsors bs
        JOIN bills b ON b.bill_pk = bs.bill_pk
        LEFT JOIN bill_metrics bm ON bm.bill_pk = b.bill_pk
        WHERE bs.member_id=?
        ORDER BY bs.sponsor_type, b.session_id DESC, b.bill_label
    """, (member_id,))

@st.cache_data(ttl=300)
def member_cross_aisle_q(member_id):
    if not tbl_exists("cross_aisle_votes"): return pd.DataFrame()
    return dbq("""
        SELECT ca.roll_call_id, rc.vote_date, b.bill_label, b.title,
               ca.vote_cast, ca.party_majority_vote
        FROM cross_aisle_votes ca
        JOIN roll_calls rc ON rc.roll_call_id = ca.roll_call_id
        LEFT JOIN bills b ON b.bill_pk = rc.bill_pk
        WHERE ca.member_id=?
        ORDER BY rc.vote_date DESC
    """, (member_id,))

@st.cache_data(ttl=300)
def member_agreement_peers_q(member_id, limit=30):
    if not tbl_exists("member_agreement"): return pd.DataFrame()
    return dbq("""
        SELECT CASE WHEN ma.member_a=? THEN ma.member_b ELSE ma.member_a END AS peer_id,
               m.full_name AS peer_name, m.party AS peer_party, m.chamber AS peer_chamber,
               ma.shared_votes, ma.agree_votes, ma.agreement_score
        FROM member_agreement ma
        JOIN members m ON m.member_id = CASE WHEN ma.member_a=? THEN ma.member_b ELSE ma.member_a END
        WHERE ma.member_a=? OR ma.member_b=?
        ORDER BY ma.agreement_score DESC LIMIT ?
    """, (member_id, member_id, member_id, member_id, limit))

@st.cache_data(ttl=300)
def bill_detail_q(bill_pk):
    df = dbq("SELECT * FROM bills WHERE bill_pk=?", (bill_pk,))
    return df.iloc[0].to_dict() if not df.empty else {}

@st.cache_data(ttl=300)
def bill_floor_votes_q(bill_pk):
    return dbq("""
        SELECT rc.roll_call_id, rc.vote_date, rc.reading_stage,
               rc.motion_text, rc.yes_count, rc.no_count,
               rc.present_count, rc.absent_count, rc.passed, rc.journal_page
        FROM roll_calls rc
        WHERE rc.bill_pk=? AND rc.vote_context='floor'
        ORDER BY rc.vote_date DESC
    """, (bill_pk,))

@st.cache_data(ttl=300)
def bill_committee_votes_q(bill_pk):
    return dbq("""
        SELECT rc.roll_call_id, rc.vote_date,
               COALESCE(c.name, rc.committee_name_raw) AS committee_name,
               rc.motion_text, rc.yes_count, rc.no_count, rc.passed
        FROM roll_calls rc
        LEFT JOIN committees c ON c.committee_id = rc.committee_id
        WHERE rc.bill_pk=? AND rc.vote_context='committee'
        ORDER BY rc.vote_date DESC
    """, (bill_pk,))

@st.cache_data(ttl=300)
def roll_call_member_votes_q(roll_call_id):
    return dbq("""
        SELECT m.member_id, m.full_name, m.party, m.chamber, m.district, mv.vote_cast
        FROM member_votes mv
        JOIN members m ON m.member_id = mv.member_id
        WHERE mv.roll_call_id=?
        ORDER BY m.party, mv.vote_cast, m.full_name
    """, (roll_call_id,))

@st.cache_data(ttl=300)
def bill_actions_q(bill_pk):
    return dbq("""
        SELECT action_date, chamber, action_text, vote_type, journal_page
        FROM bill_actions WHERE bill_pk=?
        ORDER BY action_date ASC, action_id ASC
    """, (bill_pk,))

@st.cache_data(ttl=300)
def bill_sponsors_q(bill_pk):
    return dbq("""
        SELECT m.member_id, m.full_name, m.party, m.chamber, m.district,
               bs.sponsor_type, bs.added_date
        FROM bill_sponsors bs
        JOIN members m ON m.member_id = bs.member_id
        WHERE bs.bill_pk=?
        ORDER BY bs.sponsor_type, m.full_name
    """, (bill_pk,))

@st.cache_data(ttl=300)
def bill_versions_q(bill_pk):
    return dbq("""
        SELECT version_id, version_label, version_date, stage, word_count, url, content_hash
        FROM bill_versions WHERE bill_pk=?
        ORDER BY version_date DESC
    """, (bill_pk,))

@st.cache_data(ttl=300)
def bill_fragments_q(bill_pk):
    if not tbl_exists("bill_language_fragments"): return pd.DataFrame()
    return dbq("""
        SELECT blf.fragment_id, blf.fragment_index, blf.fragment_type,
               blf.char_length, blf.fragment_text, blf.content_hash, bv.version_label
        FROM bill_language_fragments blf
        LEFT JOIN bill_versions bv ON bv.version_id = blf.version_id
        WHERE blf.bill_pk=?
        ORDER BY blf.fragment_index
    """, (bill_pk,))

@st.cache_data(ttl=300)
def language_lineage_q(bill_pk):
    if not tbl_exists("language_lineage"): return pd.DataFrame()
    return dbq("""
        SELECT ll.lang_lineage_id,
               CASE WHEN ll.source_bill_pk=? THEN 'ancestor-of' ELSE 'descended-from' END AS direction,
               CASE WHEN ll.source_bill_pk=? THEN b_tgt.bill_label ELSE b_src.bill_label END AS related_bill,
               CASE WHEN ll.source_bill_pk=? THEN b_tgt.bill_pk   ELSE b_src.bill_pk   END AS related_bill_pk,
               CASE WHEN ll.source_bill_pk=? THEN b_tgt.title     ELSE b_src.title     END AS related_title,
               CASE WHEN ll.source_bill_pk=? THEN ll.target_session_id ELSE ll.source_session_id END AS related_session,
               ll.match_type, ll.granularity, ll.similarity_score, ll.containment_score,
               ll.method, ll.confirmed, ll.note
        FROM language_lineage ll
        JOIN bills b_src ON b_src.bill_pk = ll.source_bill_pk
        JOIN bills b_tgt ON b_tgt.bill_pk = ll.target_bill_pk
        WHERE ll.source_bill_pk=? OR ll.target_bill_pk=?
        ORDER BY ll.similarity_score DESC
    """, (bill_pk,)*7)

@st.cache_data(ttl=300)
def cross_aisle_leaders_q(limit=20):
    if not tbl_exists("cross_aisle_votes"): return pd.DataFrame()
    return dbq("""
        SELECT m.member_id, m.full_name, m.party, m.chamber,
               COUNT(*) AS cross_aisle_count, mm.votes_cast,
               ROUND(100.0*COUNT(*)/NULLIF(mm.votes_cast,0),1) AS cross_pct
        FROM cross_aisle_votes ca
        JOIN members m ON m.member_id = ca.member_id
        LEFT JOIN member_metrics mm ON mm.member_id = m.member_id
        GROUP BY m.member_id ORDER BY cross_aisle_count DESC LIMIT ?
    """, (limit,))

@st.cache_data(ttl=300)
def all_committees_q(session_id=None):
    w = "WHERE session_id=?" if session_id else ""
    p = (session_id,) if session_id else ()
    return dbq(f"""
        SELECT committee_id, name, chamber, committee_type, session_id
        FROM committees {w}
        ORDER BY chamber, name
    """, p)

@st.cache_data(ttl=300)
def committee_members_q(committee_id):
    return dbq("""
        SELECT m.member_id, m.full_name, m.party, m.chamber, ca.role
        FROM committee_assignments ca
        JOIN members m ON m.member_id = ca.member_id
        WHERE ca.committee_id=?
        ORDER BY ca.role, m.full_name
    """, (committee_id,))

@st.cache_data(ttl=300)
def member_metrics_q():
    if not tbl_exists("member_metrics"): return pd.DataFrame()
    return dbq("""
        SELECT mm.*, m.full_name, m.party, m.chamber
        FROM member_metrics mm
        JOIN members m ON m.member_id = mm.member_id
    """)

@st.cache_data(ttl=300)
def party_vote_breakdown_q(roll_call_id):
    return dbq("""
        SELECT m.party, mv.vote_cast, COUNT(*) AS n
        FROM member_votes mv
        JOIN members m ON m.member_id = mv.member_id
        WHERE mv.roll_call_id=?
        GROUP BY m.party, mv.vote_cast
    """, (roll_call_id,))

@st.cache_data(ttl=300)
def global_lineage_q(limit=500):
    if not tbl_exists("language_lineage"): return pd.DataFrame()
    return dbq("""
        SELECT ll.lang_lineage_id, ll.source_bill_pk, ll.target_bill_pk,
               ll.match_type, ll.granularity, ll.similarity_score,
               ll.containment_score, ll.method, ll.detected_at,
               b_src.bill_label AS source_bill, b_src.session_id AS source_session,
               b_src.title AS source_title,
               b_tgt.bill_label AS target_bill, b_tgt.session_id AS target_session,
               b_tgt.title AS target_title,
               m_src.full_name AS source_sponsor, m_src.party AS source_party,
               m_tgt.full_name AS target_sponsor, m_tgt.party AS target_party
        FROM language_lineage ll
        JOIN bills b_src ON b_src.bill_pk = ll.source_bill_pk
        JOIN bills b_tgt ON b_tgt.bill_pk = ll.target_bill_pk
        LEFT JOIN members m_src ON m_src.member_id = ll.source_member_id
        LEFT JOIN members m_tgt ON m_tgt.member_id = ll.target_member_id
        ORDER BY ll.similarity_score DESC LIMIT ?
    """, (limit,))

# ═══════════════════════════════════════════════════════════════════════════
# ── DERIVED / INTELLIGENCE HELPERS ──────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════

def _passage_stages(detail, actions_df, floor_df, comm_df):
    """Return ordered list of (label, status, detail_text) for path-to-passage."""
    chamber = _s(detail.get("chamber","")).lower()
    status  = _s(detail.get("current_status","")).lower()
    def _done(kw): return any(k in status for k in kw)

    comm_done   = not comm_df.empty or _done(["reported","perfected","calendar","third reading","signed","enacted"])
    floor1_done = floor_df.shape[0] >= 1 or _done(["perfected","calendar","third reading","signed","enacted"])
    third_done  = floor_df.shape[0] >= 2 or _done(["truly agreed","enrolled","signed","enacted"])
    cross_done  = _done(["truly agreed","enrolled","signed","enacted"])
    gov_done    = _done(["signed","enacted","approved"])

    origin = chamber.title()
    cross  = "Senate" if origin.lower() == "house" else "House"

    steps = [
        ("Committee Hearing & Vote",   "done" if comm_done  else "next", f"{origin} committee" if not comm_done else "Passed committee"),
        (f"{origin} Floor — 1st/2nd Reading", "done" if floor1_done else "next", ""),
        (f"{origin} Floor — 3rd Reading (Passage)", "done" if third_done else "next", ""),
        (f"{cross} Committee & Floor",  "done" if cross_done else "next", "Cross-chamber concurrence required"),
        ("Governor Signature / Veto",   "done" if gov_done  else "next", ""),
    ]
    found_next = False
    result = []
    for lbl, st_, det in steps:
        if found_next:
            result.append((lbl, "todo", det))
        elif st_ == "done":
            result.append((lbl, "done", det))
        else:
            if not found_next:
                result.append((lbl, "next", det))
                found_next = True
    return result

def _persuadability_score(member_id, party, cross_df, floor_votes_df):
    """0–100, higher = more persuadable / bipartisan."""
    vc = len(floor_votes_df)
    if vc == 0: return 50
    ca = len(cross_df) if not cross_df.empty else 0
    score = min(100, int(ca / max(vc, 1) * 400))
    return score

def _vote_trend(floor_df):
    """Return 'trending_yes', 'trending_no', 'stable', or 'unknown'."""
    if floor_df.empty or "vote_cast" not in floor_df.columns: return "unknown"
    recent = floor_df.head(10)
    yes = (recent["vote_cast"] == "yes").sum()
    no  = (recent["vote_cast"] == "no").sum()
    if yes > no * 2: return "trending_yes"
    if no > yes * 2: return "trending_no"
    return "stable"

def _bill_risk_level(detail, floor_df, comm_df):
    """'high', 'medium', 'low' — from a supporter's perspective."""
    status = _s(detail.get("current_status","")).lower()
    if any(w in status for w in ("veto","defeated","failed","withdrawn")): return "failed"
    if any(w in status for w in ("signed","enacted","approved")): return "passed"
    if floor_df.empty and comm_df.empty: return "stalled"
    if not floor_df.empty:
        last = floor_df.iloc[0]
        yes, no = int(last.get("yes_count",0) or 0), int(last.get("no_count",0) or 0)
        margin = yes - no
        total  = yes + no
        if total > 0 and margin / total < 0.1: return "tight"
    return "active"

# ═══════════════════════════════════════════════════════════════════════════
# PAGE: COMMAND CENTER
# ═══════════════════════════════════════════════════════════════════════════

def page_command_center():
    page_header("Home")

    session = st.session_state.get("session_filter")

    total_bills   = scalar("SELECT COUNT(*) FROM bills" + (" WHERE session_id=?" if session else ""),
                           (session,) if session else ()) or 0
    active_bills  = scalar("""
        SELECT COUNT(*) FROM bills WHERE
          LOWER(current_status) NOT LIKE '%signed%' AND
          LOWER(current_status) NOT LIKE '%enacted%' AND
          LOWER(current_status) NOT LIKE '%defeated%' AND
          LOWER(current_status) NOT LIKE '%withdrawn%'
          """ + (" AND session_id=?" if session else ""),
          (session,) if session else ()) or 0
    floor_votes   = scalar("SELECT COUNT(*) FROM roll_calls WHERE vote_context='floor'"
                           + (" AND session_id=?" if session else ""),
                           (session,) if session else ()) or 0
    members       = scalar("SELECT COUNT(*) FROM members WHERE active=1") or 0

    on_calendar = upcoming_floor_q()
    n_calendar  = len(on_calendar)

    stat_row([
        ("Bills Tracked",    f"{total_bills:,}"),
        ("Active",           f"{active_bills:,}",  "", "good" if active_bills > 0 else ""),
        ("On Calendar / Floor-Ready", f"{n_calendar:,}", "needs attention", "warn" if n_calendar else ""),
        ("Floor Votes Cast", f"{floor_votes:,}"),
        ("Members",          f"{members:,}"),
    ])

    col1, col2 = st.columns([3, 2])

    with col1:
        slbl("Bills Near a Floor Vote")
        if on_calendar.empty:
            callout("No bills currently flagged as calendar-ready. Check bill status filters.", "info")
        else:
            for _, row in on_calendar.iterrows():
                bl  = _s(row.get("bill_label",""))
                bpk = row.get("bill_pk")
                ttl = _s(row.get("title",""))[:80]
                st_ = _s(row.get("current_status",""))
                ch  = _s(row.get("chamber",""))
                fvc = int(row.get("floor_vote_count",0) or 0)
                spc = int(row.get("sponsor_count",0) or 0)
                lad = _s(row.get("last_action_date",""))

                risk_icon = "🔴" if "third" in st_.lower() else "🟡"

                with st.container():
                    c1, c2 = st.columns([1, 7])
                    with c1:
                        if bl and bpk and st.button(bl, key=f"cc_bill_{bpk}", help="Open Bill Tracker"):
                            nav_bill(bpk, bl)
                    with c2:
                        st.markdown(
                            f'{risk_icon} <span style="font-size:.9rem;color:#e2e8f0;font-weight:500">{ttl}</span><br>'
                            f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.62rem;color:#5a6580">'
                            f'{ch.title()} · {st_[:50]} · Last: {lad} · {fvc} floor votes · {spc} sponsors'
                            f'</span>',
                            unsafe_allow_html=True)
                    st.markdown('<div style="border-top:1px solid #1c3050;margin:.2rem 0"></div>',
                                unsafe_allow_html=True)

    with col2:
        slbl("Recent Legislative Activity")
        activity = recent_activity_q(25)
        if activity.empty:
            empty_msg("No recent actions found.")
        else:
            for _, row in activity.iterrows():
                bl   = _s(row.get("bill_label",""))
                bpk  = row.get("bill_pk")
                date_ = _s(row.get("action_date",""))
                ch   = _s(row.get("chamber",""))
                text = _s(row.get("action_text",""))[:80]
                color = GOLD if "vote" in text.lower() or "passed" in text.lower() else "#1c2e42"
                st.markdown(
                    f'<div style="display:flex;gap:.6rem;padding:.3rem 0;'
                    f'border-bottom:1px solid #1c3050;align-items:flex-start">'
                    f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.58rem;'
                    f'color:#5a6580;min-width:82px;padding-top:.1rem">{date_}</span>'
                    f'<div style="flex:1">'
                    f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.66rem;'
                    f'color:{GOLD};margin-right:.4rem">{bl}</span>'
                    f'<span style="font-size:.8rem;color:#94a3b8">{text}</span>'
                    f'</div></div>',
                    unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    slbl("📊  Session Overview")
    c3, c4, c5 = st.columns(3)

    with c3:
        status_df = dbq(
            "SELECT current_status, COUNT(*) AS n FROM bills"
            + (" WHERE session_id=?" if session else "")
            + " GROUP BY current_status ORDER BY n DESC LIMIT 12",
            (session,) if session else ()
        )
        if not status_df.empty:
            top = status_df.sort_values("n").tail(10)
            fig = go.Figure(go.Bar(
                x=top["n"], y=top["current_status"], orientation="h",
                marker_color=GOLD, text=top["n"], textposition="outside",
                textfont=dict(size=9),
            ))
            fig.update_layout(title_text="Bills by Status", height=310, **PLOTLY_LAYOUT)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

    with c4:
        pv = dbq("""
            SELECT m.party, mv.vote_cast, COUNT(*) AS n
            FROM member_votes mv
            JOIN members m ON m.member_id = mv.member_id
            JOIN roll_calls rc ON rc.roll_call_id = mv.roll_call_id
            WHERE rc.vote_context='floor'
            GROUP BY m.party, mv.vote_cast
        """)
        if not pv.empty:
            fig2 = px.bar(pv[pv["vote_cast"].isin(["yes","no"])],
                          x="party", y="n", color="vote_cast",
                          color_discrete_map=VOTE_COLORS, barmode="group",
                          text_auto=True,
                          labels={"n":"Votes","party":"Party","vote_cast":"Vote"})
            fig2.update_layout(title_text="Party Vote Pattern", height=310, **PLOTLY_LAYOUT)
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar":False})

    with c5:
        ca = cross_aisle_leaders_q(10)
        if not ca.empty:
            fig3 = px.bar(ca.sort_values("cross_pct").tail(10),
                          x="cross_pct", y="full_name", orientation="h",
                          color="party", color_discrete_map=PARTY_COLORS,
                          text="cross_pct",
                          labels={"cross_pct":"% Cross-Aisle","full_name":""})
            fig3.update_traces(texttemplate="%{text:.0f}%", textfont=dict(size=9))
            fig3.update_layout(title_text="Most Bipartisan Members", height=310, **PLOTLY_LAYOUT)
            st.plotly_chart(fig3, use_container_width=True, config={"displayModeBar":False})
        else:
            empty_msg("Cross-aisle data pending.")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: BILL TRACKER
# ═══════════════════════════════════════════════════════════════════════════

def page_bill_tracker():
    page_header("Bill Tracker",
                "",
                "Path to passage · vote analysis · sponsor map")

    session  = st.session_state.get("session_filter")
    bills_df = get_all_bills(session)
    if bills_df.empty:
        callout("No bill data found.", "warn"); return

    st.markdown('<div style="background:#0f1e2e;border:1px solid #1c3050;border-radius:5px;padding:.9rem 1.1rem;margin-bottom:1rem">', unsafe_allow_html=True)
    r1, r2, r3, r4 = st.columns([2, 3, 1, 1])
    with r1:
        b_num = st.text_input("Bill number",
                              value=st.session_state.pop("nav_bill_label",""),
                              placeholder="SB 100, HB 45…", key="bt_num")
    with r2:
        b_kw  = st.text_input("Title / topic keywords", placeholder="education, tax credit…", key="bt_kw")
    with r3:
        b_ch  = st.selectbox("Chamber", ["All","house","senate"], key="bt_ch")
    with r4:
        status_opts = ["All"] + sorted(bills_df["current_status"].dropna().unique().tolist())
        b_status = st.selectbox("Status", status_opts, key="bt_status")
    st.markdown("</div>", unsafe_allow_html=True)

    df = bills_df.copy()
    if b_num.strip():
        df = df[df["bill_label"].str.upper().str.contains(b_num.strip().upper(), na=False)]
    if b_kw.strip():
        for t in b_kw.lower().split():
            df = df[df["title"].str.lower().str.contains(t, na=False) |
                    df["short_desc"].fillna("").str.lower().str.contains(t, na=False)]
    if b_ch != "All":     df = df[df["chamber"] == b_ch]
    if b_status != "All": df = df[df["current_status"] == b_status]

    if df.empty: empty_msg("No bills match those filters."); return

    def _lbl(r):
        t = _s(r.get("title",""))
        return f"{r['bill_label']}  —  {t[:65]}{'…' if len(t)>65 else ''}"

    options = ["— select a bill —"] + [_lbl(r) for _, r in df.iterrows()]
    pk_map  = {_lbl(r): r["bill_pk"] for _, r in df.iterrows()}

    nav_pk      = st.session_state.pop("nav_bill_pk", None)
    default_idx = 0
    if nav_pk:
        for i, (_, r) in enumerate(df.iterrows()):
            if r["bill_pk"] == nav_pk:
                default_idx = i + 1; break

    st.caption(f"{len(df)} bill(s) match")
    chosen = st.selectbox("Select bill to track", options, index=default_idx, key="bt_sel")

    if chosen == "— select a bill —":
        _bill_list_view(df); return

    _bill_detail_actionable(pk_map[chosen])


def _bill_list_view(df):
    slbl("Matching Bills")
    risk_map = {}
    for _, row in df.head(200).iterrows():
        st_ = _s(row.get("current_status","")).lower()
        if any(w in st_ for w in ("signed","enacted","approved")):    risk_map[row["bill_pk"]] = ("✓ Enacted",     GREEN, "good")
        elif any(w in st_ for w in ("defeated","failed","withdrawn")): risk_map[row["bill_pk"]] = ("✗ Failed",      RED,  "alert")
        elif any(w in st_ for w in ("third reading","calendar")):      risk_map[row["bill_pk"]] = ("⚡ Floor-ready", AMBER, "warn")
        else:                                                           risk_map[row["bill_pk"]] = ("● Active",      MUTED, "")

    for _, row in df.head(100).iterrows():
        bl  = _s(row.get("bill_label",""))
        bpk = row.get("bill_pk")
        ttl = _s(row.get("title",""))[:90]
        st_ = _s(row.get("current_status",""))
        ch  = _s(row.get("chamber",""))
        fvc = int(row.get("floor_vote_count",0) or 0)
        rlbl, rcol, _ = risk_map.get(bpk, ("●", MUTED, ""))
        c1, c2 = st.columns([1,9])
        with c1:
            if bl and bpk and st.button(bl, key=f"btl_{bpk}"):
                nav_bill(bpk, bl)
        with c2:
            st.markdown(
                f'<span style="color:{rcol};font-family:\'Roboto Mono\',monospace;font-size:.62rem">{rlbl}</span>'
                f' <span style="font-size:.88rem;color:#e2e8f0">{ttl}</span><br>'
                f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:#5a6580">'
                f'{ch.title()} · {st_[:55]} · {fvc} floor votes</span>',
                unsafe_allow_html=True)
        st.markdown('<div style="border-top:1px solid #1c3050;margin:.15rem 0"></div>', unsafe_allow_html=True)


def _bill_detail_actionable(bill_pk):
    detail      = bill_detail_q(bill_pk)
    floor_df    = bill_floor_votes_q(bill_pk)
    comm_df     = bill_committee_votes_q(bill_pk)
    actions_df  = bill_actions_q(bill_pk)
    spon_df     = bill_sponsors_q(bill_pk)
    versions_df = bill_versions_q(bill_pk)
    lin_df      = language_lineage_q(bill_pk)

    label   = _s(detail.get("bill_label",""))
    title   = _s(detail.get("title",""))
    status  = _s(detail.get("current_status",""))
    chamber = _s(detail.get("chamber",""))
    party   = ""
    if not spon_df.empty:
        primary_spon = spon_df[spon_df["sponsor_type"]=="primary"]
        if not primary_spon.empty:
            party = _s(primary_spon.iloc[0].get("party",""))

    risk    = _bill_risk_level(detail, floor_df, comm_df)
    risk_labels = {
        "passed": ("✓ Enacted",      GREEN, "BILL IS LAW"),
        "failed": ("✗ Failed/Vetoed", RED,  "LEGISLATION DIED"),
        "stalled":("◌ Stalled",      MUTED, "NO RECENT MOVEMENT"),
        "tight":  ("⚠ Tight Vote",   AMBER, "MARGIN <10%"),
        "active": ("● Active",        BLUE,  "MOVING"),
    }
    risk_lbl, risk_col, risk_tag = risk_labels.get(risk, ("?", MUTED, ""))

    ih_party = f"ih-party-{party}" if party in ("R","D") else ""
    st.markdown(
        f'<div class="intel-header {ih_party}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div>'
        f'<div class="ih-meta">{label} · {chamber.title()} · {_s(detail.get("session_id",""))} · '
        f'{_s(detail.get("bill_type",""))}</div>'
        f'<div class="ih-name">{title}</div>'
        f'<div class="ih-meta" style="margin-top:.25rem">{status}</div>'
        f'</div>'
        f'<div style="text-align:right;flex-shrink:0;margin-left:1rem">'
        f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:#5a6580">STATUS</div>'
        f'<div style="color:{risk_col};font-family:\'Roboto Mono\',monospace;'
        f'font-size:.75rem;font-weight:700">{risk_tag}</div>'
        f'<div style="color:{risk_col};font-size:1.1rem;margin-top:.1rem">{risk_lbl}</div>'
        f'</div></div>'
        f'</div>',
        unsafe_allow_html=True)

    if detail.get("short_desc"):
        callout(_s(detail["short_desc"]), "info")

    stat_row([
        ("Floor Votes",   len(floor_df)),
        ("Comm. Votes",   len(comm_df)),
        ("Actions",       len(actions_df)),
        ("Sponsors",      len(spon_df)),
        ("Versions",      len(versions_df)),
        ("Lineage Links", len(lin_df)),
    ])

    tabs = st.tabs([
        "🗺 Path to Passage",
        "🗳 Vote Analysis",
        "👥 Sponsor Map",
        "📋 Action History",
        "🔗 Lineage",
        "📄 Bill Text",
    ])

    with tabs[0]: _bt_path_to_passage(detail, actions_df, floor_df, comm_df, spon_df, bill_pk)
    with tabs[1]: _bt_vote_analysis(floor_df, comm_df, bill_pk)
    with tabs[2]: _bt_sponsor_map(spon_df, bill_pk)
    with tabs[3]: _bt_action_history(actions_df)
    with tabs[4]: _bt_lineage(lin_df)
    with tabs[5]: _bt_text(versions_df, bill_pk)


def _bt_path_to_passage(detail, actions_df, floor_df, comm_df, spon_df, bill_pk):
    slbl("Path to Passage")

    steps = _passage_stages(detail, actions_df, floor_df, comm_df)

    html = ""
    for i, (lbl, st_, det) in enumerate(steps):
        if st_ == "done":
            cls, icon, num_cls = "ps-done", "✓", "ps-done"
        elif st_ == "next":
            cls, icon, num_cls = "ps-next", str(i+1), "ps-next"
        else:
            cls, icon, num_cls = "ps-todo", str(i+1), "ps-todo"
        html += (f'<div class="path-step">'
                 f'<div class="ps-num {num_cls}">{icon}</div>'
                 f'<div class="ps-body">'
                 f'<div class="ps-label">{lbl}</div>'
                 + (f'<div class="ps-detail">{det}</div>' if det else "")
                 + f'</div></div>')
    st.markdown(html, unsafe_allow_html=True)

    next_step = next((lbl for lbl, st_, _ in steps if st_ == "next"), None)
    if next_step:
        callout(f"<strong>Next action required:</strong> {next_step}", "warn")

    if not floor_df.empty:
        last = floor_df.iloc[0]
        yes = int(last.get("yes_count",0) or 0)
        no  = int(last.get("no_count",0) or 0)
        total = yes + no
        if total > 0:
            margin = yes - no
            pct    = abs(margin) / total * 100
            if pct < 10:
                callout(f"<strong>⚠ Razor-thin margin</strong> — last vote was {yes}–{no} ({margin:+d}). "
                        f"Every vote matters.", "danger")
            elif margin < 0:
                callout(f"<strong>Bill failed</strong> last floor vote {yes}–{no}. "
                        f"Needs {abs(margin)+1} vote(s) switched to pass.", "danger")
            else:
                callout(f"<strong>Passed</strong> last floor vote {yes}–{no} (margin: {margin:+d}).", "success")

    if not spon_df.empty:
        n_primary = len(spon_df[spon_df["sponsor_type"]=="primary"])
        n_co      = len(spon_df[spon_df["sponsor_type"]=="cosponsor"])
        n_bipart  = len(spon_df[spon_df["party"].isin(["R","D"]) if "party" in spon_df.columns else []].drop_duplicates("party")) > 1 if "party" in spon_df.columns else False
        bip_str   = " Bipartisan co-sponsorship — strong signal." if n_bipart else ""
        callout(f"{n_primary} primary sponsor(s) · {n_co} co-sponsor(s).{bip_str}", "info")


def _bt_vote_analysis(floor_df, comm_df, bill_pk):
    slbl("Floor Vote Analysis")
    if floor_df.empty:
        empty_msg("No floor votes on record yet."); return

    for _, vrow in floor_df.iterrows():
        rc_id = vrow.get("roll_call_id")
        date_ = _s(vrow.get("vote_date",""))
        stage = _s(vrow.get("reading_stage",""))
        y = int(vrow.get("yes_count",0) or 0)
        n = int(vrow.get("no_count",0)  or 0)
        p = int(vrow.get("present_count",0) or 0)
        a = int(vrow.get("absent_count",0) or 0)
        passed = vrow.get("passed")
        result = "✓ PASSED" if passed else "✗ FAILED"

        with st.expander(f"{date_}  ·  {stage or 'Floor Vote'}  ·  {y}Y / {n}N  [{result}]"):
            st.markdown(vbar(y, n, p, a), unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

            mv = roll_call_member_votes_q(rc_id)
            pb = party_vote_breakdown_q(rc_id)

            if not pb.empty:
                c1, c2 = st.columns([2, 1])
                with c1:
                    fig = px.bar(pb[pb["vote_cast"].isin(["yes","no","present"])],
                                 x="party", y="n", color="vote_cast",
                                 color_discrete_map=VOTE_COLORS, barmode="group",
                                 text_auto=True, labels={"n":"Members"})
                    fig.update_layout(title_text="Party Breakdown", height=230, **PLOTLY_LAYOUT)
                    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
                with c2:
                    if not mv.empty:
                        r_votes = mv[mv["party"]=="R"]["vote_cast"].value_counts()
                        d_votes = mv[mv["party"]=="D"]["vote_cast"].value_counts()
                        r_maj   = r_votes.idxmax() if not r_votes.empty else "yes"
                        d_maj   = d_votes.idxmax() if not d_votes.empty else "yes"

                        swings = mv[
                            ((mv["party"]=="R") & (mv["vote_cast"] != r_maj)) |
                            ((mv["party"]=="D") & (mv["vote_cast"] != d_maj))
                        ]
                        if not swings.empty:
                            slbl("Cross-party votes")
                            chips_html = '<div class="chip-grid">'
                            for _, mr in swings.iterrows():
                                chips_html += chip(_s(mr["full_name"]), _s(mr["party"]))
                            chips_html += "</div>"
                            st.markdown(chips_html, unsafe_allow_html=True)

            if not mv.empty:
                slbl("All Member Votes")
                for vote_type, col_, icon in [("yes",GREEN,"✓"),("no",RED,"✗"),("present",AMBER,"○"),("absent",MUTED,"–")]:
                    sub = mv[mv["vote_cast"] == vote_type]
                    if sub.empty: continue
                    st.markdown(
                        f'<div style="margin:.4rem 0 .15rem;font-family:\'Roboto Mono\',monospace;'
                        f'font-size:.6rem;letter-spacing:.1em;text-transform:uppercase;color:{col_}">'
                        f'{icon} {vote_type.title()} ({len(sub)})</div>',
                        unsafe_allow_html=True)
                    chips_html = '<div class="chip-grid">'
                    for _, mr in sub.iterrows():
                        chips_html += chip(_s(mr["full_name"]), _s(mr["party"]))
                    chips_html += "</div>"
                    st.markdown(chips_html, unsafe_allow_html=True)

    if not comm_df.empty:
        slbl("Committee Votes")
        st.dataframe(comm_df[["vote_date","committee_name","motion_text","yes_count","no_count","passed"]],
                     use_container_width=True, hide_index=True)


def _bt_sponsor_map(spon_df, bill_pk):
    slbl("Sponsor Map")
    if spon_df.empty:
        empty_msg("No sponsors on record."); return

    primary = spon_df[spon_df["sponsor_type"]=="primary"]
    cospon  = spon_df[spon_df["sponsor_type"]=="cosponsor"]

    party_split = spon_df["party"].value_counts()
    n_R = int(party_split.get("R", 0))
    n_D = int(party_split.get("D", 0))
    bipartisan = n_R > 0 and n_D > 0

    if bipartisan:
        callout(f"<strong>Bipartisan bill</strong> — {n_R} Republican + {n_D} Democrat co-sponsors. "
                f"Stronger chance of passage.", "success")
    else:
        maj_party = "Republican" if n_R > n_D else "Democrat"
        callout(f"<strong>Single-party bill</strong> — primarily {maj_party}. "
                f"Will need cross-aisle support to survive a divided chamber.", "warn")

    if not primary.empty:
        slbl("Primary Sponsor(s)")
        for _, row in primary.iterrows():
            c1, c2 = st.columns([3, 1])
            with c1:
                p = _s(row.get("party",""))
                pc = {"R":"b-R","D":"b-D","I":"b-I"}.get(p,"")
                st.markdown(
                    f'{badge(p, pc)} <span style="font-size:.95rem;color:#e2e8f0;font-weight:500">'
                    f'{_s(row["full_name"])}</span> — District {_s(row.get("district","—"))} · '
                    f'{_s(row.get("chamber","")).title()}',
                    unsafe_allow_html=True)
            with c2:
                mid = row.get("member_id")
                if mid and st.button("View Intel →", key=f"smap_pri_{mid}_{bill_pk}"):
                    nav_member(mid)

    if not cospon.empty:
        slbl(f"Co-Sponsors ({len(cospon)})")
        c1, c2 = st.columns([1, 2])
        with c1:
            fig = px.pie(party_split.reset_index(), names="party", values="count",
                         color="party", color_discrete_map=PARTY_COLORS, hole=0.45,
                         title="Co-Sponsor Party Split")
            fig.update_traces(textfont=dict(size=10))
            fig.update_layout(height=220, **PLOTLY_LAYOUT)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
        with c2:
            chips_html = '<div class="chip-grid">'
            for _, row in cospon.iterrows():
                chips_html += chip(_s(row["full_name"]), _s(row.get("party","")))
            chips_html += "</div>"
            st.markdown(chips_html, unsafe_allow_html=True)


def _bt_action_history(actions_df):
    slbl("Action Timeline")
    if actions_df.empty: empty_msg("No actions."); return
    kw = st.text_input("Filter actions", key="bt_act_kw")
    disp = actions_df.copy()
    if kw.strip():
        disp = disp[disp["action_text"].str.lower().str.contains(kw.lower(), na=False)]
    for _, row in disp.iterrows():
        date_ = _s(row.get("action_date",""))
        ch    = _s(row.get("chamber",""))
        text  = _s(row.get("action_text",""))
        vt    = _s(row.get("vote_type",""))
        jpage = _s(row.get("journal_page",""))
        is_vote = bool(vt) or "vote" in text.lower()
        icon = "🗳" if is_vote else "📌"
        st.markdown(
            f'<div style="display:flex;gap:.7rem;padding:.3rem 0;border-bottom:1px solid #1c3050">'
            f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;'
            f'color:#5a6580;min-width:90px">{date_}</span>'
            f'<span style="font-size:.75rem;color:{GOLD};min-width:44px">{ch or "—"}</span>'
            f'<span style="font-size:.84rem;color:#94a3b8;flex:1">{icon} {text}'
            + (f' <span style="font-family:\'Roboto Mono\',monospace;font-size:.58rem;color:#5a6580">[{vt}]</span>' if vt else "")
            + '</span>'
            + (f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.58rem;color:#5a6580">p.{jpage}</span>' if jpage else "")
            + f'</div>',
            unsafe_allow_html=True)


def _bt_lineage(lin_df):
    slbl("Language Lineage — Where Did This Bill Come From?")
    if lin_df.empty:
        callout("No lineage data found. Run resurrection.py to detect language relationships.", "info"); return

    zombie_count = len(lin_df[lin_df["match_type"].isin(["zombie","reintroduced"])])
    if zombie_count > 0:
        callout(f"<strong> Revived Language Detected</strong> — {zombie_count} relationship(s) classified as 'zombie' or 'reintroduced'. "
                f"This bill has failed before. Know the history.", "danger")

    min_sim = st.slider("Min similarity", 0.0, 1.0, 0.3, 0.05, key="bt_lin_sim")
    disp = lin_df[lin_df["similarity_score"] >= min_sim]

    for _, row in disp.iterrows():
        direction  = _s(row.get("direction",""))
        rel_bill   = _s(row.get("related_bill",""))
        rel_bpk    = row.get("related_bill_pk")
        rel_title  = _s(row.get("related_title",""))[:80]
        rel_sess   = _s(row.get("related_session",""))
        match_type = _s(row.get("match_type",""))
        sim        = float(row.get("similarity_score",0) or 0)

        dir_color = GOLD if direction == "ancestor-of" else BLUE
        mt_icon   = {"reintroduced":"↻","revived langage detected":"!","amendment_adopted":"📎",
                     "partial_reuse":"✂","substitute":"↔"}.get(match_type,"~")

        c1, c2 = st.columns([1, 7])
        with c1:
            if rel_bill and rel_bpk and st.button(rel_bill, key=f"bt_lin_nav_{row.get('lang_lineage_id','')}"):
                nav_bill(rel_bpk, rel_bill)
        with c2:
            st.markdown(
                f'<div style="border-left:3px solid {dir_color};padding:.4rem .7rem;'
                f'background:#0f1e2e;border-radius:0 4px 4px 0;margin:.2rem 0">'
                f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.62rem;'
                f'color:{dir_color}">{mt_icon} {match_type} · {direction} · {sim:.0%} similar · {rel_sess}</div>'
                f'<div style="font-size:.86rem;color:#e2e8f0;margin-top:.15rem">{rel_title}</div>'
                f'</div>',
                unsafe_allow_html=True)


def _bt_text(versions_df, bill_pk):
    slbl("Bill Text")
    if versions_df.empty: empty_msg("No versions on file."); return
    labels = versions_df["version_label"].fillna("Unknown").tolist()
    ids    = versions_df["version_id"].tolist()
    chosen = st.selectbox("Version", labels, key="bt_ver")
    ver_id = ids[labels.index(chosen)]
    ver_row = versions_df.iloc[labels.index(chosen)]

    st.markdown(
        f'<div style="display:flex;gap:1.5rem;font-family:\'Roboto Mono\',monospace;'
        f'font-size:.65rem;color:#5a6580;margin-bottom:.5rem">'
        f'<span>Stage: {_s(ver_row.get("stage",""))}</span>'
        f'<span>Date: {_s(ver_row.get("version_date",""))}</span>'
        f'<span>Words: {int(ver_row.get("word_count",0) or 0):,}</span>'
        f'</div>', unsafe_allow_html=True)

    full_text = scalar("SELECT full_text FROM bill_versions WHERE version_id=?", (ver_id,)) or ""
    kw = st.text_input("Highlight keyword", key="bt_text_kw")
    if full_text:
        display = full_text[:30000]
        if kw.strip(): display = display.replace(kw, f"**{kw}**")
        st.text_area("", value=display, height=500, disabled=True, key=f"bt_ft_{ver_id}")
    else:
        callout("Full text not fetched for this version.", "warn")


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: MEMBER INTEL
# ═══════════════════════════════════════════════════════════════════════════

def page_member_intel():
    page_header("Member Intel",
                "Eventually this will include a 'by-issue' filter, allowing you to look at member-by-member data based only on their record in selected areas",
                "Voting profile · alignment · persuadability · opposition research")

    members_df = get_members()
    if members_df.empty:
        callout("No member data.", "warn"); return

    st.markdown('<div style="background:#0f1e2e;border:1px solid #1c3050;border-radius:5px;padding:.9rem 1.1rem;margin-bottom:1rem">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns([3,1,1])
    with c1: name_q = st.text_input("Name", placeholder="Smith, Jane…", key="mi_name")
    with c2: ch_f   = st.selectbox("Chamber", ["All","senate","house"], key="mi_ch")
    with c3: p_f    = st.selectbox("Party",   ["All","R","D","I"], key="mi_party")
    st.markdown("</div>", unsafe_allow_html=True)

    df = members_df.copy()
    if name_q.strip():
        toks = name_q.lower().split()
        df = df[df["full_name"].str.lower().apply(lambda n: all(t in n for t in toks))]
    if ch_f != "All": df = df[df["chamber"] == ch_f]
    if p_f  != "All": df = df[df["party"] == p_f]

    if df.empty: empty_msg("No members match."); return

    def _lbl(r):
        pre = "Sen." if r["chamber"] == "senate" else "Rep."
        return f"{pre} {r['full_name']}  ({_s(r.get('party',''))} · D{_s(r.get('district',''))})"

    options = ["— select —"] + [_lbl(r) for _, r in df.iterrows()]
    id_map  = {_lbl(r): r["member_id"] for _, r in df.iterrows()}

    nav_id      = st.session_state.pop("nav_member_id", None)
    default_idx = 0
    if nav_id:
        for i, (_, r) in enumerate(df.iterrows()):
            if r["member_id"] == nav_id:
                default_idx = i + 1; break

    st.caption(f"{len(df)} member(s) match")
    chosen = st.selectbox("Select member", options, index=default_idx, key="mi_sel")

    if chosen == "— select —":
        _member_roster_view(df); return

    _member_detail_actionable(id_map[chosen])


def _member_roster_view(df):
    slbl("Member Roster")
    c1, c2 = st.columns([1,2])
    with c1:
        pc = df["party"].value_counts().reset_index()
        pc.columns = ["party","n"]
        fig = px.pie(pc, names="party", values="n", color="party",
                     color_discrete_map=PARTY_COLORS, hole=0.45, title="Party Breakdown")
        fig.update_layout(height=230, **PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
    with c2:
        st.dataframe(
            df[["full_name","chamber","party","district","county_list","email"]],
            use_container_width=True, hide_index=True)


def _member_detail_actionable(member_id):
    row = dbq("SELECT * FROM members WHERE member_id=?", (member_id,))
    if row.empty: callout("Member not found.", "danger"); return
    detail = row.iloc[0].to_dict()

    floor_df  = member_floor_votes_q(member_id)
    spon_df   = member_sponsored_q(member_id)
    cross_df  = member_cross_aisle_q(member_id)
    peers_df  = member_agreement_peers_q(member_id, 30)

    mm_row = dbq("SELECT * FROM member_metrics WHERE member_id=?", (member_id,))
    mm = mm_row.iloc[0].to_dict() if not mm_row.empty and tbl_exists("member_metrics") else {}

    party   = _s(detail.get("party",""))
    vc      = int(mm.get("votes_cast",    len(floor_df)) or len(floor_df))
    yeas    = int(mm.get("yes_votes",     0) or 0)
    nays    = int(mm.get("no_votes",      0) or 0)
    ar      = float(mm.get("absence_rate",0) or 0)
    persuade= _persuadability_score(member_id, party, cross_df, floor_df)
    trend   = _vote_trend(floor_df)

    ih_party = f"ih-party-{party}" if party in ("R","D") else ""
    st.markdown(
        f'<div class="intel-header {ih_party}">'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
        f'<div>'
        f'<div class="ih-meta">{_s(detail.get("chamber","")).title()} · District {_s(detail.get("district","—"))} · {party}</div>'
        f'<div class="ih-name">{_s(detail.get("full_name",""))}</div>'
        f'<div class="ih-meta" style="margin-top:.2rem">'
        f'{_s(detail.get("phone",""))} · {_s(detail.get("email",""))}'
        f'</div></div>'
        f'<div style="text-align:right;flex-shrink:0;margin-left:1rem">'
        f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.55rem;color:#5a6580">PERSUADABILITY</div>'
        f'<div style="font-family:\'Roboto Mono\',monospace;font-size:1.4rem;font-weight:700;'
        f'color:{"#3da86a" if persuade > 60 else "#d97706" if persuade > 30 else "#d05050"}">'
        f'{persuade}/100</div>'
        f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.58rem;color:#5a6580">'
        f'{"HIGH — frequent cross-aisle" if persuade > 60 else "MEDIUM — occasional" if persuade > 30 else "LOW — party-line"}'
        f'</div></div></div></div>',
        unsafe_allow_html=True)

    stat_row([
        ("Floor Votes",     f"{vc:,}"),
        ("Yea Rate",        f"{yeas/max(vc,1)*100:.0f}%"),
        ("Nay Rate",        f"{nays/max(vc,1)*100:.0f}%"),
        ("Absence Rate",    f"{ar*100:.1f}%", "", "alert" if ar > 0.15 else ""),
        ("Cross-Aisle",     f"{len(cross_df):,}"),
        ("Bills Sponsored", f"{len(spon_df):,}"),
        ("Vote Trend",      {"trending_yes":"↑ More Yes","trending_no":"↓ More No","stable":"→ Stable","unknown":"?"}[trend]),
    ])

    ca_rate = len(cross_df) / max(vc,1) * 100
    bills_passed = len(spon_df[spon_df["current_status"].str.lower().str.contains("signed|enacted", na=False)]) if not spon_df.empty else 0
    top_peers = peers_df.head(3) if not peers_df.empty else pd.DataFrame()
    peer_names = ", ".join(top_peers["peer_name"].tolist()) if not top_peers.empty else "unknown"

    callout(
        f"<strong>Quick Read:</strong> Votes with their party {100-ca_rate:.0f}% of the time. "
        f"{'High absenteeism — may be difficult to pin down for a vote.' if ar > 0.15 else 'Reliable attendance.'} "
        f"Most aligned with: {peer_names}.",
        "info"
    )

    tabs = st.tabs([
        "🗳 Voting Record",
        "📜 Sponsored Bills",
        "🤝 Alignment & Peers",
        "🔁 Cross-Aisle History",
        "🔍 Opposition Research",
    ])

    with tabs[0]: _mi_voting_record(floor_df, member_id)
    with tabs[1]: _mi_sponsored(spon_df)
    with tabs[2]: _mi_alignment(peers_df, party, cross_df, floor_df)
    with tabs[3]: _mi_cross_aisle(cross_df, vc)
    with tabs[4]: _mi_oppo(detail, floor_df, spon_df, cross_df, mm)


def _mi_voting_record(floor_df, member_id):
    slbl("Floor Voting Record")
    if floor_df.empty: empty_msg("No floor votes."); return

    vc_counts = floor_df["vote_cast"].value_counts()
    yes = int(vc_counts.get("yes", 0))
    no  = int(vc_counts.get("no",  0))
    pr  = int(vc_counts.get("present", 0))
    ab  = int(vc_counts.get("absent",  0))

    c1, c2 = st.columns([1, 2])
    with c1:
        fig = go.Figure(go.Pie(
            labels=["Yes","No","Present","Absent"],
            values=[yes,no,pr,ab], hole=0.55,
            marker=dict(colors=[GREEN,RED,AMBER,MUTED],line=dict(color="#0b1622",width=2)),
            textinfo="label+percent",
            textfont=dict(family="Roboto Mono, monospace", size=10),
        ))
        fig.add_annotation(text=f"<b>{yes/max(yes+no+pr+ab,1)*100:.0f}%</b><br>yea",
                           x=0.5, y=0.5, showarrow=False,
                           font=dict(family="Roboto Mono, monospace", size=11, color="#e2e8f0"))
        fig.update_layout(title_text="Vote Breakdown", height=240, **PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
    with c2:
        tdf = floor_df.copy()
        tdf["vote_date"] = pd.to_datetime(tdf["vote_date"], errors="coerce")
        tdf = tdf.dropna(subset=["vote_date"])
        tdf["month"] = tdf["vote_date"].dt.to_period("M").astype(str)
        trend = tdf.groupby(["month","vote_cast"]).size().reset_index(name="n")
        if not trend.empty:
            fig2 = px.bar(trend, x="month", y="n", color="vote_cast",
                          color_discrete_map=VOTE_COLORS, barmode="stack",
                          labels={"n":"Votes","month":"","vote_cast":"Vote"})
            fig2.update_xaxes(tickangle=45)
            fig2.update_layout(title_text="Monthly Vote Trend", height=240, **PLOTLY_LAYOUT)
            st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar":False})

    slbl("Individual Votes")
    fc1, fc2, fc3 = st.columns([3,1,1])
    with fc1: kw   = st.text_input("Filter (bill, motion, description)", key="mi_vr_kw")
    with fc2:
        vopts = ["All"] + sorted(floor_df["vote_cast"].dropna().unique().tolist())
        vf = st.selectbox("Vote", vopts, key="mi_vr_vf")
    with fc3:
        sopts = ["All"] + sorted(floor_df["reading_stage"].dropna().unique().tolist())
        sf = st.selectbox("Stage", sopts, key="mi_vr_sf")

    disp = floor_df.copy()
    if kw.strip():
        for t in kw.lower().split():
            disp = disp[disp.apply(lambda r: t in " ".join(_s(v) for v in r.values).lower(), axis=1)]
    if vf != "All": disp = disp[disp["vote_cast"] == vf]
    if sf != "All": disp = disp[disp["reading_stage"] == sf]

    st.caption(f"{len(disp)} of {len(floor_df)} votes")
    for _, row in disp.head(200).iterrows():
        b   = _s(row.get("bill_label",""))
        bpk = row.get("bill_pk")
        t   = _s(row.get("title","") or row.get("short_desc",""))[:70]
        d   = _s(row.get("vote_date",""))
        vc2 = _s(row.get("vote_cast",""))
        y   = int(row.get("yes_count",0) or 0)
        n   = int(row.get("no_count",0) or 0)
        ps  = row.get("passed")
        c1, c2 = st.columns([1,8])
        with c1:
            if b and bpk and st.button(b, key=f"mi_vr_nav_{row.get('roll_call_id','')}"):
                nav_bill(bpk, b)
        with c2:
            st.markdown(
                f'{vbadge(vc2)} '
                f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.68rem;color:#94a3b8">'
                f'{d} · {t[:60]} · {y}Y/{n}N {"✓" if ps else "✗"}</span>',
                unsafe_allow_html=True)


def _mi_sponsored(spon_df):
    slbl("Sponsored Legislation")
    if spon_df.empty: empty_msg("No sponsored bills."); return

    primary = spon_df[spon_df["sponsor_type"]=="primary"]
    cospon  = spon_df[spon_df["sponsor_type"]=="cosponsor"]

    if not primary.empty:
        enacted = primary["current_status"].str.lower().str.contains("signed|enacted", na=False).sum()
        stat_row([
            ("Primary Bills", len(primary)),
            ("Co-Sponsor",    len(cospon)),
            ("Became Law",    int(enacted), f"of {len(primary)} primary bills",
             "good" if enacted > 0 else ""),
        ])

    kw = st.text_input("Filter bills", key="mi_spon_kw")
    disp = spon_df.copy()
    if kw.strip():
        for t in kw.lower().split():
            disp = disp[disp.apply(lambda r: t in " ".join(_s(v) for v in r.values).lower(), axis=1)]

    for _, row in disp.iterrows():
        bl  = _s(row.get("bill_label",""))
        bpk = row.get("bill_pk")
        t   = _s(row.get("title",""))[:80]
        st_ = _s(row.get("current_status",""))
        sl  = st_.lower()
        sc  = GREEN if any(w in sl for w in ("signed","enacted")) else \
              RED   if any(w in sl for w in ("defeated","failed","withdrawn")) else MUTED
        c1, c2 = st.columns([1,7])
        with c1:
            if bl and bpk and st.button(bl, key=f"mi_spon_nav_{bl}_{bpk}"):
                nav_bill(bpk, bl)
        with c2:
            st.markdown(
                f'<div style="font-size:.88rem;color:#e2e8f0">{t}</div>'
                f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:{sc}">'
                f'{_s(row.get("sponsor_type",""))} · {st_}</div>',
                unsafe_allow_html=True)
        st.markdown('<div style="border-top:1px solid #1c3050;margin:.15rem 0"></div>', unsafe_allow_html=True)


def _mi_alignment(peers_df, party, cross_df, floor_df):
    slbl("Ideological Alignment Map")
    if peers_df.empty:
        callout("Agreement data not yet computed (requires member_agreement analytics).", "info"); return

    c1, c2 = st.columns([2,1])
    with c1:
        top15 = peers_df.head(15).sort_values("agreement_score")
        fig = px.bar(top15, x="agreement_score", y="peer_name", orientation="h",
                     color="peer_party", color_discrete_map=PARTY_COLORS,
                     text="agreement_score", range_x=[0,1],
                     labels={"agreement_score":"Agreement","peer_name":""})
        fig.update_traces(texttemplate="%{text:.0%}",
                          textfont=dict(family="Roboto Mono, monospace", size=9))
        fig.update_layout(title_text="Top Voting Peers", height=320, **PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

    with c2:
        opp_party = "D" if party == "R" else "R"
        opp_peers = peers_df[peers_df["peer_party"] == opp_party]
        if not opp_peers.empty:
            slbl(f"Cross-party high agreement")
            st.caption(f"Members from the opposite party with highest alignment:")
            chips_html = '<div class="chip-grid">'
            for _, pr in opp_peers.head(8).iterrows():
                chips_html += chip(f"{_s(pr['peer_name'])} {pr['agreement_score']:.0%}", _s(pr["peer_party"]))
            chips_html += "</div>"
            st.markdown(chips_html, unsafe_allow_html=True)
            callout(f"<strong>Persuasion opportunity:</strong> These {opp_party} members already vote "
                    f"with this legislator frequently. They may be moveable on related bills.", "success")

        st.dataframe(
            peers_df[["peer_name","peer_party","shared_votes","agreement_score"]].head(20),
            use_container_width=True, hide_index=True)


def _mi_cross_aisle(cross_df, votes_cast):
    slbl("Cross-Aisle Vote History")
    pct = f"{len(cross_df)/votes_cast*100:.1f}%" if votes_cast > 0 else "—"
    stat_row([("Cross-Aisle Votes", len(cross_df)), ("% of Floor Votes", pct)])
    if cross_df.empty: empty_msg("No cross-aisle votes recorded."); return

    show = [c for c in ["vote_date","bill_label","title","vote_cast","party_majority_vote"] if c in cross_df.columns]
    st.dataframe(cross_df[show], use_container_width=True, hide_index=True)


def _mi_oppo(detail, floor_df, spon_df, cross_df, mm):
    slbl("Opposition Research Brief")

    party      = _s(detail.get("party",""))
    name       = _s(detail.get("full_name",""))
    chamber    = _s(detail.get("chamber","")).title()
    district   = _s(detail.get("district",""))
    vc         = int(mm.get("votes_cast", len(floor_df)) or len(floor_df))
    ar         = float(mm.get("absence_rate",0) or 0)
    yeas       = int(mm.get("yes_votes",0) or 0)
    ca_count   = len(cross_df)
    ca_rate    = ca_count / max(vc,1) * 100

    bills_primary = len(spon_df[spon_df["sponsor_type"]=="primary"]) if not spon_df.empty else 0
    bills_enacted = len(spon_df[spon_df["current_status"].str.lower().str.contains("signed|enacted", na=False)]) if not spon_df.empty else 0

    flags = []
    if ar > 0.20: flags.append(("High Absenteeism", f"{ar*100:.0f}% absence rate — reliability concern.", "danger"))
    if ca_rate > 20: flags.append(("Frequent Cross-Aisle Voter", f"Votes against party {ca_rate:.0f}% of the time — not a reliable base vote.", "warn"))
    if ca_rate < 2 and vc > 20: flags.append(("Party-Line Voter", f"Votes with party >{100-ca_rate:.0f}% — persuasion likely requires significant political pressure.", "warn"))
    if bills_primary > 0 and bills_enacted == 0: flags.append(("Low Legislative Effectiveness", f"0 of {bills_primary} primary-sponsored bills enacted.", "warn"))

    if not flags:
        callout("No major red flags detected from voting record.", "success")
    for lbl, msg, kind in flags:
        callout(f"<strong>⚑ {lbl}:</strong> {msg}", kind)

    st.markdown("---")
    st.markdown(
        f'<div style="background:#0f1e2e;border:1px solid #1c3050;border-radius:5px;'
        f'padding:1.1rem 1.4rem;font-family:\'Roboto Mono\',monospace;font-size:.72rem;line-height:2">'
        f'<div style="color:{GOLD};font-size:.58rem;letter-spacing:.16em;text-transform:uppercase;margin-bottom:.5rem">RAPID BRIEF</div>'
        f'<span style="color:#e2e8f0;font-weight:700">{name}</span> · {chamber} · District {district} · {party}<br>'
        f'Floor Votes: <span style="color:#e2e8f0">{vc:,}</span> &nbsp;|&nbsp; '
        f'Yes Rate: <span style="color:#3da86a">{yeas/max(vc,1)*100:.0f}%</span> &nbsp;|&nbsp; '
        f'Absence: <span style="color:{"#d05050" if ar>0.15 else "#94a3b8"}">{ar*100:.1f}%</span><br>'
        f'Cross-Aisle: <span style="color:#e2e8f0">{ca_count}</span> votes ({ca_rate:.1f}%)<br>'
        f'Bills Sponsored: <span style="color:#e2e8f0">{bills_primary}</span> primary · '
        f'<span style="color:#3da86a">{bills_enacted}</span> enacted<br>'
        f'Contact: {_s(detail.get("phone",""))} · {_s(detail.get("email",""))}'
        f'</div>',
        unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: COALITION BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def page_coalition_builder():
    page_header("Coalition Builder",
                "Who are my allies? Who might I flip?",
                "Persuadability · co-sponsor patterns · bipartisan opportunities")

    tabs = st.tabs([
        "🤝 Persuadable Members",
        "🔗 Co-Sponsor Network",
        "⚖️ Bipartisan Votes",
        "🎯 Target by Bill",
    ])

    with tabs[0]: _cb_persuadable()
    with tabs[1]: _cb_cosponsor()
    with tabs[2]: _cb_bipartisan()
    with tabs[3]: _cb_target_bill()


def _cb_persuadable():
    slbl("Most Persuadable Members — Cross-Aisle History")
    ca = cross_aisle_leaders_q(50)
    if ca.empty:
        callout("Cross-aisle data not yet computed (requires analytics tables).", "warn"); return

    callout(
        "<strong>How to use this:</strong> Members with a high cross-aisle rate have demonstrated "
        "willingness to break from their caucus. They are your highest-probability persuasion targets "
        "on close votes.", "info")

    c1, c2, c3 = st.columns(3)
    with c1: party_f   = st.selectbox("Party",   ["All","R","D","I"], key="cb_per_party")
    with c2: chamber_f = st.selectbox("Chamber", ["All","house","senate"], key="cb_per_ch")
    with c3: min_ca    = st.slider("Min cross-aisle votes", 1, 30, 3, key="cb_per_min")

    disp = ca.copy()
    if party_f   != "All": disp = disp[disp["party"]   == party_f]
    if chamber_f != "All": disp = disp[disp["chamber"] == chamber_f]
    disp = disp[disp["cross_aisle_count"] >= min_ca]

    if disp.empty: empty_msg("No members match those filters."); return

    c1, c2 = st.columns([2,1])
    with c1:
        fig = px.scatter(disp, x="votes_cast", y="cross_pct",
                         color="party", color_discrete_map=PARTY_COLORS,
                         size="cross_aisle_count",
                         hover_name="full_name",
                         labels={"votes_cast":"Total Floor Votes","cross_pct":"Cross-Aisle %"},
                         title="Cross-Aisle Rate vs Engagement")
        fig.update_layout(height=340, **PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
    with c2:
        slbl("Top Targets")
        for _, row in disp.head(10).iterrows():
            mid = row.get("member_id")
            c_btn, c_info = st.columns([1,2])
            with c_btn:
                if st.button(_s(row["full_name"])[:16], key=f"cb_per_nav_{mid}"):
                    nav_member(mid)
            with c_info:
                st.markdown(
                    f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.63rem">'
                    f'{badge(_s(row["party"]), "b-"+_s(row["party"]))} {row["cross_pct"]:.1f}% · {row["cross_aisle_count"]} votes'
                    f'</span>', unsafe_allow_html=True)


def _cb_cosponsor():
    slbl("Co-Sponsor Network Clusters")
    if tbl_exists("cosponsor_network"):
        cn = dbq("""
            SELECT cn.shared_bills, ma.full_name AS name_a, ma.party AS party_a,
                   mb.full_name AS name_b, mb.party AS party_b
            FROM cosponsor_network cn
            JOIN members ma ON ma.member_id = cn.sponsor_a
            JOIN members mb ON mb.member_id = cn.sponsor_b
            WHERE cn.shared_bills >= 3
            ORDER BY cn.shared_bills DESC LIMIT 40
        """)
        if not cn.empty:
            callout("<strong>Co-sponsor pairs</strong> with 3+ shared bills are natural coalition anchors. "
                    "Bipartisan pairs are especially valuable for amendment strategies.", "info")
            cross_pairs = cn[cn["party_a"] != cn["party_b"]]
            same_pairs  = cn[cn["party_a"] == cn["party_b"]]
            stat_row([
                ("Total Strong Pairs", len(cn)),
                ("Bipartisan Pairs",   len(cross_pairs), "highest value", "good"),
                ("Same-Party Pairs",   len(same_pairs)),
            ])
            fig = px.bar(cn.head(20).sort_values("shared_bills"),
                         x="shared_bills",
                         y=cn.head(20)["name_a"] + " × " + cn.head(20)["name_b"],
                         color="party_a", color_discrete_map=PARTY_COLORS,
                         orientation="h", text_auto=True,
                         labels={"shared_bills":"Shared Bills","y":""},
                         title="Top Co-Sponsor Pairs")
            fig.update_layout(height=420, **PLOTLY_LAYOUT)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
            return

    mm = member_metrics_q()
    if mm.empty: empty_msg("No sponsorship data available."); return
    c1, c2 = st.columns(2)
    with c1:
        top = mm.sort_values("primary_sponsored", ascending=False).head(20)
        fig = px.bar(top.sort_values("primary_sponsored"),
                     x="primary_sponsored", y="full_name",
                     color="party", color_discrete_map=PARTY_COLORS,
                     orientation="h", text_auto=True,
                     labels={"primary_sponsored":"Bills","full_name":""},
                     title="Top Primary Sponsors")
        fig.update_layout(height=400, **PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})
    with c2:
        fig2 = px.scatter(mm, x="primary_sponsored", y="co_sponsored",
                          color="party", color_discrete_map=PARTY_COLORS,
                          hover_name="full_name", size="votes_cast",
                          labels={"primary_sponsored":"Primary","co_sponsored":"Co-Sponsor"},
                          title="Primary vs Co-Sponsorship")
        fig2.update_layout(height=400, **PLOTLY_LAYOUT)
        st.plotly_chart(fig2, use_container_width=True, config={"displayModeBar":False})


def _cb_bipartisan():
    slbl("Most Bipartisan Floor Votes")
    callout("<strong>Bipartisan votes</strong> reveal where the parties naturally overlap. "
            "Bills generating these votes are easier to move with cross-aisle co-sponsors.", "info")

    if not tbl_exists("cross_aisle_votes"):
        callout("Bipartisan vote data requires cross_aisle_votes analytics table.", "warn"); return

    bpv = dbq("""
        SELECT rc.roll_call_id, rc.vote_date, b.bill_label, b.bill_pk, b.title,
               rc.motion_text, rc.yes_count, rc.no_count, rc.passed,
               COUNT(ca.member_id) AS cross_aisle_count,
               ROUND(100.0*COUNT(ca.member_id)/NULLIF(rc.yes_count+rc.no_count,0),1) AS cross_pct
        FROM roll_calls rc
        LEFT JOIN bills b ON b.bill_pk = rc.bill_pk
        LEFT JOIN cross_aisle_votes ca ON ca.roll_call_id = rc.roll_call_id
        WHERE rc.vote_context='floor'
        GROUP BY rc.roll_call_id
        HAVING cross_aisle_count > 0
        ORDER BY cross_pct DESC LIMIT 50
    """)
    if bpv.empty: empty_msg("No bipartisan vote data."); return

    fig = px.scatter(bpv, x="yes_count", y="cross_pct",
                     hover_name="bill_label",
                     hover_data={"vote_date":True,"passed":True},
                     color="passed", color_discrete_map={1:GREEN, 0:RED},
                     labels={"yes_count":"Yes Votes","cross_pct":"Cross-Aisle %"},
                     title="Bipartisan Votes — Yes Count vs Cross-Party %")
    fig.update_layout(height=320, **PLOTLY_LAYOUT)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

    for _, row in bpv.head(20).iterrows():
        bl  = _s(row.get("bill_label",""))
        bpk = row.get("bill_pk")
        ttl = _s(row.get("title",""))[:70]
        y   = int(row.get("yes_count",0) or 0)
        n   = int(row.get("no_count",0) or 0)
        cp  = row.get("cross_pct",0)
        ps  = row.get("passed")
        c1, c2 = st.columns([1,8])
        with c1:
            if bl and bpk and st.button(bl, key=f"cb_bp_{bpk}_{row.get('roll_call_id','')}"):
                nav_bill(bpk, bl)
        with c2:
            st.markdown(
                f'<span style="color:{GREEN};font-family:\'Roboto Mono\',monospace;font-size:.65rem">'
                f'{cp:.0f}% bipartisan</span> '
                f'<span style="font-size:.86rem;color:#e2e8f0">{ttl}</span><br>'
                f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:#5a6580">'
                f'{y}Y/{n}N · {"Passed" if ps else "Failed"}</span>',
                unsafe_allow_html=True)
        st.markdown('<div style="border-top:1px solid #1c3050;margin:.15rem 0"></div>', unsafe_allow_html=True)


def _cb_target_bill():
    slbl("Find Potential Supporters for a Specific Bill")
    callout("Select a bill and see which members from the opposite party have previously voted "
            "YES on similar measures — your highest-probability swing targets.", "info")

    bills_df = get_all_bills()
    if bills_df.empty: empty_msg("No bills."); return

    b_q = st.text_input("Search bill", placeholder="SB 100, education…", key="cb_tgt_q")
    df  = bills_df.copy()
    if b_q.strip():
        df = df[df["bill_label"].str.upper().str.contains(b_q.upper(), na=False) |
                df["title"].fillna("").str.lower().str.contains(b_q.lower(), na=False)]

    if df.empty: empty_msg("No bills match."); return

    def _lbl(r):
        t = _s(r.get("title",""))
        return f"{r['bill_label']}  —  {t[:55]}{'…' if len(t)>55 else ''}"

    opts = ["— select —"] + [_lbl(r) for _, r in df.head(150).iterrows()]
    pk_m = {_lbl(r): r["bill_pk"] for _, r in df.head(150).iterrows()}
    chosen = st.selectbox("Bill", opts, key="cb_tgt_sel")
    if chosen == "— select —": return

    bpk = pk_m[chosen]
    spon_df = bill_sponsors_q(bpk)

    primary    = spon_df[spon_df["sponsor_type"]=="primary"] if not spon_df.empty else pd.DataFrame()
    bill_party = _s(primary.iloc[0]["party"]) if not primary.empty else "R"
    opp_party  = "D" if bill_party == "R" else "R"

    floor_df = bill_floor_votes_q(bpk)
    if floor_df.empty:
        callout("No floor votes on record for this bill yet — check back once it moves to the floor.", "info")
        return

    last_rc = floor_df.iloc[0].get("roll_call_id")
    mv      = roll_call_member_votes_q(last_rc)

    opp_yes = mv[(mv["party"] == opp_party) & (mv["vote_cast"] == "yes")]
    opp_no  = mv[(mv["party"] == opp_party) & (mv["vote_cast"] == "no")]
    own_no  = mv[(mv["party"] == bill_party) & (mv["vote_cast"] == "no")]

    stat_row([
        ("Opp. Party Yes Votes", len(opp_yes), f"{opp_party} already on board", "good" if not opp_yes.empty else ""),
        ("Opp. Party No Votes",  len(opp_no),  f"{opp_party} targets to move", "warn"),
        ("Own Party No Votes",   len(own_no),  "own caucus defectors", "alert" if not own_no.empty else ""),
    ])

    c1, c2 = st.columns(2)
    with c1:
        if not opp_yes.empty:
            slbl(f"✓ {opp_party} Members Already Voting Yes (keep them)")
            chips_html = '<div class="chip-grid">'
            for _, mr in opp_yes.iterrows():
                chips_html += chip(_s(mr["full_name"]), _s(mr["party"]))
            chips_html += "</div>"
            st.markdown(chips_html, unsafe_allow_html=True)
        if not own_no.empty:
            slbl(f"⚠ Own Party ({bill_party}) Defectors — Need to Win Back")
            chips_html = '<div class="chip-grid">'
            for _, mr in own_no.iterrows():
                chips_html += chip(_s(mr["full_name"]), _s(mr["party"]))
            chips_html += "</div>"
            st.markdown(chips_html, unsafe_allow_html=True)
    with c2:
        if not opp_no.empty:
            slbl(f"🎯 {opp_party} No Votes — Persuasion Targets")
            for _, mr in opp_no.iterrows():
                mid = mr.get("member_id")
                c_btn, c_info = st.columns([1,3])
                with c_btn:
                    if st.button(_s(mr["full_name"])[:16], key=f"cb_tgt_mem_{mid}_{bpk}"):
                        nav_member(mid)
                with c_info:
                    st.markdown(
                        f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.62rem;color:#5a6580">'
                        f'D{_s(mr.get("district","—"))} · {_s(mr.get("chamber","")).title()}'
                        f'</span>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: WHIP COUNT
# ═══════════════════════════════════════════════════════════════════════════

def page_whip_count():
    page_header("Whip Floor Votes",
                "Kill-able bills",
                "Real vote margins · scenario modeling · target identification")
 
    session  = st.session_state.get("session_filter")
    bills_df = get_all_bills(session)
    if bills_df.empty: callout("No bills.", "warn"); return
 
    b_q = st.text_input("Search bill for whip count", placeholder="SB 100, HB 45…", key="wc_q")
    df  = bills_df.copy()
    if b_q.strip():
        df = df[df["bill_label"].str.upper().str.contains(b_q.upper(), na=False) |
                df["title"].fillna("").str.lower().str.contains(b_q.lower(), na=False)]
 
    if df.empty and b_q.strip():
        empty_msg("No bills match."); return
 
    def _lbl(r):
        t = _s(r.get("title",""))
        return f"{r['bill_label']}  —  {t[:60]}{'…' if len(t)>60 else ''}"
 
    opts = ["— select a bill to whip —"] + [_lbl(r) for _, r in df.head(150).iterrows()]
    pk_m = {_lbl(r): r["bill_pk"] for _, r in df.head(150).iterrows()}
    chosen = st.selectbox("Bill", opts, key="wc_sel")
    if chosen == "— select a bill to whip —":
        callout("Select a bill above to see its whip count and model vote scenarios.", "info")
 
        slbl("All Close Floor Votes (margin <10%)")
        close = dbq("""
            SELECT b.bill_label, b.bill_pk, b.title, rc.vote_date, rc.reading_stage,
                   rc.yes_count, rc.no_count, rc.passed,
                   ABS(rc.yes_count - rc.no_count) AS margin
            FROM roll_calls rc
            JOIN bills b ON b.bill_pk = rc.bill_pk
            WHERE rc.vote_context='floor'
              AND rc.yes_count + rc.no_count > 0
              AND ABS(rc.yes_count - rc.no_count) * 10 < rc.yes_count + rc.no_count
            ORDER BY margin ASC LIMIT 30
        """)
        # ✓ FIX: Use enumerate for unique keys instead of roll_call_id
        if not close.empty:
            for idx, (_, row) in enumerate(close.iterrows()):
                bl  = _s(row.get("bill_label",""))
                bpk = row.get("bill_pk")
                ttl = _s(row.get("title",""))[:70]
                y   = int(row.get("yes_count",0) or 0)
                n   = int(row.get("no_count",0) or 0)
                m   = int(row.get("margin",0) or 0)
                ps  = row.get("passed")
                c1, c2 = st.columns([1,8])
                with c1:
                    # ✓ FIX: Unique key using idx
                    if bl and bpk and st.button(bl, key=f"wc_close_{bpk}_{idx}"):
                        nav_bill(bpk, bl)
                with c2:
                    col = GREEN if ps else RED
                    st.markdown(
                        f'<span style="color:{col};font-family:\'Roboto Mono\',monospace;font-size:.65rem;font-weight:700">'
                        f'{y}Y / {n}N  (margin: {m:+d})</span> '
                        f'<span style="font-size:.85rem;color:#e2e8f0">{ttl}</span>',
                        unsafe_allow_html=True)
                st.markdown('<div style="border-top:1px solid #1c3050;margin:.15rem 0"></div>', unsafe_allow_html=True)
        return
 
    bpk = pk_m[chosen]
    _whip_count_detail(bpk)


def _whip_count_detail(bill_pk):
    detail   = bill_detail_q(bill_pk)
    floor_df = bill_floor_votes_q(bill_pk)
    spon_df  = bill_sponsors_q(bill_pk)

    label  = _s(detail.get("bill_label",""))
    title  = _s(detail.get("title",""))
    status = _s(detail.get("current_status",""))

    callout(f"<strong>{label}</strong> — {title[:80]} · {status}", "info")

    if floor_df.empty:
        callout("No floor votes on record. This whip count will be based on sponsorship only.", "warn")
        if not spon_df.empty:
            stat_row([
                ("Primary Sponsors", len(spon_df[spon_df["sponsor_type"]=="primary"])),
                ("Co-Sponsors",      len(spon_df[spon_df["sponsor_type"]=="cosponsor"])),
            ])
        return

    last   = floor_df.iloc[0]
    rc_id  = last.get("roll_call_id")
    y      = int(last.get("yes_count",0) or 0)
    n      = int(last.get("no_count",0)  or 0)
    p      = int(last.get("present_count",0) or 0)
    a      = int(last.get("absent_count",0) or 0)
    passed = last.get("passed")
    total  = y + n + p + a
    needed = (total // 2) + 1
    margin = y - n
    gap    = needed - y if not passed else 0

    stat_row([
        ("Yes",           y,  "", "good"  if y >= needed else "alert"),
        ("No",            n,  "", "alert" if n > y else ""),
        ("Present",       p),
        ("Absent",        a,  "potential votes", "warn" if a > 2 else ""),
        ("Needed",        needed),
        ("Current Margin", f"{margin:+d}", "votes", "good" if margin > 0 else "alert"),
        ("Gap to Pass",   max(gap,0) if not passed else "✓ PASSED", "", "alert" if gap > 0 else "good"),
    ])

    st.markdown(vbar(y, n, p, a), unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    if passed:
        callout(f"<strong>Bill passed</strong> this vote {y}–{n}. Monitor for subsequent amendments or cross-chamber action.", "success")
    elif gap > 0 and a >= gap:
        callout(f"<strong>Absentees could flip the outcome</strong> — {a} absent members, need {gap} more yes. "
                f"Pursue those absent members first.", "danger")
    elif gap > 0:
        callout(f"<strong>Need {gap} more yes vote(s)</strong> — "
                f"focus on present ({p}) and no votes that are persuadable.", "danger")

    mv = roll_call_member_votes_q(rc_id)
    if mv.empty: return

    slbl("Member Vote Detail — Last Floor Vote")

    c1, c2, c3, c4 = st.columns(4)
    for vtype, col_, label_, c_col in [
        ("yes",     GREEN, "✓ YES",     c1),
        ("no",      RED,   "✗ NO",      c2),
        ("present", AMBER, "○ PRESENT", c3),
        ("absent",  MUTED, "– ABSENT",  c4),
    ]:
        sub = mv[mv["vote_cast"] == vtype]
        with c_col:
            slbl(f"{label_} ({len(sub)})")
            chips_html = '<div class="chip-grid">'
            for _, mr in sub.iterrows():
                chips_html += chip(_s(mr["full_name"]), _s(mr["party"]))
            chips_html += "</div>"
            st.markdown(chips_html, unsafe_allow_html=True)

    st.markdown("---")
    slbl("🎛 Scenario Modeler")
    callout("Use the sliders to model what happens if you can flip present/absent/no members.", "info")

    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1: flip_present = st.slider("Present → Yes", 0, p, 0, key="wc_fp")
    with col_s2: flip_absent  = st.slider("Absent → Yes",  0, a, 0, key="wc_fa")
    with col_s3: flip_no      = st.slider("No → Yes",      0, n, 0, key="wc_fn")

    new_yes = y + flip_present + flip_absent + flip_no
    new_no  = n - flip_no
    result  = "✓ PASSES" if new_yes >= needed else "✗ FAILS"
    rc      = GREEN if new_yes >= needed else RED
    st.markdown(
        f'<div style="background:#0f1e2e;border:1px solid #1c3050;border-radius:5px;'
        f'padding:.9rem 1.2rem;text-align:center;margin:.8rem 0">'
        f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:#5a6580;margin-bottom:.25rem">SCENARIO RESULT</div>'
        f'<div style="font-family:\'Roboto Mono\',monospace;font-size:1.2rem;font-weight:700;color:{rc}">'
        f'{result}</div>'
        f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.78rem;color:#94a3b8;margin-top:.15rem">'
        f'{new_yes} YES · {new_no} NO · needed {needed}</div>'
        f'</div>',
        unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: LANGUAGE & LINEAGE
# ═══════════════════════════════════════════════════════════════════════════

def page_lineage():
    page_header("Language & Lineage",
                "Where did this come from? Has it failed before?",
                "Zombie watch · idea tracker · language search")

    if not tbl_exists("language_lineage") and not tbl_exists("bill_language_fragments"):
        callout("Language lineage tables not populated. Run intelligence/resurrection.py first.", "warn")
        return

    tabs = st.tabs(["Language Search", "Idea Browser", "Cross-Session Revived Langage"])
    with tabs[0]: _lin_language_search()
    with tabs[1]: _lin_idea_tracker()
    with tabs[2]: _lin_zombie_watch()


def _lin_zombie_watch():
    slbl("🧟  Zombie Bills — Failed, Then Came Back")
    callout("<strong>Zombie bills</strong> are legislation that failed in one session but resurfaced "
            "with substantially the same language in a later session. Knowing the history is critical — "
            "why did it fail before? Did the political context change?", "info")

    if not tbl_exists("language_lineage"):
        callout("language_lineage not populated.", "warn"); return

    all_lin = global_lineage_q(5000)
    if all_lin.empty: empty_msg("No lineage data."); return

    zombies = all_lin[all_lin["match_type"].isin(["zombie","reintroduced"])].copy()
    if zombies.empty:
        callout("This data has not yet been calculated.", "info"); return

    c1, c2, c3 = st.columns(3)
    with c1: zm_kw   = st.text_input("Filter", key="lz_kw")
    with c2: zm_type = st.selectbox("Type", ["All","zombie","reintroduced"], key="lz_type")
    with c3: zm_sim  = st.slider("Min similarity", 0.5, 1.0, 0.7, 0.05, key="lz_sim")

    disp = zombies[zombies["similarity_score"] >= zm_sim].copy()
    if zm_type != "All": disp = disp[disp["match_type"] == zm_type]
    if zm_kw.strip():
        for t in zm_kw.lower().split():
            disp = disp[disp.apply(
                lambda r: t in " ".join(_s(v) for v in r.values).lower(),
                axis=1)]

    n_z = len(disp[disp["match_type"]=="zombie"])
    n_r = len(disp[disp["match_type"]=="reintroduced"])
    stat_row([("Zombies", n_z, "", "danger" if n_z else ""),
              ("Reintroduced", n_r),
              ("Avg Similarity", f"{disp['similarity_score'].mean():.0%}" if not disp.empty else "—")])

    if disp.empty: empty_msg("No bills match."); return

    for _, row in disp.iterrows():
        src_bill  = _s(row.get("source_bill",""));  src_sess = _s(row.get("source_session",""))
        tgt_bill  = _s(row.get("target_bill",""));  tgt_sess = _s(row.get("target_session",""))
        src_ttl   = _s(row.get("source_title",""))
        tgt_ttl   = _s(row.get("target_title",""))
        src_spon  = _s(row.get("source_sponsor","")) or "—"
        tgt_spon  = _s(row.get("target_sponsor","")) or "—"
        src_pty   = _s(row.get("source_party",""))
        tgt_pty   = _s(row.get("target_party",""))
        sim       = float(row.get("similarity_score",0) or 0)
        mt        = _s(row.get("match_type",""))
        spk       = row.get("source_bill_pk")
        tpk       = row.get("target_bill_pk")
        same_spon = src_spon == tgt_spon and src_spon != "—"
        bar_w     = int(sim * 100)
        icon      = "🧟" if mt == "zombie" else "↻"

        st.markdown(
            f'<div style="border:1px solid #1c3050;border-radius:5px;margin:.6rem 0;overflow:hidden">'
            f'<div style="background:#152235;padding:.45rem 1rem;display:flex;justify-content:space-between">'
            f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.68rem;font-weight:700;color:#e2e8f0">'
            f'{icon} {mt.title()} · {sim:.0%} similar</span>'
            f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:{GOLD}">'
            f'{"same sponsor" if same_spon else f"{src_spon} → {tgt_spon}"}</span></div>'
            f'<div style="height:3px;background:#1c3050"><div style="height:3px;background:{GOLD};width:{bar_w}%"></div></div>'
            f'<div style="display:grid;grid-template-columns:1fr 30px 1fr">'
            f'<div style="padding:.6rem 1rem;border-right:1px solid #1c3050">'
            f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.55rem;color:#5a6580">ORIGINAL · {src_sess}</div>'
            f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.8rem;font-weight:700;color:{GOLD}">{src_bill}</div>'
            f'<div style="font-size:.82rem;color:#94a3b8;margin:.1rem 0">{src_ttl[:75]}</div>'
            f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:#5a6580">'
            f'{src_spon}{" ("+src_pty+")" if src_pty else ""}</div></div>'
            f'<div style="text-align:center;padding-top:1rem;color:{GOLD};font-size:1rem">→</div>'
            f'<div style="padding:.6rem 1rem;border-left:1px solid #1c3050">'
            f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.55rem;color:#5a6580">REVIVED · {tgt_sess}</div>'
            f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.8rem;font-weight:700;color:{GOLD}">{tgt_bill}</div>'
            f'<div style="font-size:.82rem;color:#94a3b8;margin:.1rem 0">{tgt_ttl[:75]}</div>'
            f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:#5a6580">'
            f'{tgt_spon}{" ("+tgt_pty+")" if tgt_pty else ""}</div></div>'
            f'</div></div>',
            unsafe_allow_html=True)

        nv1, nv2, _ = st.columns([1,1,4])
        with nv1:
            if spk and st.button(f"↗ {src_bill}", key=f"lz_src_{spk}_{tpk}"):
                nav_bill(spk, src_bill)
        with nv2:
            if tpk and st.button(f"↗ {tgt_bill}", key=f"lz_tgt_{tpk}_{spk}"):
                nav_bill(tpk, tgt_bill)
        st.markdown("<br>", unsafe_allow_html=True)


def _lin_idea_tracker():
    slbl("💡  Idea Tracker — Trace a Policy Across Sessions")
    callout("Search for a topic or phrase to see every bill containing that language, "
            "who championed it in each session, and whether it ever passed.", "info")
 
    if not tbl_exists("bill_language_fragments"):
        callout("bill_language_fragments not populated.", "warn"); return
 
    # ✓ FIX: Changed key from "li_kw" to "li_kw_input"
    idea_kw = st.text_input("Topic or phrase", placeholder="rural broadband, mandatory minimum…", key="li_kw_input")
 
    if len(idea_kw.strip()) < 4:
        suggs = ["mental health","income tax","law enforcement","public school",
                 "property tax","health care","sentencing","transportation","election","veterans"]
        slbl("Quick picks")
        cols = st.columns(5)
        for i, s in enumerate(suggs):
            with cols[i % 5]:
                # ✓ FIX: Use enumerate index in key (not the string suggestion)
                if st.button(s, key=f"li_sugg_{i}", use_container_width=True):
                    # ✓ FIX: Set a different session state key
                    st.session_state["li_kw_value"] = s
                    st.rerun()
        
        # ✓ FIX: Check the session state value set by button callback
        if st.session_state.get("li_kw_value"):
            idea_kw = st.session_state["li_kw_value"]
        else:
            return
 
    if not idea_kw.strip():
        return
 
    bills_kw = dbq("""
        SELECT DISTINCT b.bill_pk, b.bill_label, b.session_id, b.title,
               b.current_status, b.chamber,
               m.full_name AS sponsor, m.party AS sponsor_party
        FROM bill_language_fragments blf
        JOIN bills b ON b.bill_pk = blf.bill_pk
        LEFT JOIN bill_sponsors bs ON bs.bill_pk = b.bill_pk AND bs.sponsor_type='primary'
        LEFT JOIN members m ON m.member_id = bs.member_id
        WHERE blf.fragment_text LIKE ?
        ORDER BY b.session_id DESC, b.bill_label
    """, (f"%{idea_kw.strip()}%",))
 
    if bills_kw.empty:
        callout(f'No bills found containing "{idea_kw}". Try a shorter phrase.', "warn"); return
 
    n_passed = bills_kw["current_status"].str.lower().str.contains("signed|enacted", na=False).sum()
    stat_row([
        ("Bills Found",     len(bills_kw)),
        ("Sessions",        bills_kw["session_id"].nunique()),
        ("Became Law",      int(n_passed), "", "good" if n_passed > 0 else "alert"),
        ("Unique Sponsors", bills_kw["sponsor"].dropna().nunique()),
    ])
 
    if n_passed == 0:
        callout(f"<strong>This idea has never been enacted.</strong> "
                f"Review the history below to understand why and who has tried.", "warn")
    else:
        callout(f"<strong>This idea passed {n_passed} time(s).</strong> "
                f"Study those successful sessions for coalition and framing strategies.", "success")
 
    for sess in sorted(bills_kw["session_id"].unique()):
        slbl(str(sess))
        grp = bills_kw[bills_kw["session_id"]==sess]
        # ✓ FIX: Enumerate to get unique index for button key
        for idx_nav, (_, brow) in enumerate(grp.iterrows()):
            pk   = brow["bill_pk"];   lbl = _s(brow.get("bill_label",""))
            ttl  = _s(brow.get("title",""))[:80]
            st_  = _s(brow.get("current_status",""))
            spon = _s(brow.get("sponsor",""))
            pty  = _s(brow.get("sponsor_party",""))
            sl   = st_.lower()
            sc   = GREEN if any(w in sl for w in ("signed","enacted")) else \
                   RED   if any(w in sl for w in ("defeated","failed","withdrawn")) else MUTED
            c1, c2 = st.columns([1,9])
            with c1:
                # ✓ FIX: Include idx_nav in key for uniqueness
                if pk and st.button(lbl, key=f"li_nav_{pk}_{idx_nav}_{sess}"):
                    nav_bill(pk, lbl)
            with c2:
                st.markdown(
                    f'{pbadge(pty)} <span style="font-size:.88rem;color:#e2e8f0">{ttl}</span><br>'
                    f'<span style="font-family:\'Roboto Mono\',monospace;font-size:.6rem;color:{sc}">'
                    f'{spon} · {st_[:55]}</span>',
                    unsafe_allow_html=True)


def _lin_language_search():
    slbl("🔍  Language Search — Find Any Phrase in Any Bill")
    if not tbl_exists("bill_language_fragments"):
        callout("bill_language_fragments not populated.", "warn"); return
 
    s1, s2 = st.columns([3,1])
    # ✓ FIX: Changed key from "ls_kw" to "ls_kw_input"
    with s1: ls_kw = st.text_input("Search text (≥4 chars)", placeholder="section 144.030, preemption…", key="ls_kw_input")
    with s2:
        sess_df  = get_sessions()
        sess_opts = ["All"] + (sess_df["session_id"].tolist() if not sess_df.empty else [])
        ls_sess  = st.selectbox("Session", sess_opts, key="ls_sess")
 
    if len(ls_kw.strip()) < 4:
        tf = scalar("SELECT COUNT(*) FROM bill_language_fragments") or 0
        tb = scalar("SELECT COUNT(DISTINCT bill_pk) FROM bill_language_fragments") or 0
        stat_row([("Fragments Indexed", f"{tf:,}"), ("Bills Indexed", f"{tb:,}")])
        return
 
    sess_c = "AND b.session_id=?" if ls_sess != "All" else ""
    params = [f"%{ls_kw.strip()}%"] + ([ls_sess] if ls_sess != "All" else [])
 
    with st.spinner("Searching…"):
        results = dbq(f"""
            SELECT b.bill_label, b.bill_pk, b.session_id, b.title, b.current_status, b.chamber,
                   blf.fragment_type, blf.fragment_index,
                   SUBSTR(blf.fragment_text,1,600) AS excerpt, bv.version_label
            FROM bill_language_fragments blf
            JOIN bills b ON b.bill_pk = blf.bill_pk
            LEFT JOIN bill_versions bv ON bv.version_id = blf.version_id
            WHERE blf.fragment_text LIKE ? {sess_c}
            ORDER BY b.session_id DESC, b.bill_label LIMIT 150
        """, params)
 
    if results.empty:
        callout(f'No fragments found for "{ls_kw}".', "warn"); return
 
    n_bills = results["bill_pk"].nunique()
    n_sess  = results["session_id"].nunique()
    stat_row([("Matching Fragments", len(results)), ("Bills", n_bills), ("Sessions", n_sess)])
 
    # ✓ FIX: Enumerate to get unique index
    for bpk_idx, bpk in enumerate(results["bill_pk"].unique()):
        bfrags = results[results["bill_pk"]==bpk]
        first  = bfrags.iloc[0]
        bl     = _s(first.get("bill_label",""))
        ttl    = _s(first.get("title",""))[:60]
        n      = len(bfrags)
        with st.expander(f"{bl}  —  {n} match{'es' if n!=1 else ''}  ·  {ttl}",
                         expanded=(n_bills <= 3)):
            c1, c2 = st.columns([1,5])
            with c1:
                # ✓ FIX: Include bpk_idx in key
                if st.button("View Bill →", key=f"ls_nav_{bpk}_{bpk_idx}"):
                    nav_bill(bpk, bl)
            with c2:
                st.caption(f'{_s(first.get("current_status",""))} · {_s(first.get("chamber","")).title()}')
            for frag_idx, (_, frow) in enumerate(bfrags.iterrows()):
                frag_block(_s(frow.get("excerpt",""))[:500],
                           meta=f"{_s(frow.get('version_label',''))} · frag #{frow.get('fragment_index','')}",
                           highlight=ls_kw.strip())

# ═══════════════════════════════════════════════════════════════════════════
# PAGE: FIELD REPORTS (PDF export)
# ═══════════════════════════════════════════════════════════════════════════

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors as rl_colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Table, TableStyle, Paragraph,
        Spacer, HRFlowable, PageBreak
    )
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

def page_field_reports():
    page_header("Field Reports",
                "Build a leave-behind or briefing document.",
                "PDF exports for legislative, campaign, and advocacy use")

    if not REPORTLAB_OK:
        callout("**reportlab** not installed. Run `pip install reportlab` to enable PDF export.", "warn")
        return

    members_df = get_members()
    bills_df   = get_all_bills()

    REPORT_TYPES = {
        "member_brief":      "Member Intelligence Brief (1-pager)",
        "bill_whip_sheet":   "Bill Whip Sheet",
        "coalition_targets": "Coalition Target List",
        "vote_record":       "Member Vote Record",
        "bill_timeline":     "Bill Action Timeline",
    }

    st.markdown('<div style="background:#0f1e2e;border:1px solid #1c3050;border-radius:5px;padding:.9rem 1.1rem;margin-bottom:1rem">', unsafe_allow_html=True)
    rtype_lbl = st.selectbox("Report type", list(REPORT_TYPES.values()), key="fr_rtype")
    rtype = [k for k,v in REPORT_TYPES.items() if v == rtype_lbl][0]

    c1, c2 = st.columns(2)
    with c1: rpt_title = st.text_input("Document title", value=rtype_lbl, key="fr_title")
    with c2: rpt_sub   = st.text_input("Subtitle/date", value=datetime.now().strftime("%B %d, %Y"), key="fr_sub")
    st.markdown("</div>", unsafe_allow_html=True)

    member_id = bill_pk = None

    if rtype in ("member_brief","vote_record","coalition_targets"):
        member_opts = ["— select —"] + (members_df["full_name"].tolist() if not members_df.empty else [])
        chosen_mem  = st.selectbox("Select member", member_opts, key="fr_mem")
        if chosen_mem != "— select —" and not members_df.empty:
            member_id = int(members_df[members_df["full_name"]==chosen_mem]["member_id"].iloc[0])

    if rtype in ("bill_whip_sheet","bill_timeline"):
        bill_opts = ["— select —"] + (bills_df["bill_label"].tolist() if not bills_df.empty else [])
        chosen_bill = st.selectbox("Select bill", bill_opts, key="fr_bill")
        if chosen_bill != "— select —" and not bills_df.empty:
            bill_pk = int(bills_df[bills_df["bill_label"]==chosen_bill]["bill_pk"].iloc[0])

    if st.button("⬇ Generate Report", type="primary", key="fr_gen"):
        needs_member = rtype in ("member_brief","vote_record","coalition_targets")
        needs_bill   = rtype in ("bill_whip_sheet","bill_timeline")
        if needs_member and not member_id:
            st.error("Select a member first."); return
        if needs_bill and not bill_pk:
            st.error("Select a bill first."); return

        with st.spinner("Building PDF…"):
            pdf_bytes = _build_field_report(rtype, rpt_title, rpt_sub, member_id, bill_pk, members_df, bills_df)

        if pdf_bytes:
            fname = re.sub(r"[^a-zA-Z0-9_-]","_",rpt_title)[:40].lower() + ".pdf"
            st.success("Report ready.")
            st.download_button("Download PDF", data=pdf_bytes, file_name=fname,
                               mime="application/pdf", use_container_width=True)


def _pdf_ts():
    return TableStyle([
        ("BACKGROUND",     (0,0),(-1,0),  rl_colors.HexColor("#0b1622")),
        ("TEXTCOLOR",      (0,0),(-1,0),  rl_colors.HexColor("#c8a450")),
        ("FONTNAME",       (0,0),(-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",       (0,0),(-1,0),  7.5),
        ("ROWBACKGROUNDS", (0,1),(-1,-1), [rl_colors.HexColor("#0f1e2e"), rl_colors.HexColor("#152235")]),
        ("TEXTCOLOR",      (0,1),(-1,-1), rl_colors.HexColor("#94a3b8")),
        ("FONTNAME",       (0,1),(-1,-1), "Helvetica"),
        ("FONTSIZE",       (0,1),(-1,-1), 7),
        ("GRID",           (0,0),(-1,-1), 0.3, rl_colors.HexColor("#1c3050")),
        ("VALIGN",         (0,0),(-1,-1), "MIDDLE"),
        ("TOPPADDING",     (0,0),(-1,-1), 3),
        ("BOTTOMPADDING",  (0,0),(-1,-1), 3),
        ("LEFTPADDING",    (0,0),(-1,-1), 5),
        ("RIGHTPADDING",   (0,0),(-1,-1), 5),
        ("LINEBELOW",      (0,0),(-1,0),  1, rl_colors.HexColor("#c8a450")),
    ])

def _build_field_report(rtype, title, subtitle, member_id, bill_pk, members_df, bills_df):
    import io as _io
    buf = _io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=0.7*inch, rightMargin=0.7*inch,
                            topMargin=0.75*inch, bottomMargin=0.65*inch, title=title)
    ss  = getSampleStyleSheet()
    H1  = ParagraphStyle("H1", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=14, textColor=rl_colors.HexColor("#0b1622"), spaceAfter=3)
    H2  = ParagraphStyle("H2", parent=ss["Normal"], fontName="Helvetica-Bold",
                          fontSize=10, textColor=rl_colors.HexColor("#0b1622"),
                          spaceAfter=3, spaceBefore=9)
    Sub = ParagraphStyle("Sub", parent=ss["Normal"], fontName="Helvetica",
                          fontSize=8, textColor=rl_colors.HexColor("#5a6580"), spaceAfter=8)
    Body= ParagraphStyle("Body",parent=ss["Normal"], fontName="Helvetica",
                          fontSize=8, leading=12, spaceAfter=5)
    story = [Paragraph(title, H1), Paragraph(subtitle, Sub),
             HRFlowable(width="100%", thickness=1, color=rl_colors.HexColor("#c8a450"), spaceAfter=8)]

    def _tbl(df, cols=None):
        if df is None or df.empty:
            story.append(Paragraph("No data.", Body)); return
        c = cols if cols else list(df.columns)
        c = [x for x in c if x in df.columns]
        data = [c] + [[_s(row[x]) for x in c] for _, row in df[c].iterrows()]
        t = Table(data, repeatRows=1)
        t.setStyle(_pdf_ts())
        story.append(t)

    if rtype == "member_brief" and member_id:
        row = dbq("SELECT * FROM members WHERE member_id=?", (member_id,))
        if not row.empty:
            d = row.iloc[0].to_dict()
            story.append(Paragraph(f"Member Intelligence Brief: {_s(d.get('full_name',''))}", H1))
            story.append(Paragraph(f"{_s(d.get('chamber','')).title()} · District {_s(d.get('district',''))} · {_s(d.get('party',''))}", Sub))
            story.append(Paragraph(f"Phone: {_s(d.get('phone',''))}  |  Email: {_s(d.get('email',''))}", Body))
        floor_df = member_floor_votes_q(member_id)
        story.append(Paragraph("Voting Record Summary", H2))
        vc = floor_df["vote_cast"].value_counts() if not floor_df.empty else {}
        story.append(Paragraph(
            f"Total floor votes: {len(floor_df)} · "
            f"Yes: {int(vc.get('yes',0))} · No: {int(vc.get('no',0))} · "
            f"Absent: {int(vc.get('absent',0))}", Body))
        cross_df = member_cross_aisle_q(member_id)
        if not cross_df.empty:
            story.append(Paragraph(f"Cross-aisle votes: {len(cross_df)} ({len(cross_df)/max(len(floor_df),1)*100:.1f}%)", Body))
        spon_df = member_sponsored_q(member_id)
        story.append(Paragraph("Sponsored Bills", H2))
        _tbl(spon_df, ["bill_label","session_id","title","current_status","sponsor_type"])

    elif rtype == "bill_whip_sheet" and bill_pk:
        detail   = bill_detail_q(bill_pk)
        floor_df = bill_floor_votes_q(bill_pk)
        story.append(Paragraph(f"Whip Sheet: {_s(detail.get('bill_label',''))} — {_s(detail.get('title',''))[:70]}", H1))
        story.append(Paragraph(f"Status: {_s(detail.get('current_status',''))} · {subtitle}", Sub))
        if not floor_df.empty:
            last = floor_df.iloc[0]
            y = int(last.get("yes_count",0) or 0)
            n = int(last.get("no_count",0) or 0)
            story.append(Paragraph(f"Last Floor Vote: {y} YES / {n} NO · {'PASSED' if last.get('passed') else 'FAILED'}", H2))
            mv = roll_call_member_votes_q(last.get("roll_call_id"))
            _tbl(mv, ["full_name","party","chamber","district","vote_cast"])

    elif rtype == "vote_record" and member_id:
        floor_df = member_floor_votes_q(member_id)
        story.append(Paragraph("Floor Vote Record", H2))
        _tbl(floor_df, ["vote_date","bill_label","motion_text","vote_cast","yes_count","no_count","passed"])

    elif rtype == "bill_timeline" and bill_pk:
        actions_df = bill_actions_q(bill_pk)
        story.append(Paragraph("Action Timeline", H2))
        _tbl(actions_df, ["action_date","chamber","action_text","vote_type"])

    doc.build(story)
    return buf.getvalue()


# ═══════════════════════════════════════════════════════════════════════════
# PAGE: EXPLORE DB
# ═══════════════════════════════════════════════════════════════════════════

def page_explorer():
    page_header("Explore DB", "Raw schema · table browser · custom SQL", "For power users")

    tables = tbl_names()
    if not tables:
        callout("No tables — is the DB connected?", "danger"); return

    counts    = [{"table": t, "rows": row_count(t)} for t in tables]
    counts_df = pd.DataFrame(counts).sort_values("rows", ascending=False)

    c1, c2 = st.columns([1,2])
    with c1:
        slbl("Tables & Row Counts")
        st.dataframe(counts_df, use_container_width=True, hide_index=True)
    with c2:
        top = counts_df[counts_df["rows"]>0].head(18)
        fig = go.Figure(go.Bar(
            x=top["rows"], y=top["table"], orientation="h",
            marker_color=GOLD, text=top["rows"], textposition="outside", textfont=dict(size=8),
        ))
        fig.update_layout(height=360, **PLOTLY_LAYOUT)
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar":False})

    slbl("Table Inspector")
    chosen_tbl = st.selectbox("Table", tables, key="exp_tbl")
    if chosen_tbl:
        pragma = dbq(f'PRAGMA table_info("{chosen_tbl}")')
        st.dataframe(pragma[["cid","name","type","notnull","pk"]], use_container_width=True, hide_index=True)
        st.caption("Sample (5 rows)")
        st.dataframe(dbq(f'SELECT * FROM "{chosen_tbl}" LIMIT 5'), use_container_width=True, hide_index=True)

    slbl("Custom SQL (read-only)")
    sql_in = st.text_area("SQL", value=f'SELECT * FROM "{chosen_tbl}" LIMIT 20', height=90, key="exp_sql")
    if st.button("Run", key="exp_run"):
        res = dbq(sql_in)
        if res.empty: st.info("No rows.")
        else:
            st.caption(f"{len(res)} rows")
            st.dataframe(res, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════════════
# NAVIGATION & SIDEBAR
# ═══════════════════════════════════════════════════════════════════════════

PAGES = {
    "Command Center":     ("", page_command_center,    "What needs attention now?"),
    "Bill Tracker":       ("", page_bill_tracker,      "Path to passage"),
    "Member Intel":       ("", page_member_intel,      "Voting record & persuadability"),
    "Coalition Builder":  ("", page_coalition_builder, "Allies, targets, bipartisanship"),
    "Whip Count":         ("", page_whip_count,        "Vote margin & scenario modeling"),
    "Language & Lineage": ("", page_lineage,            "Zombie watch & idea tracker"),
    "Field Reports":      ("", page_field_reports,      "Leave-behinds & briefing docs"),
    "Explore DB":         ("", page_explorer,           "Raw data for power users"),
}

with st.sidebar:
    st.markdown(f"""
    <div style="padding:1.1rem 0 1.4rem;border-bottom:1px solid #1c3050;margin-bottom:1.2rem">
      <div style="font-family:'Roboto Mono',monospace;font-size:.48rem;letter-spacing:.24em;
                  text-transform:uppercase;color:{GOLD};margin-bottom:.25rem">Missouri</div>
      <div style="font-family:'DM Serif Display',serif;font-size:1.05rem;
                  color:#e2e8f0;line-height:1.35">Legislature<br>Intelligence</div>
      <div style="font-family:'Roboto Mono',monospace;font-size:.52rem;color:#5a6580;margin-top:.3rem">
        Actionable · Operational
      </div>
    </div>
    """, unsafe_allow_html=True)

    page_list   = list(PAGES.keys())
    override    = st.session_state.pop("_page", None)
    default_idx = page_list.index(override) if override and override in page_list else 0

    selected = st.radio(
        "Navigate", page_list, index=default_idx,
        format_func=lambda k: f"{PAGES[k][0]}  {k}",
        label_visibility="collapsed",
    )

    _, _, question = PAGES[selected]
    st.markdown(f"""
    <div style="margin-top:.5rem;padding:.5rem .4rem;border-top:1px solid #1c3050">
      <div style="font-family:'Roboto Mono',monospace;font-size:.54rem;color:#5a6580;
                  letter-spacing:.08em;text-transform:uppercase;margin-bottom:.15rem">Focus</div>
      <div style="font-family:'DM Serif Display',serif;font-size:.82rem;color:{GOLD};
                  font-style:italic;line-height:1.4">{question}</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(f'<div style="font-family:\'Roboto Mono\',monospace;font-size:.52rem;'
                f'letter-spacing:.12em;text-transform:uppercase;color:{GOLD};margin-bottom:.3rem">'
                f'Session Filter</div>', unsafe_allow_html=True)

    try:
        sess_df   = dbq("SELECT session_id, label FROM sessions ORDER BY year DESC, session_code")
        sess_opts = {"All sessions": None}
        for _, r in sess_df.iterrows():
            lbl = _s(r.get("label")) or _s(r["session_id"])
            sess_opts[lbl] = r["session_id"]
    except Exception:
        sess_opts = {"All sessions": None}

    chosen_sess = st.selectbox("Session", list(sess_opts.keys()),
                               label_visibility="collapsed", key="global_session")
    st.session_state["session_filter"] = sess_opts[chosen_sess]

    st.markdown(f"""
    <div style="position:fixed;bottom:.8rem;left:0;width:220px;padding:0 1rem;
                font-family:'Roboto Mono',monospace;font-size:.47rem;color:#2d4057;line-height:1.8">
      SQLite · Streamlit<br>MO_VOTES_DB → path to mo_votes.db
    </div>
    """, unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════
# RENDER
# ═══════════════════════════════════════════════════════════════════════════
PAGES[selected][1]()