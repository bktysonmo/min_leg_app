"""
scrapers/journals.py
Parse Missouri Senate and House legislative journals for vote records.

Sources:
  Senate:  senate.mo.gov/{YY}info/pdf-jrnl/DAY{NN}.pdf
  House:   documents.house.mo.gov/billtracking/bills{CODE}/jrnpdf/jrn{NNN}.pdf
  House roll-call sheets: individual PDFs linked from BillContent.aspx

Persistence model:
  All votes (floor AND committee) go into roll_calls with vote_context=
  'floor' or 'committee'.  Per-member votes go into member_votes.
  A companion bill_actions row is written for every roll_call so the
  bill action timeline stays complete — this is handled by insert_floor_vote()
  and insert_committee_vote() in db.py.

House journal extraction uses extract_text_from_bytes_spatial() which
reconstructs lines by Y-coordinate before joining, preventing the column-
scramble that pdfplumber's default reader produces on multi-column name grids.
"""

import io
import re
import time as _time
import logging
from datetime import datetime
from pathlib import Path

import pdfplumber
import requests as _requests

from config.settings import (
    SENATE_BASE, STORAGE, DEFAULT_YEAR, DEFAULT_SESSION,
)
from utils.http import fetch_bytes
from utils.pdf import (
    extract_text_from_bytes,
    extract_text_from_bytes_spatial,
    parse_vote_block,
    find_vote_blocks_in_journal,
    parse_roll_call_vote_sheet,
    _is_roll_call_vote_sheet,
)
from utils.normalize import match_member
from db.db import (
    get_db, queue_for_review, now_utc,
    insert_floor_vote, insert_committee_vote,
    resolve_committee_id, _action_hash,
)

log = logging.getLogger(__name__)

SENATE_JOURNAL_STORAGE = STORAGE  / "journals" / "senate"
HOUSE_JOURNAL_STORAGE  = STORAGE / "journals" / "house"

HOUSE_SESSION_CODES = {
    "2026R": "261", "2025R": "251", "2024R": "241", "2023R": "231",
    "2022R": "221", "2021R": "211", "2020R": "201", "2019R": "191",
}

_HOUSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*;q=0.9",
    "Referer": "https://documents.house.mo.gov/",
}
_RETRY_WAITS = [2, 5, 10]

# Names that appear in vote lists but are never legislators
_NAME_STOPWORDS: frozenset[str] = frozenset({
    "JOURNAL", "SENATE", "HOUSE", "AYES", "NAYS", "NOES", "YEAS",
    "PRESENT", "ABSENT", "TOTAL", "VOTING", "ROLL", "CALL", "PAGE",
    "MOTION", "THE", "OF", "AND", "A", "AN", "IN", "ON", "TO",
    "SPEAKER", "MR. SPEAKER", "MR SPEAKER",
    "PRESIDENT", "PRO", "TEM", "CLERK", "SECRETARY",
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
    "SATURDAY", "SUNDAY",
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
})
_SPEAKER_ALIASES: frozenset[str] = frozenset({"MR. SPEAKER", "MR SPEAKER", "SPEAKER"})

# ── Committee vote block patterns (House journal) ─────────────────────────────
_COMM_HEADER_RE = re.compile(
    r"^Committee\s+on\s+(.+?),\s+(?:Chairman|Vice\s+Chair|Chair)\s+\S+\s+reporting\s*:",
    re.IGNORECASE | re.MULTILINE,
)
_COMM_MOTION_RE = re.compile(
    r"recommends?\s+that\s+it\s+(Do\s+(?:Not\s+)?Pass(?:\s+with\s+[^,\n]+?)?)"
    r"(?:\s+by\s+the\s+following\s+vote)?[:\s]*$",
    re.IGNORECASE | re.MULTILINE,
)
_COMM_NAMELIST_RE = re.compile(
    r"^(Ayes?|Noes?|Absent|Present)\s*\((\d+)\)(?:\s*:\s*(.+))?$",
    re.IGNORECASE | re.MULTILINE,
)
_BILL_LABEL_RE = re.compile(
    r"\b((?:HB|SB|HCR|SCR|HJR|SJR|HR|SR)\s*\d+)\b", re.IGNORECASE
)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _house_get(url: str, timeout: int = 20) -> bytes | None:
    last_err = None
    for i, wait in enumerate(_RETRY_WAITS):
        try:
            r = _requests.get(url, headers=_HOUSE_HEADERS, timeout=timeout)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                return r.content
            if r.status_code == 404:
                return None
            log.debug("_house_get %s → HTTP %d", url[:70], r.status_code)
            return None
        except (_requests.exceptions.Timeout,
                _requests.exceptions.ConnectionError) as e:
            last_err = e
            if i < len(_RETRY_WAITS) - 1:
                _time.sleep(wait)
    log.warning("_house_get failed for %s: %s", url[:70], last_err)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# URL / path helpers
# ─────────────────────────────────────────────────────────────────────────────

def _senate_journal_url(year: int, day_num: int) -> str:
    yy = str(year)[-2:]
    return f"{SENATE_BASE}/{yy}info/pdf-jrnl/DAY{day_num:02d}.pdf"


def _senate_journal_path(year: int, day_num: int) -> Path:
    SENATE_JOURNAL_STORAGE.mkdir(parents=True, exist_ok=True)
    return SENATE_JOURNAL_STORAGE / f"{year}_DAY{day_num:02d}.pdf"


def _house_journal_url(session_code: str, num: int) -> str:
    return (
        f"https://documents.house.mo.gov/billtracking/"
        f"bills{session_code}/jrnpdf/jrn{num:03d}.pdf"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────

def discover_senate_journals(year: int, max_day: int = 90) -> list[dict]:
    found: list[dict] = []
    misses = 0
    for day_num in range(1, max_day + 1):
        url  = _senate_journal_url(year, day_num)
        path = _senate_journal_path(year, day_num)
        if path.exists():
            found.append({"day_num": day_num, "url": url, "path": path})
            misses = 0
            continue
        content = fetch_bytes(url)
        if content:
            path.write_bytes(content)
            found.append({"day_num": day_num, "url": url, "path": path})
            misses = 0
            log.info("Senate journal: DAY%02d (%d bytes)", day_num, len(content))
        else:
            misses += 1
            if misses >= 5:
                break
    log.info("Senate journals for %d: %d found", year, len(found))
    return found


def discover_house_journals(year: int, session: str = "R") -> list[dict]:
    session_id   = f"{year}{session}"
    session_code = HOUSE_SESSION_CODES.get(session_id)
    if not session_code:
        log.error("No session code for %s", session_id)
        return []

    HOUSE_JOURNAL_STORAGE.mkdir(parents=True, exist_ok=True)
    found:  list[dict] = []
    misses: int = 0

    for num in range(1, 200):
        url  = _house_journal_url(session_code, num)
        path = HOUSE_JOURNAL_STORAGE / f"{year}_house_jrn{num:03d}.pdf"
        if path.exists():
            found.append({"num": num, "url": url, "path": path})
            misses = 0
            continue
        content = _house_get(url)
        if content:
            path.write_bytes(content)
            found.append({"num": num, "url": url, "path": path})
            misses = 0
            log.info("House journal: jrn%03d (%d bytes)", num, len(content))
        else:
            misses += 1
            if misses >= 5:
                break

    log.info("House journals for %d: %d found", year, len(found))
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Committee vote block extraction (House journals)
# ─────────────────────────────────────────────────────────────────────────────

def find_committee_vote_blocks(text: str) -> list[dict]:
    """
    Extract structured committee vote records from House journal text.

    Format confirmed from real journals:
      Committee on Fiscal Review, Chairman Murphy reporting:
        ...recommends that it Do Pass by the following vote:
      Ayes (8): Casteel, Cupps, Fogle, Gragg, Hein, Mayhew, Murphy and Pouche
      Noes (0)
      Absent (0)

    Returns list of dicts with keys:
      bill_label, committee_name, motion_text,
      yes_count, no_count, present_count, absent_count,
      passed (1/0/None), ayes, noes, present, absent (list[str])
    """
    results: list[dict] = []
    header_matches = list(_COMM_HEADER_RE.finditer(text))
    if not header_matches:
        return results

    # Slice text between consecutive committee headers
    for i, hm in enumerate(header_matches):
        comm_name  = hm.group(1).strip()
        block_text = text[hm.end(): (
            header_matches[i + 1].start() if i + 1 < len(header_matches) else len(text)
        )]

        for mi, mm in enumerate(list(_COMM_MOTION_RE.finditer(block_text))):
            motion_text = mm.group(1).strip()

            # Bill label: search preamble before this motion
            preamble = block_text[
                (list(_COMM_MOTION_RE.finditer(block_text))[mi - 1].end()
                 if mi > 0 else 0): mm.start()
            ]
            bl = _BILL_LABEL_RE.search(preamble)
            if not bl:
                bl = _BILL_LABEL_RE.search(text[hm.start(): hm.end() + mm.start()])
            bill_label = bl.group(1).strip() if bl else None

            # Vote lines follow the motion line
            motion_matches = list(_COMM_MOTION_RE.finditer(block_text))
            vote_end   = (motion_matches[mi + 1].start()
                          if mi + 1 < len(motion_matches) else len(block_text))
            vote_text  = block_text[mm.end(): vote_end]

            ayes: list[str]    = []
            noes: list[str]    = []
            present: list[str] = []
            absent: list[str]  = []
            yes_count = no_count = present_count = absent_count = 0

            for nl in _COMM_NAMELIST_RE.finditer(vote_text):
                category  = nl.group(1).lower()
                count     = int(nl.group(2))
                names_raw = nl.group(3) or ""
                # Split "Smith, Jones and Brown" → ["Smith", "Jones", "Brown"]
                names = [
                    re.sub(r"\s*\((\d+)\)", r" \1", n.strip().rstrip("."))
                    for part in re.split(r",\s*|\s+and\s+", names_raw)
                    for n in [part.strip()] if n
                ]
                if category in ("aye", "ayes"):
                    ayes, yes_count = names, count
                elif category in ("noe", "noes"):
                    noes, no_count = names, count
                elif category == "present":
                    present, present_count = names, count
                elif category == "absent":
                    absent, absent_count = names, count

            passed: int | None = None
            if yes_count + no_count > 0:
                passed = 1 if yes_count > no_count else 0
            elif re.search(r"\bDo\s+Not\s+Pass\b", motion_text, re.I):
                passed = 0
            elif re.search(r"\bDo\s+Pass\b", motion_text, re.I):
                passed = 1

            results.append({
                "bill_label":     bill_label,
                "committee_name": comm_name,
                "motion_text":    motion_text,
                "yes_count":      yes_count,
                "no_count":       no_count,
                "present_count":  present_count,
                "absent_count":   absent_count,
                "passed":         passed,
                "ayes":           ayes,
                "noes":           noes,
                "present":        present,
                "absent":         absent,
            })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# DB write helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_bill(conn, bill_label: str | None, session_id: str) -> int | None:
    if not bill_label:
        return None
    m = re.match(r"([A-Z]+)\s*(\d+)", bill_label.strip().upper())
    if not m:
        return None
    row = conn.execute("""
        SELECT bill_pk FROM bills
        WHERE session_id=? AND bill_type=? AND bill_number=?
    """, (session_id, m.group(1), int(m.group(2)))).fetchone()
    return row["bill_pk"] if row else None


def _lookup_speaker(conn, session_id: str) -> int | None:
    try:
        row = conn.execute(
            "SELECT speaker_member_id FROM sessions WHERE session_id=?",
            (session_id,)
        ).fetchone()
        return int(row["speaker_member_id"]) if row and row["speaker_member_id"] else None
    except Exception:
        return None


def _insert_named_votes(
    conn,
    roll_call_id: int,
    vote_data: dict,
    chamber: str,
    session_id: str,
) -> int:
    """
    Resolve member names and insert rows into member_votes.
    Returns count of rows inserted.
    """
    inserted = 0
    # District suffix strip for display names that come with parens
    _DIST_SUFFIX = re.compile(r"\s*\(?\s*\d{1,3}\s*\)?\s*$")

    vote_map = {
        "yes":     vote_data.get("ayes", []),
        "no":      vote_data.get("nays", []),
        "present": vote_data.get("present", []),
        "absent":  vote_data.get("absent", []),
    }

    for vote_cast, names in vote_map.items():
        for name_raw in names:
            name_raw = name_raw.strip()
            if not name_raw:
                continue

            name_for_match = _DIST_SUFFIX.sub("", name_raw).strip()

            # Speaker alias
            if name_for_match.upper() in _SPEAKER_ALIASES:
                speaker_id = _lookup_speaker(conn, session_id)
                if speaker_id is None:
                    queue_for_review(
                        conn,
                        source_type="member_match",
                        source_path=f"roll_call_id={roll_call_id}",
                        raw_content=name_raw,
                        error_reason=(
                            f"Speaker alias '{name_for_match}' — "
                            f"sessions.speaker_member_id not set for {session_id}"
                        ),
                    )
                    continue
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO member_votes
                            (roll_call_id, member_id, vote_cast, name_raw, match_confidence)
                        VALUES (?, ?, ?, ?, ?)
                    """, (roll_call_id, speaker_id, vote_cast, name_raw, 1.0))
                    inserted += 1
                except Exception as e:
                    log.error("Speaker vote insert failed: %s", e)
                continue

            if name_for_match.upper() in _NAME_STOPWORDS or len(name_for_match) < 3:
                continue

            member_id, confidence = match_member(conn, name_raw, chamber, session_id)
            if member_id is None:
                queue_for_review(
                    conn,
                    source_type="member_match",
                    source_path=f"roll_call_id={roll_call_id}",
                    raw_content=name_raw,
                    error_reason=f"No match for '{name_for_match}' (conf={confidence:.2f})",
                )
                continue

            try:
                conn.execute("""
                    INSERT OR IGNORE INTO member_votes
                        (roll_call_id, member_id, vote_cast, name_raw, match_confidence)
                    VALUES (?, ?, ?, ?, ?)
                """, (roll_call_id, member_id, vote_cast, name_raw, confidence))
                inserted += 1
            except Exception as e:
                log.error("Vote insert failed for '%s': %s", name_for_match, e)

    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# Senate journal parsing
# ─────────────────────────────────────────────────────────────────────────────

def _extract_journal_date(text: str) -> str | None:
    m = re.search(
        r"(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY),\s+"
        r"([A-Z]+ \d{1,2}, \d{4})",
        text[:2000],
    )
    if m:
        try:
            return datetime.strptime(m.group(1), "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _extract_bill_from_header(header: str) -> str | None:
    m = re.search(r"\b((?:SB|HB|SCR|HCR|SJR|HJR)\s*\d+)\b", header, re.IGNORECASE)
    return m.group(1).strip() if m else None


def parse_senate_journal(journal: dict, session_id: str) -> int:
    path = journal["path"]
    text = extract_text_from_bytes(path.read_bytes())
    if not text:
        log.warning("Empty text from %s", path.name)
        return 0

    journal_date = _extract_journal_date(text)
    blocks       = find_vote_blocks_in_journal(text)
    log.info("  %s: %d vote blocks", path.name, len(blocks))

    saved = 0
    with get_db() as conn:
        for block in blocks:
            vote_data  = parse_vote_block(block["text"], "senate")
            bill_label = block.get("bill_label") or _extract_bill_from_header(block["header"])

            if not vote_data["valid"] and vote_data["parse_confidence"] == "low":
                queue_for_review(
                    conn, "senate_journal_vote", str(path),
                    block["header"][:500], "low-confidence parse",
                )
                continue

            bill_pk    = _resolve_bill(conn, bill_label, session_id)
            vote_date  = journal_date or f"{session_id[:4]}-01-01"
            motion_trunc = block["header"][:500]

            # Check for existing row (idempotent)
            existing = conn.execute("""
                SELECT roll_call_id FROM roll_calls
                WHERE session_id=? AND chamber='senate'
                  AND journal_pdf_path=? AND motion_text=?
                LIMIT 1
            """, (session_id, str(path), motion_trunc)).fetchone()

            if existing:
                _insert_named_votes(conn, existing["roll_call_id"], vote_data, "senate", session_id)
                continue

            roll_call_id = insert_floor_vote(
                conn,
                bill_pk          = bill_pk,
                session_id       = session_id,
                chamber          = "senate",
                vote_date        = vote_date,
                motion_text      = motion_trunc,
                yes_count        = vote_data["yes_count"],
                no_count         = vote_data["no_count"],
                present_count    = vote_data["present_count"],
                absent_count     = vote_data["absent_count"],
                journal_pdf_path = str(path),
                parse_confidence = vote_data["parse_confidence"],
            )
            if roll_call_id:
                _insert_named_votes(conn, roll_call_id, vote_data, "senate", session_id)
                saved += 1

    return saved


# ─────────────────────────────────────────────────────────────────────────────
# House full-journal parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_house_journal(journal: dict, session_id: str) -> int:
    path = journal["path"]
    # Spatial extraction preserves multi-column name grids
    text = extract_text_from_bytes_spatial(path.read_bytes())
    if not text:
        text = extract_text_from_bytes(path.read_bytes())  # fallback
    if not text:
        log.error("House journal extraction failed: %s", path.name)
        return 0

    journal_date = _extract_journal_date(text)
    blocks       = find_vote_blocks_in_journal(text)
    log.info("  %s: %d floor vote blocks", path.name, len(blocks))

    saved = 0
    with get_db() as conn:
        # ── Floor votes ──────────────────────────────────────────────────────
        for block in blocks:
            vote_data  = parse_vote_block(block["text"], "house")
            bill_label = block.get("bill_label") or _extract_bill_from_header(block["header"])

            if not vote_data["valid"] and vote_data["parse_confidence"] == "low":
                queue_for_review(
                    conn, "house_journal_vote", str(path),
                    block["header"][:500], "low-confidence parse",
                )
                continue

            bill_pk      = _resolve_bill(conn, bill_label, session_id)
            vote_date    = journal_date or f"{session_id[:4]}-01-01"
            motion_trunc = block["header"][:500]

            existing = conn.execute("""
                SELECT roll_call_id FROM roll_calls
                WHERE session_id=? AND chamber='house'
                  AND journal_pdf_path=? AND motion_text=?
                LIMIT 1
            """, (session_id, str(path), motion_trunc)).fetchone()

            if existing:
                _insert_named_votes(conn, existing["roll_call_id"], vote_data, "house", session_id)
                continue

            roll_call_id = insert_floor_vote(
                conn,
                bill_pk          = bill_pk,
                session_id       = session_id,
                chamber          = "house",
                vote_date        = vote_date,
                motion_text      = motion_trunc,
                yes_count        = vote_data["yes_count"],
                no_count         = vote_data["no_count"],
                present_count    = vote_data["present_count"],
                absent_count     = vote_data["absent_count"],
                journal_pdf_path = str(path),
                parse_confidence = vote_data["parse_confidence"],
            )
            if roll_call_id:
                _insert_named_votes(conn, roll_call_id, vote_data, "house", session_id)
                saved += 1

        # ── Committee votes ──────────────────────────────────────────────────
        comm_blocks = find_committee_vote_blocks(text)
        cv_saved = cmv_saved = 0

        for cb in comm_blocks:
            bill_pk = _resolve_bill(conn, cb.get("bill_label"), session_id)
            if not bill_pk:
                log.debug("Committee vote: bill %r not in DB", cb.get("bill_label"))
                continue

            cname = cb.get("committee_name")
            cid   = resolve_committee_id(conn, cname, session_id, "house") if cname else None
            vote_date = journal_date or f"{session_id[:4]}-01-01"

            roll_call_id = insert_committee_vote(
                conn,
                bill_pk            = bill_pk,
                session_id         = session_id,
                chamber            = "house",
                vote_date          = vote_date,
                motion_text        = cb["motion_text"],
                yes_count          = cb["yes_count"],
                no_count           = cb["no_count"],
                present_count      = cb.get("present_count", 0),
                absent_count       = cb.get("absent_count", 0),
                passed             = cb["passed"],
                committee_id       = cid,
                committee_name_raw = cname,
                source_url         = str(path),
                parse_confidence   = "high",
            )
            if roll_call_id:
                cv_saved += 1
                # Per-member committee votes
                member_votes_data = {
                    "ayes":    cb.get("ayes", []),
                    "nays":    cb.get("noes", []),
                    "present": cb.get("present", []),
                    "absent":  cb.get("absent", []),
                }
                cmv_saved += _insert_named_votes(
                    conn, roll_call_id, member_votes_data, "house", session_id
                )

        if cv_saved:
            log.info("  %s: %d committee vote(s), %d member vote(s)", path.name, cv_saved, cmv_saved)

    return saved


# ─────────────────────────────────────────────────────────────────────────────
# House individual roll call PDF parsing
# ─────────────────────────────────────────────────────────────────────────────

def _pending_roll_call_pdfs() -> list[dict]:
    """
    Return roll_calls rows that have a PDF URL but no member_votes yet.
    Covers both pdf_parse rows (motion_text starts with URL) and
    xml_summary rows (journal_pdf_path is a URL).
    """
    results: list[dict] = []
    with get_db() as conn:
        rows = conn.execute("""
            SELECT rc.roll_call_id, rc.bill_pk, rc.vote_date,
                   rc.motion_text, rc.journal_pdf_path, rc.parse_confidence
            FROM roll_calls rc
            WHERE rc.chamber = 'house'
              AND rc.vote_context = 'floor'
              AND rc.roll_call_id NOT IN (
                  SELECT DISTINCT roll_call_id FROM member_votes
              )
              AND (
                  (rc.parse_confidence = 'pdf_parse' AND rc.motion_text LIKE 'http%')
                  OR
                  (rc.parse_confidence = 'xml_summary' AND rc.journal_pdf_path LIKE 'http%')
              )
        """).fetchall()

        for row in rows:
            if row["parse_confidence"] == "pdf_parse":
                url = (row["motion_text"] or "").split("|", 1)[0].strip()
            else:
                url = row["journal_pdf_path"] or ""
            if not url.startswith("http"):
                continue
            local = row["journal_pdf_path"] or ""
            results.append({
                "roll_call_id": row["roll_call_id"],
                "pdf_url":      url,
                "vote_date":    row["vote_date"],
                "local_path":   local if local and not local.startswith("http") else None,
            })
    log.info("Pending House roll call PDFs: %d", len(results))
    return results


def parse_house_rollcall_pdf(pdf_bytes: bytes, roll_call_id: int, session_id: str) -> int:
    """
    Parse one House roll call vote sheet and insert member_votes.
    Updates roll_calls with declared totals and high parse_confidence.
    Returns count of member_votes rows inserted.
    """
    text = extract_text_from_bytes(pdf_bytes)
    if not text:
        return 0

    sheet = parse_roll_call_vote_sheet(text)
    if not sheet.is_valid:
        with get_db() as conn:
            queue_for_review(
                conn, "house_rollcall_pdf", f"roll_call_id={roll_call_id}",
                text[:500], "No vote entries parsed from roll call sheet",
            )
        return 0

    vote_data = {
        "ayes":          sheet.yeas(),
        "nays":          sheet.nays(),
        "present":       sheet.present(),
        "absent":        [],
        "yes_count":     sheet.total_yes,
        "no_count":      sheet.total_no,
        "present_count": sheet.total_present,
        "absent_count":  sheet.total_absent,
    }

    with get_db() as conn:
        # Update totals and confidence on the existing roll_calls row
        conn.execute("""
            UPDATE roll_calls SET
                parse_confidence = 'high',
                yes_count        = ?,
                no_count         = ?,
                present_count    = ?,
                absent_count     = ?
            WHERE roll_call_id = ?
        """, (
            sheet.total_yes, sheet.total_no,
            sheet.total_present, sheet.total_absent,
            roll_call_id,
        ))

        # Build a clean motion description from the sheet metadata
        rc_row = conn.execute(
            "SELECT bill_pk, vote_date, motion_text FROM roll_calls WHERE roll_call_id=?",
            (roll_call_id,)
        ).fetchone()

        if rc_row and rc_row["bill_pk"]:
            clean_motion = (
                sheet.read_stage or sheet.bill_title
                or (rc_row["motion_text"] or "").split("|", 1)[-1].strip()
            )[:500]
            if clean_motion and rc_row["vote_date"]:
                h = _action_hash(rc_row["bill_pk"], rc_row["vote_date"], clean_motion)
                conn.execute("""
                    INSERT OR IGNORE INTO bill_actions
                        (bill_pk, action_date, action_text, chamber, action_hash, vote_type)
                    VALUES (?, ?, ?, 'house', ?, 'floor')
                """, (rc_row["bill_pk"], rc_row["vote_date"], clean_motion, h))

        return _insert_named_votes(conn, roll_call_id, vote_data, "house", session_id)


# ─────────────────────────────────────────────────────────────────────────────
# Run functions
# ─────────────────────────────────────────────────────────────────────────────

def run_senate(year: int = DEFAULT_YEAR, session: str = DEFAULT_SESSION):
    log.info("Senate journal scrape: %d/%s", year, session)
    session_id = f"{year}{session}"
    journals   = discover_senate_journals(year)
    if not journals:
        log.warning("No Senate journals found")
        return
    total = sum(parse_senate_journal(j, session_id) for j in journals)
    log.info("Senate journals: %d roll calls saved", total)


def run_house(year: int = DEFAULT_YEAR, session: str = DEFAULT_SESSION):
    log.info("House journal scrape: %d/%s", year, session)
    session_id = f"{year}{session}"

    # Full journals
    journals = discover_house_journals(year, session)
    total_jrn = 0
    for j in journals:
        total_jrn += parse_house_journal(j, session_id)
    log.info("House journals (full): %d roll calls saved", total_jrn)

    # Individual roll call PDFs
    HOUSE_JOURNAL_STORAGE.mkdir(parents=True, exist_ok=True)
    pending  = _pending_roll_call_pdfs()
    total_rc = 0

    for record in pending:
        pdf_url      = record["pdf_url"]
        roll_call_id = record["roll_call_id"]
        safe_name    = re.sub(r"[^\w.]", "_", pdf_url.split("/")[-1])
        cache_path   = HOUSE_JOURNAL_STORAGE / f"{roll_call_id}_{safe_name}"

        # Resolution order: local bill copy → cache → download
        pdf_bytes = None
        if record["local_path"]:
            p = Path(record["local_path"])
            if p.exists():
                pdf_bytes = p.read_bytes()

        if pdf_bytes is None and cache_path.exists():
            pdf_bytes = cache_path.read_bytes()

        if pdf_bytes is None:
            pdf_bytes = _house_get(pdf_url)
            if not pdf_bytes:
                log.warning("Could not download roll call PDF: %s", pdf_url)
                continue
            cache_path.write_bytes(pdf_bytes)

        inserted  = parse_house_rollcall_pdf(pdf_bytes, roll_call_id, session_id)
        total_rc += inserted
        log.debug("  roll_call_id=%d: %d votes inserted", roll_call_id, inserted)

    log.info("House roll call PDFs: %d member votes inserted", total_rc)


def run(
    year: int = DEFAULT_YEAR,
    session: str = DEFAULT_SESSION,
    chamber: str = "both",
):
    if chamber in ("senate", "both"):
        run_senate(year=year, session=session)
    if chamber in ("house", "both"):
        run_house(year=year, session=session)