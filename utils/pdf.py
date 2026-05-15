"""
utils/pdf.py
PDF text extraction and structured vote parsing for Missouri legislative documents.

Three distinct source formats handled:

  1. Senate journal (DAY##.pdf)
       Headers: YEAS—Senators / NAYS—Senators / Absent—Senators / Absent with leave—Senators
       Counts:  trailing "—N" at end of each name block
       Names:   "Brown (26)" — district in parens, stripped before matching
       End:     "Vacancies—None"

  2. House journal (jrn###.pdf)
       Headers: AYES: NNN / NOES: NNN / PRESENT: NNN / ABSENT WITH LEAVE: NNN
       Counts:  embedded in header (authoritative)
       Names:   "Brown 27" — district as bare suffix, KEPT for match_member disambiguation
       Two-word last names: "Fountain Henderson", "Walsh Moore", "Van Schoiack"
       End:     "VACANCIES: N"

  3. House roll call vote sheet (individual PDFs from BillContent.aspx)
       Layout:  multi-column "Y - ALLEN  N - DURNELL  @ - LEWIS" grid
       Counts:  footer "Total Yes : N  Total No : N  Total Present : N  Total Absent : N"
       Vote codes: Y (yes), N (no), @ (present/abstain), P (present, some sheets)

Key improvements over prior version:
  - extract_text_from_bytes_spatial(): spatially-aware House journal extraction
    that buckets words by Y-coordinate before joining, preventing column-scramble
  - Senate name parser collapses multi-line artifacts before splitting names
  - House name parser correctly handles three-token "Van Schoiack 6" names and
    passes declared counts through separately from parsed name lists
  - parse_vote_block() now returns declared_counts dict so callers store the
    authoritative source total even when name-list parsing diverges
  - Roll call sheet regex extended for 'P' vote code and tighter name boundary
  - find_vote_blocks_in_journal(): window 3000→4500 chars, look-back 1000→1500
"""

import io
import re
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from config.settings import SENATE_SIZE, HOUSE_SIZE

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# TEXT EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_path: Path) -> str:
    try:
        with pdfplumber.open(pdf_path) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as e:
        log.error("PDF text extraction failed: %s — %s", pdf_path, e)
        return ""


def extract_text_from_bytes(pdf_bytes: bytes) -> str:
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as e:
        log.error("PDF bytes extraction failed — %s", e)
        return ""


def extract_text_from_bytes_spatial(pdf_bytes: bytes) -> str:
    """
    Spatially-aware extraction for House journals.

    pdfplumber's default text extraction merges columns in document order,
    which scrambles the multi-column name grids in House vote blocks. This
    method reconstructs lines by Y-coordinate (4pt buckets) so names in the
    same visual row appear on the same line regardless of column position.
    Used by scrapers/journals.py for House journal parsing.
    """
    try:
        pages: list[str] = []
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                words = page.extract_words(x_tolerance=2, y_tolerance=4)
                if not words:
                    continue
                rows: dict[int, list] = {}
                for w in words:
                    y_key = round(w["top"] / 4) * 4
                    rows.setdefault(y_key, []).append(w)
                line_strs = []
                for y_key in sorted(rows):
                    row_words = sorted(rows[y_key], key=lambda w: w["x0"])
                    parts = [row_words[0]["text"]]
                    for prev, cur in zip(row_words, row_words[1:]):
                        # gap > 12pt treated as column separator → two spaces
                        parts.append(("  " if cur["x0"] - prev["x1"] > 12 else " ") + cur["text"])
                    line_strs.append("".join(parts))
                pages.append("\n".join(line_strs))
        return "\n".join(pages)
    except Exception as e:
        log.error("Spatial PDF extraction failed — %s", e)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# SENATE VOTE BLOCK PARSING
# ─────────────────────────────────────────────────────────────────────────────

_S_YEAS_PAT = re.compile(
    r"YEAS[—\-]+Senators?\s*\n(.*?)(?=\nNAYS[—\-]|\nAbsent[—\-]|\nVacancies|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_S_NAYS_PAT = re.compile(
    r"NAYS[—\-]+Senators?\s*\n(.*?)(?=\nAbsent[—\-]|\nVacancies|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_S_ABSENT_PAT = re.compile(
    r"(?:^|\n)Absent[—\-]+Senators?\s*\n(.*?)(?=\nAbsent\s+with|\nVacancies|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_S_ABSENT_LEAVE_PAT = re.compile(
    r"Absent\s+with\s+leave[—\-]+Senators?\s*\n(.*?)(?=\nVacancies|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_S_TRAILING_COUNT = re.compile(r"[—\-](\d+)\s*$", re.MULTILINE)
_S_BLOCK_END      = re.compile(r"Vacancies[—\-]+None", re.IGNORECASE)
_S_DISTRICT_PAREN = re.compile(r"\s*\(\d+\)")  # "(26)" disambiguation suffix


def _parse_senate_section(raw: str) -> list[str]:
    """Parse senator last names from one section of a Senate vote block."""
    if not raw:
        return []
    raw = _S_TRAILING_COUNT.sub("", raw).strip()
    if not raw or raw.strip().lower() == "none":
        return []
    raw = _S_DISTRICT_PAREN.sub("", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    # Title-case capitalized names; handles O'Laughlin, Luetkemeyer, McCreery
    return [n for n in re.findall(r"[A-Z][a-zA-Z'\-]+", raw) if len(n) > 1]


def _parse_vote_block_senate(text: str) -> dict:
    result = {
        "ayes": [], "nays": [], "present": [], "absent": [],
        "yes_count": 0, "no_count": 0, "present_count": 0, "absent_count": 0,
        "declared_counts": {},
        "valid": False, "parse_confidence": "low",
    }

    m_y  = _S_YEAS_PAT.search(text)
    m_n  = _S_NAYS_PAT.search(text)
    m_a  = _S_ABSENT_PAT.search(text)
    m_al = _S_ABSENT_LEAVE_PAT.search(text)

    if m_y:  result["ayes"]   = _parse_senate_section(m_y.group(1))
    if m_n:  result["nays"]   = _parse_senate_section(m_n.group(1))
    absent = []
    if m_a:  absent += _parse_senate_section(m_a.group(1))
    if m_al: absent += _parse_senate_section(m_al.group(1))
    result["absent"] = absent

    # Extract declared trailing counts
    declared: dict[str, int] = {}
    for blob, key in [(m_y, "yes"), (m_n, "no"), (m_a, "absent")]:
        if blob:
            cm = _S_TRAILING_COUNT.search(blob.group(0))
            if cm:
                declared[key] = int(cm.group(1))
    result["declared_counts"] = declared

    result["yes_count"]    = declared.get("yes",    len(result["ayes"]))
    result["no_count"]     = declared.get("no",     len(result["nays"]))
    result["absent_count"] = declared.get("absent", len(result["absent"]))

    total_parsed = sum(len(result[k]) for k in ("ayes", "nays", "absent"))
    declared_yes = declared.get("yes")

    if declared_yes is not None and declared_yes == len(result["ayes"]):
        result["parse_confidence"] = "high"
        result["valid"] = True
    elif m_y and 0 < total_parsed <= SENATE_SIZE:
        result["parse_confidence"] = "medium"
        result["valid"] = True

    return result


# ─────────────────────────────────────────────────────────────────────────────
# HOUSE JOURNAL VOTE BLOCK PARSING
# ─────────────────────────────────────────────────────────────────────────────

_H_AYES_PAT = re.compile(
    r"AYES:\s*(\d+)\s*\n(.*?)(?=\nNOES:|\nPRESENT:|\nABSENT|\nVACANCIES:|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_H_NOES_PAT = re.compile(
    r"NOES:\s*(\d+)\s*\n(.*?)(?=\nPRESENT:|\nABSENT|\nVACANCIES:|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_H_PRESENT_PAT = re.compile(
    r"PRESENT:\s*(\d+)\s*\n(.*?)(?=\nABSENT|\nVACANCIES:|\nAYES:|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_H_ABSENT_PAT = re.compile(
    r"ABSENT(?:\s+WITH\s+LEAVE)?:\s*(\d+)\s*\n(.*?)(?=\nVACANCIES:|\nAYES:|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_H_BLOCK_END     = re.compile(r"VACANCIES:\s*\d+", re.IGNORECASE)
_H_SECTION_PROBE = re.compile(r"AYES:\s*\d+", re.IGNORECASE)
_S_SECTION_PROBE = re.compile(r"YEAS[—\-]+Senators?", re.IGNORECASE)

_H_SECTION_KW = frozenset({
    "AYES", "NOES", "NAYS", "YEAS", "PRESENT", "ABSENT",
    "WITH", "LEAVE", "VACANCIES",
})
_H_CALENDAR = frozenset({
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
})
_H_EXCLUDED = frozenset({"Mr. Speaker"})
# Two-word-last-name starter tokens — extend from DB if needed
_H_TWO_WORD_STARTS = frozenset({"Fountain", "Walsh", "Van"})


def _parse_house_section(
    raw: str,
    two_word_starts: frozenset[str] = _H_TWO_WORD_STARTS,
) -> list[str]:
    """
    Parse member names from one section of a House journal vote block.

    District suffix is PRESERVED in the returned name ("Brown 27") so
    normalize.match_member can use it for same-last-name disambiguation.
    Two-word last names and three-token "Van Schoiack 6" forms are handled.
    """
    text = re.sub(r"[ \t]+", " ", raw).strip()
    text = re.sub(r"\b\d{4}\b", "", text)   # strip 4-digit year artifacts

    tokens = [t for t in text.split() if t]
    names: list[str] = []
    i = 0

    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        nxt2 = tokens[i + 2] if i + 2 < len(tokens) else ""

        # "Mr. Speaker" — two-token special case
        if tok == "Mr." and nxt == "Speaker":
            names.append("Mr. Speaker")
            i += 2
            continue

        if not tok or not tok[0].isupper():
            i += 1
            continue

        if tok.upper() in _H_SECTION_KW or tok.endswith(":"):
            i += 1
            continue

        if tok.rstrip(",") in _H_CALENDAR:
            i += 1
            continue

        if "-" in tok or "\u2013" in tok:
            i += 1
            continue

        if tok in ("Day", "Journal", "Page"):
            i += 1
            continue

        # Two-word last name: "Fountain Henderson" or "Van Schoiack 6"
        if tok in two_word_starts and nxt and nxt[0].isupper():
            if re.fullmatch(r"\d{1,3}", nxt2):
                names.append(f"{tok} {nxt} {nxt2}")
                i += 3
            else:
                names.append(f"{tok} {nxt}")
                i += 2
            continue

        # Trailing district number: "Brown 27"
        if re.fullmatch(r"\d{1,3}", nxt):
            names.append(f"{tok} {nxt}")
            i += 2
            continue

        names.append(tok)
        i += 1

    return [n for n in names if n not in _H_EXCLUDED]


def _parse_vote_block_house(text: str) -> dict:
    result = {
        "ayes": [], "nays": [], "present": [], "absent": [],
        "yes_count": 0, "no_count": 0, "present_count": 0, "absent_count": 0,
        "declared_counts": {},
        "valid": False, "parse_confidence": "low",
    }

    m_a = _H_AYES_PAT.search(text)
    m_n = _H_NOES_PAT.search(text)
    m_p = _H_PRESENT_PAT.search(text)
    m_ab = _H_ABSENT_PAT.search(text)

    declared = {
        "yes":     int(m_a.group(1))  if m_a  else None,
        "no":      int(m_n.group(1))  if m_n  else None,
        "present": int(m_p.group(1))  if m_p  else None,
        "absent":  int(m_ab.group(1)) if m_ab else None,
    }
    result["declared_counts"] = {k: v for k, v in declared.items() if v is not None}

    if m_a:  result["ayes"]    = _parse_house_section(m_a.group(2))
    if m_n:  result["nays"]    = _parse_house_section(m_n.group(2))
    if m_p:  result["present"] = _parse_house_section(m_p.group(2))
    if m_ab: result["absent"]  = _parse_house_section(m_ab.group(2))

    # Declared counts are authoritative for the totals stored in roll_calls
    result["yes_count"]     = declared["yes"]     if declared["yes"]     is not None else len(result["ayes"])
    result["no_count"]      = declared["no"]      if declared["no"]      is not None else len(result["nays"])
    result["present_count"] = declared["present"] if declared["present"] is not None else len(result["present"])
    result["absent_count"]  = declared["absent"]  if declared["absent"]  is not None else len(result["absent"])

    total_parsed = sum(len(result[k]) for k in ("ayes", "nays", "present", "absent"))

    if declared["yes"] is not None:
        result["valid"] = True
        # ±1 tolerance for Mr. Speaker exclusion
        result["parse_confidence"] = "high" if abs(len(result["ayes"]) - declared["yes"]) <= 1 else "medium"
    elif m_a and 0 < total_parsed <= HOUSE_SIZE:
        result["valid"] = True
        result["parse_confidence"] = "medium"

    return result


def parse_vote_block(text: str, chamber: str = "senate") -> dict:
    """Dispatch to chamber-specific vote block parser."""
    return _parse_vote_block_house(text) if chamber == "house" else _parse_vote_block_senate(text)


# ─────────────────────────────────────────────────────────────────────────────
# HOUSE ROLL CALL VOTE SHEET
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RollCallVoteSheet:
    bill_number:   str = ""
    sponsor:       str = ""
    bill_title:    str = ""
    read_stage:    str = ""
    session_day:   str = ""
    date:          str = ""
    roll_call:     str = ""
    votes:         list[dict] = field(default_factory=list)
    total_yes:     int = 0
    total_no:      int = 0
    total_present: int = 0
    total_absent:  int = 0
    vacancies:     int = 0

    def yeas(self)    -> list[str]: return [v["name"] for v in self.votes if v["vote"] == "Y"]
    def nays(self)    -> list[str]: return [v["name"] for v in self.votes if v["vote"] == "N"]
    def present(self) -> list[str]: return [v["name"] for v in self.votes if v["vote"] in ("@", "P")]
    def by_vote(self, code: str) -> list[str]: return [v["name"] for v in self.votes if v["vote"] == code]

    @property
    def is_valid(self) -> bool:
        return bool(self.votes) and self.total_yes > 0


_RC_PROBE           = re.compile(r"Total\s+Yes\s*:\s*\d+", re.IGNORECASE)
_RC_BILL_PAT        = re.compile(r"\b(H[BCJRs]+\s*\d+|S[BCJRs]+\s*\d+)\b")
_RC_SPONSOR_PAT     = re.compile(r"\b([A-Z]{3,}(?:\s+[A-Z]{2,})*)\s+\((\d{3})\)")
_RC_READ_PAT        = re.compile(
    r"((?:HBs?|SBs?|HJRs?|SJRs?)\s*[\w\s]*?"
    r"(?:1st|2nd|3rd|\d+th)\s+READ[^\n]{0,60})",
    re.IGNORECASE,
)
_RC_SESSION_DAY_PAT = re.compile(r"Session\s+Day\s*:\s*(\d+)", re.IGNORECASE)
_RC_DATE_PAT        = re.compile(r"Date\s*:\s*([\d/]+)", re.IGNORECASE)
_RC_ROLL_CALL_PAT   = re.compile(r"Roll\s+Call\s*:\s*(\d+)", re.IGNORECASE)
_RC_TOTAL_YES_PAT   = re.compile(r"Total\s+Yes\s*:\s*(\d+)", re.IGNORECASE)
_RC_TOTAL_NO_PAT    = re.compile(r"Total\s+No\s*:\s*(\d+)", re.IGNORECASE)
_RC_TOTAL_PRES_PAT  = re.compile(r"Total\s+Present\s*:\s*(\d+)", re.IGNORECASE)
_RC_TOTAL_ABS_PAT   = re.compile(r"Total\s+Absent\s*:\s*(\d+)", re.IGNORECASE)
_RC_VACANCIES_PAT   = re.compile(r"Vacancies\s*:\s*(\d+)", re.IGNORECASE)

# Handles Y/N/@/P, optional extra whitespace, district suffix on name
_RC_VOTE_ENTRY_PAT = re.compile(
    r"([YN@P])\s*-\s*([A-Z][A-Z '\-]+?(?:\s+\d{1,3})?)"
    r"(?=\s+[YN@P]\s*-|\s*(?:Total|Vacancies)|\s*$|\n)",
    re.MULTILINE,
)
_RC_DISCARD = frozenset({
    "BUILD CONTRACTS", "MR SPEAKER", "UNOFFICIAL COPY", "GENERAL ASSEMBLY",
})


def _is_roll_call_vote_sheet(text: str) -> bool:
    return bool(_RC_PROBE.search(text))


def parse_roll_call_vote_sheet(text: str) -> RollCallVoteSheet:
    r = RollCallVoteSheet()

    bills = _RC_BILL_PAT.findall(text)
    if bills:
        r.bill_number = bills[0].strip()

    m = _RC_SPONSOR_PAT.search(text)
    if m:
        r.sponsor = f"{m.group(1).strip()} ({m.group(2)})"

    m = _RC_READ_PAT.search(text)
    if m:
        r.read_stage = re.sub(r"\s+", " ", m.group(1)).strip()

    m = _RC_SESSION_DAY_PAT.search(text)
    if m:
        r.session_day = m.group(1)

    m = _RC_DATE_PAT.search(text)
    if m:
        r.date = m.group(1)

    m = _RC_ROLL_CALL_PAT.search(text)
    if m:
        r.roll_call = m.group(1)

    # Title: all-caps lines before Session Day / Read Stage / first vote entry
    day_pos  = (m.start() if (m := _RC_SESSION_DAY_PAT.search(text)) else len(text))
    read_pos = (m.start() if (m := _RC_READ_PAT.search(text)) else len(text))
    stop     = min(day_pos, read_pos)
    title_lines: list[str] = []
    for line in text[:stop].splitlines():
        s = line.strip()
        if not s or _RC_VOTE_ENTRY_PAT.search(s):
            break
        if re.fullmatch(r"[A-Z0-9 '\-/,\.()]+", s):
            if not _RC_BILL_PAT.fullmatch(s) and not _RC_SPONSOR_PAT.fullmatch(s):
                title_lines.append(s)
    r.bill_title = " ".join(title_lines).strip()

    votes: list[dict] = []
    for entry in _RC_VOTE_ENTRY_PAT.finditer(text):
        name = entry.group(2).strip()
        if name in _RC_DISCARD:
            continue
        votes.append({"name": name, "vote": entry.group(1)})
    r.votes = votes

    for pat, attr in [
        (_RC_TOTAL_YES_PAT,  "total_yes"),
        (_RC_TOTAL_NO_PAT,   "total_no"),
        (_RC_TOTAL_PRES_PAT, "total_present"),
        (_RC_TOTAL_ABS_PAT,  "total_absent"),
        (_RC_VACANCIES_PAT,  "vacancies"),
    ]:
        m = pat.search(text)
        if m:
            setattr(r, attr, int(m.group(1)))

    parsed_yes = len(r.yeas())
    if r.total_yes and parsed_yes != r.total_yes:
        log.warning(
            "roll_call_sheet: declared yes=%d parsed=%d "
            "(bill=%s day=%s rc=%s) — declared count stored",
            r.total_yes, parsed_yes, r.bill_number, r.session_day, r.roll_call,
        )

    return r


# ─────────────────────────────────────────────────────────────────────────────
# VOTE BLOCK DISCOVERY IN FULL JOURNAL TEXT
# ─────────────────────────────────────────────────────────────────────────────

_VOTE_TRIGGER = re.compile(
    r"([^\n]*?(?:following vote|by the following)[^\n]*)\n",
    re.IGNORECASE,
)
_BILL_LABEL_RE = re.compile(
    r"\b((?:SB|HB|SCR|HCR|SJR|HJR|SR|HR)\s*\d+)\b",
    re.IGNORECASE,
)
_MOTION_VERB_RE = re.compile(
    r"((?:Do Pass|adopted|passed|approved|rejected|failed|third reading|"
    r"perfected|truly agreed|emergency clause|conference committee)[^\n]{0,120})",
    re.IGNORECASE,
)
_BARE_TRIGGER_RE = re.compile(
    r"^(?:the\s+)?(?:journal[^\n]{0,80})?by the following vote[:\s]*$",
    re.IGNORECASE,
)


def _build_block_header(pre_context: str, trigger_line: str) -> tuple[str, str | None]:
    bill_label: str | None = None
    for m in _BILL_LABEL_RE.finditer(pre_context):
        bill_label = m.group(1).strip()
    if m2 := _BILL_LABEL_RE.search(trigger_line):
        bill_label = m2.group(1).strip()

    motion_from_context: str | None = None
    for m in _MOTION_VERB_RE.finditer(pre_context):
        motion_from_context = m.group(1).strip()

    trigger_stripped = trigger_line.strip()
    if _BARE_TRIGGER_RE.match(trigger_stripped):
        if motion_from_context:
            motion_text = motion_from_context
        else:
            lines = [ln.strip() for ln in pre_context.splitlines() if ln.strip()]
            motion_text = lines[-1] if lines else trigger_stripped
    else:
        clean = re.sub(
            r"\s*by the following vote[:\s]*$", "", trigger_stripped, flags=re.IGNORECASE
        ).strip()
        if motion_from_context and motion_from_context.lower() not in clean.lower():
            motion_text = f"{motion_from_context} — {clean}" if clean else motion_from_context
        else:
            motion_text = clean or trigger_stripped

    return motion_text[:500], bill_label


def find_vote_blocks_in_journal(text: str) -> list[dict]:
    """
    Find all vote blocks in a full Senate or House journal text.

    Returns list of dicts:
      header      — cleaned motion description
      bill_label  — e.g. "HB 2097" or None
      text        — full block text
      position    — char offset in source
      chamber     — "senate" or "house"
    """
    blocks: list[dict] = []
    for trigger_match in _VOTE_TRIGGER.finditer(text):
        trigger_start = trigger_match.start()
        trigger_line  = trigger_match.group(1)

        pre_context = text[max(0, trigger_start - 1500):trigger_start]
        window      = text[trigger_start: trigger_start + 4500]

        is_house  = bool(_H_SECTION_PROBE.search(window))
        is_senate = bool(_S_SECTION_PROBE.search(window))
        if not is_house and not is_senate:
            continue

        chamber  = "house" if is_house else "senate"
        end_pat  = _H_BLOCK_END if is_house else _S_BLOCK_END
        end_m    = end_pat.search(window)

        if end_m:
            end = trigger_start + end_m.end()
        else:
            next_t = _VOTE_TRIGGER.search(text, trigger_match.end())
            end    = next_t.start() if next_t else trigger_start + 3000

        motion_text, bill_label = _build_block_header(pre_context, trigger_line)
        blocks.append({
            "header":     motion_text,
            "bill_label": bill_label,
            "text":       text[trigger_start:end],
            "position":   trigger_start,
            "chamber":    chamber,
        })

    log.debug("find_vote_blocks: %d blocks in %d chars", len(blocks), len(text))
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# RSMo CITATION EXTRACTION  (shared by bill scrapers)
# ─────────────────────────────────────────────────────────────────────────────

_RSMO_SECTION_PAT = re.compile(r"(?:section|§)\s*(\d{1,3}\.\d{3,4})", re.IGNORECASE)
_RSMO_CHAPTER_PAT = re.compile(r"\bchapter\s+(\d{1,3})(?!\.\d)", re.IGNORECASE)
_RSMO_DATE_NOISE  = re.compile(r"\b\d{1,2}\.\d{2}\.\d{4}\b")


def extract_rsmo_citations(text: str) -> list[str]:
    """Return deduplicated sorted RSMo section numbers from bill text."""
    cleaned = _RSMO_DATE_NOISE.sub("", text)
    seen: set[str] = set()
    out: list[str] = []
    for s in _RSMO_SECTION_PAT.findall(cleaned):
        chapter = int(s.split(".")[0])
        if 1 <= chapter <= 699 and s not in seen:
            seen.add(s); out.append(s)
    for c in _RSMO_CHAPTER_PAT.findall(cleaned):
        key = f"{c}.000"
        if key not in seen:
            seen.add(key); out.append(key)
    return sorted(out, key=lambda s: [int(p) for p in s.split(".")])