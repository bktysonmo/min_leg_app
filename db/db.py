"""
db/db.py
SQLite connection helper — WAL mode, context managers, upsert helpers.

Key design points:
  · Committee votes are stored in roll_calls with vote_context='committee',
    not in a separate committee_votes table. member_votes covers both.
  · _action_hash uses a canonical 16-char SHA-1 for dedup across all scrapers.
  · All public helpers accept a live connection and do NOT commit — callers
    use the get_db() context manager to control transaction boundaries.
"""

import sqlite3
import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config.settings import DB_PATH


# ── Connection ────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous  = NORMAL")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Utilities ─────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _action_hash(bill_pk, action_date: str, action_text: str) -> str:
    """
    Canonical 16-char SHA-1 action deduplication key.
    All code paths writing to bill_actions must use this function so the
    unique index on action_hash reliably deduplicates across scrapers.
    action_text must be pre-truncated to 500 chars.
    """
    raw = f"{bill_pk if bill_pk is not None else 'NULL'}|{action_date}|{action_text}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ── Upsert helpers ────────────────────────────────────────────────────────────

def upsert_member(conn: sqlite3.Connection, member: dict) -> int:
    conn.execute("""
        INSERT INTO members
            (member_id, chamber, first_name, last_name, full_name,
             party, district, county_list, first_elected, phone, email,
             photo_url, active, last_scraped)
        VALUES
            (:member_id, :chamber, :first_name, :last_name, :full_name,
             :party, :district, :county_list, :first_elected, :phone, :email,
             :photo_url, 1, :last_scraped)
        ON CONFLICT(member_id) DO UPDATE SET
            full_name    = excluded.full_name,
            party        = excluded.party,
            district     = excluded.district,
            county_list  = excluded.county_list,
            phone        = excluded.phone,
            email        = excluded.email,
            photo_url    = excluded.photo_url,
            last_scraped = excluded.last_scraped
    """, member)
    return member["member_id"]


def upsert_bill(conn: sqlite3.Connection, bill: dict) -> int:
    cur = conn.execute("""
        INSERT INTO bills
            (bill_id, session_id, chamber, bill_type, bill_number, bill_label,
             lr_number, title, short_desc, introduced_date, effective_date,
             current_status, last_action_date, last_scraped, source_url)
        VALUES
            (:bill_id, :session_id, :chamber, :bill_type, :bill_number, :bill_label,
             :lr_number, :title, :short_desc, :introduced_date, :effective_date,
             :current_status, :last_action_date, :last_scraped, :source_url)
        ON CONFLICT(session_id, chamber, bill_type, bill_number) DO UPDATE SET
            bill_id          = excluded.bill_id,
            lr_number        = excluded.lr_number,
            title            = excluded.title,
            short_desc       = excluded.short_desc,
            current_status   = excluded.current_status,
            last_action_date = excluded.last_action_date,
            last_scraped     = excluded.last_scraped
        RETURNING bill_pk
    """, bill)
    row = cur.fetchone()
    return row["bill_pk"]


def insert_action(
    conn: sqlite3.Connection,
    action: dict,
    vote_type: str | None = None,
) -> tuple[bool, int | None]:
    """
    Insert one row into bill_actions.

    Returns (inserted: bool, action_id: int | None).
      inserted=True  → new row written
      inserted=False → duplicate (existing row returned)
    vote_type: 'floor' | 'committee' | 'procedural' | None
    """
    action_text = (action.get("action_text") or "")[:500]
    h = _action_hash(action["bill_pk"], action.get("action_date", ""), action_text)
    try:
        cur = conn.execute("""
            INSERT INTO bill_actions
                (bill_pk, action_date, action_text, chamber,
                 journal_page, action_hash, vote_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            RETURNING action_id
        """, (
            action["bill_pk"],
            action.get("action_date"),
            action_text,
            action.get("chamber"),
            action.get("journal_page"),
            h,
            vote_type,
        ))
        row = cur.fetchone()
        return True, row["action_id"] if row else None
    except sqlite3.IntegrityError:
        row = conn.execute(
            "SELECT action_id FROM bill_actions WHERE action_hash = ?", (h,)
        ).fetchone()
        return False, row["action_id"] if row else None


def insert_roll_call(
    conn: sqlite3.Connection,
    *,
    bill_pk: int,
    session_id: str,
    chamber: str,
    vote_date: str,
    motion_text: str,
    yes_count: int,
    no_count: int,
    present_count: int = 0,
    absent_count: int = 0,
    passed: int | None = None,
    # Floor-specific
    reading_stage: str | None = None,
    journal_page: str | None = None,
    journal_pdf_path: str | None = None,
    # Committee-specific
    vote_context: str = "floor",
    committee_id: int | None = None,
    committee_name_raw: str | None = None,
    # Common
    source_url: str | None = None,
    parse_confidence: str = "medium",
    member_votes: list[dict] | None = None,
) -> int | None:
    """
    Insert one roll_calls row (floor or committee) plus optional per-member
    votes, and a companion bill_actions row so the action timeline stays
    complete.

    vote_context: 'floor' | 'committee'

    member_votes: list of dicts with keys:
        member_id        int   (required)
        vote_cast        str   yes/no/present/absent/NV
        name_raw         str   original source string (audit)
        match_confidence float 0–1

    Returns roll_call_id or None on failure.
    """
    if vote_context not in ("floor", "committee"):
        raise ValueError(f"vote_context must be 'floor' or 'committee', got {vote_context!r}")

    # Infer passed from vote counts when not explicitly provided
    if passed is None and (yes_count + no_count) > 0:
        passed = 1 if yes_count > no_count else 0

    motion_trunc = (motion_text or "")[:500]

    # ── Companion bill_actions row keeps the action timeline complete
    action_label = motion_trunc or (
        f"Committee vote: {committee_name_raw}" if committee_name_raw else "Committee vote"
        if vote_context == "committee" else "Floor vote"
    )
    _, action_id = insert_action(conn, {
        "bill_pk":      bill_pk,
        "action_date":  vote_date,
        "action_text":  action_label,
        "chamber":      chamber,
        "journal_page": journal_page,
    }, vote_type=vote_context)

    # ── Insert roll_calls row
    cur = conn.execute("""
        INSERT OR IGNORE INTO roll_calls
            (bill_pk, session_id, chamber, vote_date, vote_context,
             committee_id, committee_name_raw,
             reading_stage, motion_text,
             yes_count, no_count, present_count, absent_count, total_counted,
             passed, action_id,
             journal_page, journal_pdf_path, source_url,
             parse_confidence, parsed_at)
        VALUES (?, ?, ?, ?, ?,  ?, ?,  ?, ?,  ?, ?, ?, ?, ?,  ?, ?,  ?, ?, ?,  ?, ?)
    """, (
        bill_pk, session_id, chamber, vote_date, vote_context,
        committee_id, committee_name_raw,
        reading_stage, motion_trunc,
        yes_count, no_count, present_count, absent_count,
        yes_count + no_count + present_count + absent_count,
        passed, action_id,
        journal_page, journal_pdf_path, source_url,
        parse_confidence, now_utc(),
    ))

    if cur.lastrowid:
        roll_call_id = cur.lastrowid
    else:
        # Duplicate — look it up
        row = conn.execute("""
            SELECT roll_call_id FROM roll_calls
            WHERE bill_pk = ? AND vote_date = ? AND motion_text = ? AND vote_context = ?
            LIMIT 1
        """, (bill_pk, vote_date, motion_trunc, vote_context)).fetchone()
        if not row:
            return None
        roll_call_id = row["roll_call_id"]

    # ── Per-member votes
    for mv in (member_votes or []):
        mid = mv.get("member_id")
        if not mid:
            continue
        try:
            conn.execute("""
                INSERT OR IGNORE INTO member_votes
                    (roll_call_id, member_id, vote_cast, name_raw, match_confidence)
                VALUES (?, ?, ?, ?, ?)
            """, (
                roll_call_id,
                mid,
                mv.get("vote_cast", "NV"),
                mv.get("name_raw"),
                mv.get("match_confidence"),
            ))
        except sqlite3.IntegrityError:
            pass

    return roll_call_id


# Convenience aliases for call-site clarity
def insert_floor_vote(conn, **kwargs) -> int | None:
    return insert_roll_call(conn, vote_context="floor", **kwargs)


def insert_committee_vote(conn, **kwargs) -> int | None:
    return insert_roll_call(conn, vote_context="committee", **kwargs)


# ── Lookup helpers ────────────────────────────────────────────────────────────

def get_bill_pk(
    conn: sqlite3.Connection,
    session_id: str,
    chamber: str,
    bill_type: str,
    bill_number: int,
) -> int | None:
    row = conn.execute("""
        SELECT bill_pk FROM bills
        WHERE session_id=? AND chamber=? AND bill_type=? AND bill_number=?
    """, (session_id, chamber, bill_type, bill_number)).fetchone()
    return row["bill_pk"] if row else None


def get_member_by_id(
    conn: sqlite3.Connection, member_id: int
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM members WHERE member_id=?", (member_id,)
    ).fetchone()


def resolve_committee_id(
    conn: sqlite3.Connection,
    name: str,
    session_id: str,
    chamber: str,
) -> int | None:
    """
    Resolve committee_id by name for a given session and chamber.
    Tries exact match first, then case-insensitive LIKE.
    Returns None if unresolvable — callers should store committee_name_raw.
    """
    row = conn.execute("""
        SELECT committee_id FROM committees
        WHERE session_id=? AND chamber=? AND name=?
        LIMIT 1
    """, (session_id, chamber, name)).fetchone()
    if row:
        return row["committee_id"]
    row = conn.execute("""
        SELECT committee_id FROM committees
        WHERE session_id=? AND chamber=?
          AND LOWER(name) LIKE LOWER(?)
        LIMIT 1
    """, (session_id, chamber, f"%{name}%")).fetchone()
    return row["committee_id"] if row else None


# ── Audit helpers ─────────────────────────────────────────────────────────────

def log_change(
    conn: sqlite3.Connection,
    event_type: str,
    entity_type: str,
    entity_pk: str,
    field: str,
    old_val,
    new_val,
    scraper: str = "",
    source_url: str = "",
):
    if old_val == new_val:
        return
    conn.execute("""
        INSERT INTO change_events
            (event_type, entity_type, entity_pk, field_changed,
             old_value, new_value, detected_at, scraper, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_type, entity_type, str(entity_pk), field,
        str(old_val) if old_val is not None else None,
        str(new_val) if new_val is not None else None,
        now_utc(), scraper, source_url,
    ))


def queue_for_review(
    conn: sqlite3.Connection,
    source_type: str,
    source_path: str,
    raw_content: str,
    error_reason: str,
):
    conn.execute("""
        INSERT INTO parse_review_queue
            (source_type, source_path, raw_content, error_reason, detected_at)
        VALUES (?, ?, ?, ?, ?)
    """, (source_type, source_path, raw_content, error_reason, now_utc()))