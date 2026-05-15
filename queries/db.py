"""
queries/db.py — SQLite connection pool + raw query helpers.
All query functions live in their own module; this file is infrastructure only.
"""
import sqlite3
import os
import re
from pathlib import Path
from functools import lru_cache

import pandas as pd
import streamlit as st

# ── Path resolution ────────────────────────────────────────────────────────
# Priority: MO_VOTES_DB env var → same dir as this file → cwd
def _resolve_db() -> str:
    env = os.getenv("MO_VOTES_DB")
    if env and Path(env).exists():
        return env
    candidates = [
        Path(__file__).parent.parent / "mo_votes.db",
        Path.cwd() / "mo_votes.db",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # Return the default path even if it doesn't exist yet (show a nice error)
    return str(Path.cwd() / "mo_votes.db")


DB_PATH = _resolve_db()


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    if not Path(DB_PATH).exists():
        st.error(
            f"Database not found at **{DB_PATH}**. "
            "Set the `MO_VOTES_DB` environment variable or place `mo_votes.db` "
            "in the project root."
        )
        st.stop()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA query_only=ON")   # read-only safety
    return conn


def q(sql: str, params=()) -> pd.DataFrame:
    """Execute a SELECT and return a DataFrame."""
    conn = get_conn()
    try:
        return pd.read_sql_query(sql, conn, params=params)
    except Exception as e:
        st.warning(f"Query error: {e}")
        return pd.DataFrame()


def scalar(sql: str, params=()) -> object:
    """Return a single scalar value or None."""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def table_exists(name: str) -> bool:
    r = scalar(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return r is not None


def table_names() -> list[str]:
    df = q("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return df["name"].tolist() if not df.empty else []


def row_count(table: str) -> int:
    n = scalar(f'SELECT COUNT(*) FROM "{table}"')
    return int(n) if n is not None else 0


# ── Bill number normalisation ──────────────────────────────────────────────
_STRIP_SUB_RE = re.compile(
    r"^(?:(?:HCS|SCS|SS|CCS|HS)(?:\s*#\s*\d+)?\s+)+",
    re.IGNORECASE,
)


def norm_bill(bn: str) -> str:
    """Canonical: strip substitute prefix, upper, one space between alpha+digits."""
    if not bn:
        return ""
    s = _STRIP_SUB_RE.sub("", bn.strip()).upper().strip()
    s = re.sub(r"^([A-Z]+)(\d+)$", r"\1 \2", s)
    return re.sub(r"\s+", " ", s).strip()


def bill_nospace(bn: str) -> str:
    return norm_bill(bn).replace(" ", "")


def bill_where(col: str = "bill_number") -> str:
    """SQL WHERE fragment matching a bill column against a normalised (no-space) value."""
    return f"UPPER(REPLACE({col},' ','')) = UPPER(?)"