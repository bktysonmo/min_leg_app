"""
utils/normalize.py
Member name resolution for vote parsing and contributor entity matching.

match_member() is the single entry-point used by all scrapers.
Returns (member_id: int | None, confidence: float).

Matching strategy (stops at first success):

  Step 0 — Split trailing district suffix ("Brown 27" → name="Brown", district=27)
  Step 1 — If district present: exact (chamber, UPPER(last_name), district) lookup → 1.0
  Step 2 — Exact lookup in member_name_variants (cache of prior matches)   → 1.0
  Step 3 — Exact UPPER(last_name) match in members table                   → 1.0
             If exactly one result, cache and return.
             If multiple, try first-name initial disambiguation before falling through.
  Step 4 — Prefix match (last_name LIKE 'QUERY%') for truncated names      → 0.92
  Step 5 — Rapidfuzz WRatio fuzzy match on last_name ≥ threshold           → score
  Step 6 — Rapidfuzz WRatio fuzzy match on full_name ≥ threshold           → score
  Step 7 — No match → queue_for_review via caller

Confidence is always 0.0–1.0 (stored in member_votes.match_confidence).
The old code used 0–100 range from rapidfuzz; this file normalizes to 0–1.

Successful matches (steps 1–6) are cached in member_name_variants so subsequent
calls for the same name skip to step 2.
"""

import re
import sqlite3
import logging

log = logging.getLogger(__name__)

try:
    from rapidfuzz import process as _rf_process, fuzz as _rf_fuzz
    _HAS_RAPIDFUZZ = True
except ImportError:
    _HAS_RAPIDFUZZ = False
    log.warning("rapidfuzz not installed — fuzzy name matching disabled")

# Minimum fuzzy score (0–1) to accept a match
_FUZZY_THRESHOLD = 0.82
# Minimum margin over the runner-up to avoid ambiguous matches
_FUZZY_MARGIN    = 0.05

# Trailing district suffix: "Brown 27" or "Brown (27)"
_DISTRICT_RE = re.compile(r"^(.*\S)\s+\(?(\d{1,3})\)?$")

# Honorifics/suffixes to strip before matching
_STRIP_TOKENS = frozenset({
    "SENATOR", "REPRESENTATIVE", "REP", "SEN", "HON",
    "MR", "MRS", "MS", "DR", "JR", "SR", "II", "III", "IV",
})


def _split_district(raw: str) -> tuple[str, int | None]:
    """
    Split "Brown 27" → ("Brown", 27).
    Returns (raw, None) when no trailing 1-3 digit integer is present.
    Does NOT split "Brown 1623" (4+ digits are bill numbers, not districts).
    """
    m = _DISTRICT_RE.match(raw.strip())
    if m:
        return m.group(1).strip(), int(m.group(2))
    return raw.strip(), None


def _norm(s: str) -> str:
    """Normalize for case-insensitive comparison: uppercase, collapse whitespace."""
    return re.sub(r"\s+", " ", s.upper().strip())


def _strip_honorifics(s: str) -> str:
    parts = [w for w in _norm(s).split() if w not in _STRIP_TOKENS]
    return " ".join(parts)


def _last_name_from_norm(norm: str) -> str:
    """
    Extract the most-likely last name token from a normalized name string.
    "JOHN SMITH" → "SMITH"
    "SMITH, JOHN" → "SMITH"
    "SMITH" → "SMITH"
    """
    if "," in norm:
        return norm.split(",")[0].strip()
    parts = norm.split()
    return parts[-1] if parts else norm


def _first_initial(norm: str) -> str:
    """Return first name initial or '' if none extractable."""
    if "," in norm:
        rest = norm.split(",", 1)[1].strip()
        return rest[0] if rest else ""
    parts = norm.split()
    return parts[0][0] if len(parts) > 1 else ""


def _cache_variant(conn: sqlite3.Connection, member_id: int, variant: str) -> None:
    try:
        conn.execute("""
            INSERT OR IGNORE INTO member_name_variants (member_id, name_variant, source)
            VALUES (?, ?, 'journal')
        """, (member_id, variant))
    except sqlite3.Error:
        pass


def match_member(
    conn: sqlite3.Connection,
    raw_name: str,
    chamber: str,
    session_id: str,
    min_confidence: float = _FUZZY_THRESHOLD,
) -> tuple[int | None, float]:
    """
    Resolve a raw name string to (member_id, confidence ∈ 0.0–1.0).
    Returns (None, 0.0) when no match meets min_confidence.
    """
    if not raw_name or not raw_name.strip():
        return None, 0.0

    # ── Step 0: split district suffix ──────────────────────────────────────
    name_part, district = _split_district(raw_name.strip())
    cleaned = _strip_honorifics(name_part)
    if not cleaned:
        return None, 0.0

    query_last  = _last_name_from_norm(cleaned)
    query_first = _first_initial(cleaned)

    # ── Step 1: district-qualified exact lookup ─────────────────────────────
    if district is not None:
        row = conn.execute("""
            SELECT member_id FROM members
            WHERE chamber = ? AND UPPER(last_name) = ? AND district = ? AND active = 1
            LIMIT 1
        """, (chamber, query_last, district)).fetchone()
        if row:
            mid = row["member_id"]
            _cache_variant(conn, mid, _norm(raw_name))
            _cache_variant(conn, mid, query_last)
            return mid, 1.0
        # District miss → fall through to fuzzy on name portion only

    # ── Step 2: variant cache lookup ────────────────────────────────────────
    for lookup in {_norm(raw_name), cleaned, query_last}:
        row = conn.execute("""
            SELECT m.member_id FROM member_name_variants v
            JOIN members m ON m.member_id = v.member_id
            WHERE v.name_variant = ? AND m.chamber = ? AND m.active = 1
            LIMIT 1
        """, (lookup, chamber)).fetchone()
        if row:
            return row["member_id"], 1.0

    # ── Step 3: exact last_name match ────────────────────────────────────────
    rows = conn.execute("""
        SELECT member_id, first_name FROM members
        WHERE chamber = ? AND UPPER(last_name) = ? AND active = 1
    """, (chamber, query_last)).fetchall()

    if len(rows) == 1:
        mid = rows[0]["member_id"]
        _cache_variant(conn, mid, query_last)
        return mid, 1.0

    if len(rows) > 1 and query_first:
        # Disambiguate by first initial
        hits = [r for r in rows if (r["first_name"] or "").upper().startswith(query_first)]
        if len(hits) == 1:
            mid = hits[0]["member_id"]
            _cache_variant(conn, mid, cleaned)
            return mid, 0.98

    # ── Step 4: prefix match (handles OCR truncation) ───────────────────────
    if len(query_last) >= 4:
        rows_prefix = conn.execute("""
            SELECT member_id, last_name FROM members
            WHERE chamber = ? AND UPPER(last_name) LIKE ? AND active = 1
        """, (chamber, f"{query_last[:5]}%")).fetchall()
        if len(rows_prefix) == 1:
            mid = rows_prefix[0]["member_id"]
            _cache_variant(conn, mid, query_last)
            return mid, 0.92

    if not _HAS_RAPIDFUZZ:
        log.warning("match_member: no match for '%s' (rapidfuzz unavailable)", raw_name)
        return None, 0.0

    # ── Load all active members for fuzzy matching ───────────────────────────
    members = conn.execute("""
        SELECT member_id, last_name, full_name
        FROM members WHERE chamber = ? AND active = 1
    """, (chamber,)).fetchall()

    if not members:
        return None, 0.0

    def _fuzzy(query: str, name_map: dict[int, str]) -> tuple[int | None, float]:
        results = _rf_process.extract(
            query, name_map, scorer=_rf_fuzz.WRatio, limit=2
        )
        if not results:
            return None, 0.0
        # rapidfuzz dict mode: (value, score, key) where key=member_id
        _val, score, best_id = results[0]
        score_norm = score / 100.0
        runner_up  = results[1][1] / 100.0 if len(results) > 1 else 0.0
        if score_norm >= min_confidence and (score_norm - runner_up) >= _FUZZY_MARGIN:
            return best_id, score_norm
        return None, score_norm

    # ── Step 5: fuzzy on last_name ───────────────────────────────────────────
    last_map = {r["member_id"]: (r["last_name"] or "").upper() for r in members}
    mid, conf = _fuzzy(query_last, last_map)
    if mid is not None:
        _cache_variant(conn, mid, query_last)
        return mid, conf

    # ── Step 6: fuzzy on full_name ───────────────────────────────────────────
    full_map = {r["member_id"]: (r["full_name"] or r["last_name"] or "").upper() for r in members}
    mid, conf = _fuzzy(cleaned, full_map)
    if mid is not None:
        _cache_variant(conn, mid, cleaned)
        return mid, conf

    # ── No match ─────────────────────────────────────────────────────────────
    best_score = conf  # last attempted score
    log.warning(
        "match_member: no match for '%s' (last='%s' district=%s chamber=%s best=%.2f)",
        raw_name, query_last, district, chamber, best_score,
    )
    return None, best_score


# ─────────────────────────────────────────────────────────────────────────────
# CAMPAIGN FINANCE ENTITY MATCHING
# ─────────────────────────────────────────────────────────────────────────────

def normalize_entity_name(raw: str) -> str:
    s = raw.upper().strip()
    s = re.sub(r"\b(LLC|INC|CORP|CO|LTD|PAC|P\.A\.C\.)\b\.?", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def find_canonical_entity(
    conn: sqlite3.Connection,
    raw_name: str,
    threshold: float = 0.88,
) -> int | None:
    """Match a contributor name to a canonical_entity. Returns entity_id or None."""
    normalized = normalize_entity_name(raw_name)

    row = conn.execute(
        "SELECT entity_id FROM canonical_entities WHERE canonical_name = ?",
        (normalized,)
    ).fetchone()
    if row:
        return row["entity_id"]

    if not _HAS_RAPIDFUZZ:
        return None

    entities = conn.execute(
        "SELECT entity_id, canonical_name FROM canonical_entities"
    ).fetchall()
    if not entities:
        return None

    candidates = {e["entity_id"]: e["canonical_name"] for e in entities}
    result = _rf_process.extractOne(normalized, candidates, scorer=_rf_fuzz.WRatio)
    if result:
        _val, score, entity_id = result
        if score / 100.0 >= threshold:
            return entity_id

    return None