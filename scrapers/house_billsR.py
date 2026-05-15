"""
scrapers/house_bills.py
Missouri House of Representatives — bills, members, actions, roll calls.

Discovery strategy by session type:
  CURRENT session (live):
    1. Bill list:    archive.house.mo.gov/LegislationTMenu.aspx  → SubjectIndex.aspx per subject (~150 subject pages, deduplicated) 
    2. Per-bill:     house.mo.gov/BillContent.aspx            (actions, links, PDFs)
    3. XML suppl.:   documents.house.mo.gov/BillList.xml      (titles, sponsors, roll-call totals)

  PAST sessions (closed):
    1. Bill list:    documents.house.mo.gov/xml/{zip_code}.zip (bulk XML archive)
    2. All metadata from XML; summaries/texts fetched from documents.house.mo.gov

  Documents (both sessions):
    Summaries:    documents.house.mo.gov/billtracking/bills{assembly}/sumpdf/{bill}{v}.pdf
    Full texts:   linked from BillContent.aspx / BillText XML nodes
    Fiscal notes, amendments, testimony: linked from BillContent.aspx
    Roll calls:   linked from BillContent.aspx (PDF parse) or XML (totals only)

Storage layout (under STORAGE root):
  bills/{session_id}/house/{bill_label}/
    texts/
    summaries/
    fiscal_notes/
    amendments/
    testimony/
    roll_calls/
"""

import hashlib
import io
import re
import time
import zipfile
import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

from config.settings import DEFAULT_YEAR, DEFAULT_SESSION, STORAGE
from utils.http import fetch_bytes
from utils.pdf import extract_rsmo_citations
from db.db import (
    get_db, upsert_member, upsert_bill, insert_action,
    insert_floor_vote, log_change, now_utc, _action_hash,
)

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

HOUSE_BASE     = "https://house.mo.gov"
HOUSE_XML_BASE = "https://documents.house.mo.gov"
HOUSE_IMG_BASE = "https://images.house.mo.gov"
ARCHIVE_BASE   = "https://archive.house.mo.gov"

# Maps session_id → zip archive code used in documents.house.mo.gov/xml/{code}.zip
SESSION_ZIP_CODES: dict[str, str] = {
    "2026R": "261", "2025R": "251", "2024R": "241", "2023R": "231",
    "2022R": "221", "2021R": "211", "2020R": "201", "2019R": "191",
    "2018R": "181", "2017R": "171", "2016R": "161", "2015R": "151",
}

# Bill text version codes, in discovery order (earlier = less complete version)
HOUSE_PDF_VERSIONS = ["T", "C", "S", "P", "I"]

# Bill type prefixes we care about (exclude HR = simple chamber-use resolutions)
BILL_PREFIXES = re.compile(r"^H(?:B|CB|CR|JR)\d+$", re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SLEEP        = 0.4
_RETRY_WAITS = [2, 5, 10]

_PARTY_MAP = {
    "republican": "R", "democrat": "D", "democratic": "D", "independent": "I",
    "r": "R", "d": "D", "i": "I",
}

_STAGE_MAP: dict[str, str] = {
    "T": "introduced", "C": "committee_sub", "S": "committee_sub",
    "P": "perfected",  "I": "truly_agreed",
}

_STAGE_DATE_KW: dict[str, list[str]] = {
    "introduced":    ["introduced"],
    "committee_sub": ["committee substitute", "do pass"],
    "perfected":     ["perfected"],
    "conference":    ["conference"],
    "truly_agreed":  ["truly agreed", "tatfp", "finally passed"],
}


# ── Utility: session classification ──────────────────────────────────────────

def _is_past_session(year: int, session: str, current_year: int = DEFAULT_YEAR,
                     current_session: str = DEFAULT_SESSION) -> bool:
    """Return True if this year/session is a closed (past) legislative session."""
    return (year, session) < (current_year, current_session)


def _assembly_from_year(year: int) -> str:
    """Derive the two-digit assembly code used in PDF paths (e.g. 2025 → '251')."""
    return SESSION_ZIP_CODES.get(f"{year}R", str((year % 100) * 10 + 1))


# ── Storage helpers ───────────────────────────────────────────────────────────

def _bill_dir(session_id: str, bill_label: str, subdir: str) -> Path:
    d = STORAGE / "bills" / session_id / "house" / bill_label.replace(" ", "") / subdir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_doc(session_id: str, bill_label: str, subdir: str,
              filename: str, content: bytes) -> Path:
    dest = _bill_dir(session_id, bill_label, subdir) / filename
    dest.write_bytes(content)
    return dest


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(url: str, **kwargs) -> requests.Response | None:
    kwargs.setdefault("headers", _HEADERS)
    kwargs.setdefault("timeout", 15)
    for i, wait in enumerate(_RETRY_WAITS):
        try:
            r = requests.get(url, **kwargs)
            return r
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            if i < len(_RETRY_WAITS) - 1:
                log.debug("Retry %d for %s: %s", i + 1, url[:70], e)
                time.sleep(wait)
            else:
                log.warning("All retries failed: %s — %s", url[:70], e)
    return None


def _fetch_pdf_bytes(url: str) -> bytes | None:
    try:
        r = _get(url, timeout=20)
        if not r or r.status_code != 200 or len(r.content) < 500:
            return None
        return r.content if r.content[:4] == b"%PDF" else None
    except Exception as e:
        log.debug("PDF fetch failed %s: %s", url, e)
        return None


# ── XML helpers ───────────────────────────────────────────────────────────────

def _t(elem, tag: str, default: str = "") -> str:
    """Safe text extractor for an XML child element."""
    if elem is None:
        return default
    child = elem.find(tag)
    return (child.text or "").strip() if child is not None else default


def _parse_xml(content: bytes) -> ET.Element | None:
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        pass
    # Try escaping bare ampersands and retry
    try:
        fixed = re.sub(rb"&(?!amp;|lt;|gt;|quot;|apos;|#)", b"&amp;", content)
        return ET.fromstring(fixed)
    except ET.ParseError as e:
        log.error("XML parse failed: %s", e)
        return None


# ── Text extraction helpers ───────────────────────────────────────────────────

def _extract_title_from_pdf(text: str) -> str | None:
    for line in text.split("\n"):
        line = line.strip()
        if "--" in line:
            title = line.split("--", 1)[1].strip()
            if title:
                return title
    return None


def _extract_sponsor_from_pdf(text: str) -> str | None:
    m = re.search(r"SPONSOR:\s*(.+)", text, re.IGNORECASE)
    if m:
        return m.group(1).strip().split("\n")[0].strip()
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MEMBER SCRAPING
# ─────────────────────────────────────────────────────────────────────────────

def _member_photo_url(district: int) -> str:
    return f"{HOUSE_IMG_BASE}/MemberPhoto.aspx?id={district:03d}"


def _extract_party_from_soup(soup: BeautifulSoup) -> str | None:
    for tag in soup.find_all("div", class_="details"):
        raw = tag.get_text(separator=" ", strip=True)
        first_word = raw.split()[0] if raw.split() else ""
        norm = _PARTY_MAP.get(first_word.lower()) or _PARTY_MAP.get(raw.lower().strip())
        if norm:
            return norm
    page_text = soup.get_text(separator=" ")
    m = re.search(
        r"(?:Political\s+)?Party\s*:?\s+(Republican|Democrat(?:ic)?|Independent)",
        page_text, re.IGNORECASE,
    )
    if m:
        return _PARTY_MAP.get(m.group(1).lower())
    return None


def _scrape_member_detail(district: int, year: int, code: str = "R") -> dict | None:
    url = (f"{HOUSE_BASE}/MemberDetails.aspx"
           f"?year={year}&code={code}&district={district:03d}")
    r = _get(url)
    if not r or r.status_code != 200:
        return None
    if any(kw in r.text.lower() for kw in ("incorrect link", "application error", "vacant")):
        return None

    soup = BeautifulSoup(r.text, "html.parser")
    raw_name = None
    for tag in soup.find_all(["h1", "h2", "title"]):
        t = re.sub(r"(?i)^(representative\s+|missouri house.*?-\s*)", "",
                   tag.get_text(strip=True)).strip()
        if t and not re.search(r"(?i)vacant|error|missouri house", t):
            raw_name = t
            break
    if not raw_name:
        return None

    if "," in raw_name:
        last_name, first_name = [p.strip() for p in raw_name.split(",", 1)]
    else:
        parts      = raw_name.split()
        first_name = parts[0] if parts else ""
        last_name  = " ".join(parts[1:]) if len(parts) > 1 else raw_name

    def _lv(label: str) -> str:
        for tag in soup.find_all(True):
            if tag.get_text(strip=True).lower() == label.lower():
                nxt = tag.find_next_sibling()
                if nxt:
                    v = nxt.get_text(" ", strip=True)
                    if v:
                        return v
        return ""

    email_tag    = soup.find("a", href=re.compile(r"^mailto:", re.I))
    year_elected = re.search(r"\d{4}", _lv("Year Elected") or _lv("First Elected") or "")

    return {
        "member_id":     10000 + district,
        "chamber":       "house",
        "first_name":    first_name,
        "last_name":     last_name,
        "full_name":     raw_name,
        "party":         _extract_party_from_soup(soup),
        "district":      district,
        "county_list":   _lv("County") or _lv("Counties") or None,
        "first_elected": year_elected.group() if year_elected else None,
        "phone":         _lv("Phone") or _lv("Telephone") or None,
        "email":         (email_tag["href"].replace("mailto:", "").strip()
                          if email_tag else None),
        "photo_url":     _member_photo_url(district),
        "last_scraped":  now_utc(),
    }


def run_members(year: int = DEFAULT_YEAR, session: str = DEFAULT_SESSION) -> None:
    log.info("Scraping House members year=%d", year)
    members: list[dict] = []
    vacant:  list[int]  = []

    for district in range(1, 164):
        m = _scrape_member_detail(district, year, session)
        if m:
            members.append(m)
        else:
            vacant.append(district)
        time.sleep(SLEEP)

    log.info("House members: %d found, %d vacant", len(members), len(vacant))
    with get_db() as conn:
        for member in members:
            upsert_member(conn, member)
            if member.get("last_name"):
                conn.execute("""
                    INSERT OR IGNORE INTO member_name_variants
                        (member_id, name_variant, source)
                    VALUES (?, ?, 'detail_page')
                """, (member["member_id"], member["last_name"].upper()))
    log.info("House members saved: %d", len(members))


# ─────────────────────────────────────────────────────────────────────────────
# BILL DISCOVERY
# ─────────────────────────────────────────────────────────────────────────────

def _discover_bills_live(year: int, session: str = "R") -> list[str]:
    """
    Discover bill numbers for a live/current session by:
      1. Fetching LegislationTMenu.aspx to get all subject codes
      2. Fetching each SubjectIndex.aspx?subjectcode=N to collect bill numbers
      3. Deduplicating across all subjects
    
    The top-level LegislationSP.aspx and LegislationTMenu.aspx pages are JS-rendered
    shells — the actual bill lists live in SubjectIndex.aspx per subject code.
    """
    # Step 1: get all subject codes from the menu iframe
    menu_url = f"{ARCHIVE_BASE}/LegislationTMenu.aspx?year={year}&code={session}"
    r = _get(menu_url)
    if not r or r.status_code != 200:
        log.error("LegislationTMenu failed: HTTP %s", r and r.status_code)
        return []

    subject_codes = re.findall(r'SubjectIndex\.aspx\?subjectcode=(\d+)', r.text)
    if not subject_codes:
        log.error("No subject codes found in LegislationTMenu — page structure may have changed")
        return []

    log.info("Bill discovery: found %d subject codes for %d/%s", len(subject_codes), year, session)

    # Step 2: fetch each subject page and collect bill numbers
    bills: set[str] = set()
    for i, code in enumerate(subject_codes):
        url = f"{ARCHIVE_BASE}/SubjectIndex.aspx?subjectcode={code}&year={year}&code={session}"
        r = _get(url)
        if not r or r.status_code != 200:
            log.debug("SubjectIndex %s failed: HTTP %s", code, r and r.status_code)
            continue
        found = re.findall(r'[?&]bill=(H[A-Z]+\d+)', r.text, re.I)
        for b in found:
            b = b.upper()
            if BILL_PREFIXES.match(b):
                bills.add(b)
        time.sleep(0.15)  # be polite — this is ~150 requests
        if (i + 1) % 25 == 0:
            log.info("  Subject pages: %d/%d fetched, %d unique bills so far",
                     i + 1, len(subject_codes), len(bills))

    result = sorted(
        bills,
        key=lambda b: (re.match(r"[A-Z]+", b).group(), int(re.search(r"\d+", b).group()))
    )
    log.info("Bill discovery complete: %d unique bills for %d/%s", len(result), year, session)
    if len(result) < 20:
        log.warning("Suspiciously few bills (%d) — check SubjectIndex URL pattern", len(result))
    return result


def _discover_bills_from_zip(session_id: str) -> tuple[list[str], Path | None]:
    """
    Discover bills for a past session by downloading the bulk XML zip archive.
    Returns (bill_labels, extract_dir) — extract_dir is None on failure.
    """
    zip_code = SESSION_ZIP_CODES.get(session_id)
    if not zip_code:
        log.error("No zip archive code known for session %s", session_id)
        return [], None

    zip_url   = f"{HOUSE_XML_BASE}/xml/{zip_code}.zip"
    zip_bytes = fetch_bytes(zip_url)
    if not zip_bytes:
        log.error("Failed to download zip archive: %s", zip_url)
        return [], None

    extract_dir = STORAGE / "house_xml" / session_id
    extract_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as e:
        log.error("Bad zip for %s: %s", session_id, e)
        return [], None

    bill_files = [
        f for f in extract_dir.rglob("*.xml")
        if re.match(r"H[A-Z]+\d+\.xml$", f.name)
    ]
    bill_labels = sorted(
        {f.stem.upper() for f in bill_files if BILL_PREFIXES.match(f.stem)},
        key=lambda b: (re.match(r"[A-Z]+", b).group(), int(re.search(r"\d+", b).group()))
    )
    log.info("Bill discovery (zip): %d bills extracted for %s", len(bill_labels), session_id)
    return bill_labels, extract_dir


# ─────────────────────────────────────────────────────────────────────────────
# PER-BILL PAGE SCRAPING (live/current sessions)
# ─────────────────────────────────────────────────────────────────────────────

def _scrape_bill_page(bill_number: str, year: int, session: str = "R") -> dict:
    """
    Fetch house.mo.gov/BillContent.aspx for a single bill and extract:
      - actions (date, description, journal URL)
      - fulltext PDF URLs + version codes
      - roll call PDF URLs
      - testimony, fiscal note, amendment PDF URLs
    """
    result: dict = {
        "actions": [], "fulltext_urls": [], "rollcall_urls": [],
        "testimony_urls": [], "fiscal_note_urls": [], "amendment_urls": [],
    }
    url = (f"{HOUSE_BASE}/BillContent.aspx"
           f"?bill={bill_number.replace(' ', '')}"
           f"&year={year}&code={session}&style=new")
    r = _get(url)
    if not r or r.status_code != 200:
        log.warning("BillContent page failed for %s: HTTP %s", bill_number, r and r.status_code)
        return result

    soup = BeautifulSoup(r.text, "html.parser")

    # ── Actions table ────────────────────────────────────────────────────────
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_text = " ".join(
            th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])
        )
        if "date" not in header_text and "action" not in header_text:
            continue
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            date_text = cells[0].get_text(strip=True)
            desc_text = cells[1].get_text(strip=True)
            if not date_text or not re.match(r"\d", date_text):
                continue
            jrn_url = None
            for a in row.find_all("a", href=True):
                if "jrnpdf" in a["href"].lower():
                    href    = a["href"]
                    jrn_url = href if href.startswith("http") else f"{HOUSE_XML_BASE}{href}"
                    break
            result["actions"].append({
                "chamber":     "house",
                "action_date": date_text,
                "action_text": desc_text,
                "journal_url": jrn_url,
            })

    # ── Document links ────────────────────────────────────────────────────────
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = href if href.startswith("http") else f"{HOUSE_XML_BASE}{href}"
        hl   = href.lower()

        if "hlrbillspdf" in hl:
            m = re.search(r"(?:H[A-Z]+\d+|H\.\d+)([TSPCI])\.pdf", href, re.I)
            version = m.group(1).upper() if m else None
            if version and (full, version) not in result["fulltext_urls"]:
                result["fulltext_urls"].append((full, version))
        elif "rollcalls" in hl and hl.endswith(".pdf"):
            if full not in result["rollcall_urls"]:
                result["rollcall_urls"].append(full)
        elif "witnesses" in hl or "testimony" in hl:
            if full not in result["testimony_urls"]:
                result["testimony_urls"].append(full)
        elif "fiscalnote" in hl or "fiscal_note" in hl:
            if full not in result["fiscal_note_urls"]:
                result["fiscal_note_urls"].append(full)
        elif "amendment" in hl and hl.endswith(".pdf"):
            if full not in result["amendment_urls"]:
                result["amendment_urls"].append(full)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT FETCHERS (shared by live and past sessions)
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_summaries(
    bill_number: str,
    year: int,
    assembly: str,
    session_id: str,
    existing_versions: set | None = None,
) -> list[dict]:
    """
    Download summary PDFs for all known version codes (T, C, S, P, I).
    Stops at the first 404 (versions are sequential).
    Skips versions already in existing_versions.
    """
    bill_clean = bill_number.replace(" ", "")
    results: list[dict] = []

    for v in HOUSE_PDF_VERSIONS:
        url = f"{HOUSE_XML_BASE}/billtracking/bills{assembly}/sumpdf/{bill_clean}{v}.pdf"
        try:
            r = _get(url, timeout=12)
            if not r or r.status_code == 404:
                break                          # versions are sequential; stop on first 404
            if r.status_code != 200 or len(r.content) < 500:
                continue
            if existing_versions and v in existing_versions:
                continue                       # already have this version
            if r.content[:4] != b"%PDF":
                continue

            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()

            if not text:
                continue

            filename     = f"sum_{bill_clean}{v}.pdf"
            storage_path = _save_doc(session_id, bill_clean, "summaries", filename, r.content)
            results.append({
                "version_code":   v,
                "text":           text,
                "title":          _extract_title_from_pdf(text),
                "sponsor":        _extract_sponsor_from_pdf(text),
                "rsmo_citations": extract_rsmo_citations(text),
                "source_url":     url,
                "storage_path":   str(storage_path),
            })
            time.sleep(SLEEP)
        except Exception as e:
            log.debug("Summary PDF %s v%s: %s", bill_number, v, e)

    return results


def _download_ancillary_docs(
    bill_number: str,
    session_id: str,
    fiscal_note_urls: list[str],
    amendment_urls: list[str],
    testimony_urls: list[str],
) -> dict:
    """Download fiscal notes, amendments, and testimony PDFs to storage."""
    bill_clean = bill_number.replace(" ", "")
    saved = {"fiscal_notes": 0, "amendments": 0, "testimony": 0}

    for urls, subdir, key in [
        (fiscal_note_urls, "fiscal_notes", "fiscal_notes"),
        (amendment_urls,   "amendments",   "amendments"),
        (testimony_urls,   "testimony",    "testimony"),
    ]:
        for u in urls:
            fname = u.rstrip("/").split("/")[-1] or f"{key}.pdf"
            dest  = _bill_dir(session_id, bill_clean, subdir) / fname
            if dest.exists():
                continue
            raw = _fetch_pdf_bytes(u)
            if raw:
                dest.write_bytes(raw)
                saved[key] += 1
                time.sleep(SLEEP)

    return saved


def _parse_roll_call_pdf(url: str) -> dict | None:
    """Download and parse a House roll-call PDF into a structured dict."""
    try:
        r = _get(url, timeout=20)
        if not r or r.status_code != 200 or r.content[:4] != b"%PDF":
            return None
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        if not text:
            return None
    except Exception as e:
        log.debug("Roll call PDF failed %s: %s", url, e)
        return None

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    motion = lines[0] if lines else "Floor Vote"

    date = None
    for line in lines[:10]:
        dm = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", line)
        if dm:
            date = dm.group(1)
            break

    yes = no = present = absent = 0
    for line in lines[:20]:
        for label, attr in [("AYES", "yes"), ("NOES", "no"),
                             ("PRESENT", "present"), ("ABSENT", "absent")]:
            m = re.search(rf"{label}[:\s]+(\d+)", line, re.IGNORECASE)
            if m:
                locals()[attr]  # reference keeps linter happy
                if label == "AYES":    yes     = int(m.group(1))
                elif label == "NOES":  no      = int(m.group(1))
                elif label == "PRESENT": present = int(m.group(1))
                elif label == "ABSENT":  absent  = int(m.group(1))

    # Parse individual member votes
    member_rows: list[tuple[str, str]] = []
    current_type: str | None = None
    vote_hdr_re = re.compile(r"^(AYES?|NOES?|PRESENT|ABSENT)\b", re.IGNORECASE)
    name_re     = re.compile(r"^[A-Z][A-Za-z\s,'\-\.]{1,40}$")
    skip_re     = re.compile(r"^(AYES|NOES|TOTAL|MOTION|HOUSE|SENATE|ROLL|CALL|PAGE|\d)",
                              re.IGNORECASE)
    _vote_type_map = {
        "NOE": "Nay", "AYE": "Yea", "PRESENT": "NV", "ABSENT": "Absent",
    }
    for line in lines:
        if vote_hdr_re.match(line):
            raw_type = line.split()[0].upper().rstrip("S")
            current_type = _vote_type_map.get(raw_type, raw_type)
        elif current_type and name_re.match(line) and not skip_re.match(line):
            member_rows.append((line, current_type))

    return {
        "motion":        motion,
        "vote_date":     date,
        "yes_count":     yes,
        "no_count":      no,
        "present_count": present,
        "absent_count":  absent,
        "member_votes":  member_rows,
        "roll_call_pdf": url,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BILL CONSTRUCTION
# ─────────────────────────────────────────────────────────────────────────────

def _build_bill_from_html(
    bill_number: str,
    year: int,
    assembly: str,
    session_id: str,
    session: str = "R",
    existing_summary_versions: set | None = None,
    existing_rollcall_urls: set | None = None,
) -> dict | None:
    """
    Build a complete bill dict for a live session by combining:
      - BillContent.aspx (actions, links)
      - Summary PDFs (title, sponsor, text)
      - Roll call PDFs (member votes)
      - Ancillary docs (fiscal notes, amendments, testimony)
    """
    m = re.match(r"([A-Z]+)(\d+)", bill_number, re.I)
    if not m:
        return None

    bill_type       = m.group(1).upper()
    bill_number_int = int(m.group(2))
    bill_clean      = bill_number.replace(" ", "")

    page = _scrape_bill_page(bill_number, year, session)

    summaries = _fetch_summaries(
        bill_number, year, assembly, session_id,
        existing_versions=existing_summary_versions,
    )

    anc = _download_ancillary_docs(
        bill_number, session_id,
        page["fiscal_note_urls"], page["amendment_urls"], page["testimony_urls"],
    )
    if any(anc.values()):
        log.debug("%s ancillary: fiscal=%d amend=%d testimony=%d",
                  bill_number, anc["fiscal_notes"], anc["amendments"], anc["testimony"])

    # Prefer title/sponsor from latest summary version
    title = sponsor_str = None
    for s in summaries:
        if not title       and s.get("title"):   title       = s["title"]
        if not sponsor_str and s.get("sponsor"): sponsor_str = s["sponsor"]

    # Roll calls
    roll_calls: list[dict] = []
    for rc_url in page["rollcall_urls"]:
        if existing_rollcall_urls and rc_url in existing_rollcall_urls:
            continue
        rc = _parse_roll_call_pdf(rc_url)
        if rc:
            fname   = rc_url.rstrip("/").split("/")[-1] or "rollcall.pdf"
            rc_dest = _bill_dir(session_id, bill_clean, "roll_calls") / fname
            if not rc_dest.exists():
                raw_rc = _fetch_pdf_bytes(rc_url)
                if raw_rc:
                    rc_dest.write_bytes(raw_rc)
            roll_calls.append(rc)
        time.sleep(SLEEP)

    actions     = page["actions"]
    introduced  = actions[0]["action_date"] if actions else None
    last_date   = actions[-1]["action_date"] if actions else None
    status      = actions[-1]["action_text"] if actions else None

    return {
        "bill_type":        bill_type,
        "bill_number":      bill_number_int,
        "bill_label":       bill_clean,
        "session_id":       session_id,
        "lr_number":        None,
        "title":            title,
        "short_desc":       title,
        "current_status":   status,
        "effective_date":   None,
        "introduced_date":  introduced,
        "last_action_date": last_date,
        "source_url":       (f"{HOUSE_BASE}/BillContent.aspx"
                             f"?bill={bill_clean}&year={year}&code={session}&style=new"),
        "sponsors":         ([{"member_id": None, "sponsor_type": "primary",
                               "sponsor_name": sponsor_str}]
                             if sponsor_str else []),
        "actions":          actions,
        "roll_calls":       roll_calls,
        "text_versions":    [{"url": u, "version_code": v, "doc_name": v, "web_name": ""}
                             for u, v in page["fulltext_urls"]],
        "summaries":        summaries,
        "fiscal_links":     page["fiscal_note_urls"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# XML BILL PARSER (used for past session zip archives AND live XML supplement)
# ─────────────────────────────────────────────────────────────────────────────

def parse_bill_xml(content: bytes, source_url: str, session_id: str) -> dict | None:
    """
    Parse a Missouri House bill XML file into a bill dict.
    Works for both individual bill XMLs (zip archives) and BillList.xml entries.
    """
    root = _parse_xml(content)
    if root is None:
        return None

    bill_number_raw = _t(root, "BillNumber")
    if not bill_number_raw:
        return None
    m = re.match(r"([A-Z]+)(\d+)", bill_number_raw)
    if not m:
        return None

    title_elem  = root.find("Title")
    short_title = _t(title_elem, "ShortTitle")
    long_title  = _t(title_elem, "LongTitle")
    last_action = _t(root, "LastAction")
    gov_elem    = root.find("GovernorLastAction")
    status      = (_t(gov_elem, "GovernorAction") if gov_elem is not None else "") or last_action or ""

    sponsors: list[dict] = []
    for sp in root.findall(".//Sponsor"):
        d = _t(sp, "District")
        if d.isdigit():
            sp_type = _t(sp, "SponsorType")
            sponsors.append({
                "member_id":    10000 + int(d),
                "sponsor_type": "primary" if sp_type == "Sponsor" else "cosponsor",
            })

    actions:    list[dict] = []
    roll_calls: list[dict] = []
    for action_elem in root.findall(".//Action"):
        desc     = _t(action_elem, "Description")
        pub_date = _t(action_elem, "PubDate")
        h_start  = _t(action_elem, "HouseJournalStartPage")
        s_start  = _t(action_elem, "SenateJournalStartPage")
        journal_page = (f"H{h_start}" if h_start else f"S{s_start}" if s_start else None)
        chamber      = "senate" if (s_start and not h_start) else "house"
        if desc:
            actions.append({
                "action_date": pub_date, "action_text": desc,
                "journal_page": journal_page, "chamber": chamber,
            })
        rc = action_elem.find("RollCall")
        if rc is not None:
            try:
                roll_calls.append({
                    "motion_text":   desc,
                    "vote_date":     pub_date,
                    "yes_count":     int(_t(rc, "TotalYes") or 0),
                    "no_count":      int(_t(rc, "TotalNo") or 0),
                    "present_count": int(_t(rc, "TotalPresent") or 0),
                    "roll_call_pdf": _t(rc, "RollCallPDF"),
                    "journal_page":  journal_page,
                    "chamber":       chamber,
                    "member_votes":  [],
                })
            except (ValueError, TypeError):
                pass

    return {
        "bill_type":        m.group(1),
        "bill_number":      int(m.group(2)),
        "bill_label":       bill_number_raw.replace(" ", ""),
        "session_id":       session_id,
        "lr_number":        _t(root, "CurrentLRNumber") or None,
        "title":            short_title or long_title or None,
        "short_desc":       long_title or None,
        "current_status":   status or None,
        "effective_date":   _t(root, "ProposedEffectiveDate") or None,
        "introduced_date":  actions[0]["action_date"] if actions else None,
        "last_action_date": actions[-1]["action_date"] if actions else None,
        "source_url":       source_url,
        "sponsors":         sponsors,
        "actions":          actions,
        "roll_calls":       roll_calls,
        "text_versions":    [
            {
                "url":          _t(bt, "BillTextLink"),
                "version_code": _t(bt, "VersionTypeCode"),
                "doc_name":     _t(bt, "DocumentName"),
                "web_name":     _t(bt, "WebName"),
            }
            for bt in root.findall(".//BillText")
        ],
        "summaries":   [],        # populated later by _fetch_summaries
        "fiscal_links": [
            _t(fn, "FiscalNoteLink")
            for fn in root.findall(".//FiscalNotes")
            if _t(fn, "FiscalNoteLink")
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def persist_bill(conn, bill: dict) -> int:
    """Upsert a bill and all its related records. Returns bill_pk."""

    bill_rec = {
        "bill_id":          bill.get("bill_id"),
        "session_id":       bill["session_id"],
        "chamber":          "house",
        "bill_type":        bill["bill_type"],
        "bill_number":      bill["bill_number"],
        "bill_label":       bill["bill_label"],
        "lr_number":        bill.get("lr_number"),
        "title":            bill.get("title"),
        "short_desc":       bill.get("short_desc"),
        "introduced_date":  bill.get("introduced_date"),
        "effective_date":   bill.get("effective_date"),
        "current_status":   bill.get("current_status"),
        "last_action_date": bill.get("last_action_date"),
        "last_scraped":     now_utc(),
        "source_url":       bill.get("source_url"),
    }

    existing = conn.execute("""
        SELECT bill_pk, current_status FROM bills
        WHERE session_id=? AND chamber='house' AND bill_type=? AND bill_number=?
    """, (bill["session_id"], bill["bill_type"], bill["bill_number"])).fetchone()

    bill_pk = upsert_bill(conn, bill_rec)

    if existing and existing["current_status"] != bill_rec["current_status"]:
        log_change(
            conn, "bill_status_change", "bill", str(bill_pk), "current_status",
            existing["current_status"], bill_rec["current_status"],
            scraper="house_bills", source_url=bill_rec["source_url"] or "",
        )

    # ── Sponsors ──────────────────────────────────────────────────────────────
    for sp in bill.get("sponsors", []):
        if sp.get("member_id"):
            conn.execute("""
                INSERT OR IGNORE INTO bill_sponsors (bill_pk, member_id, sponsor_type)
                VALUES (?, ?, ?)
            """, (bill_pk, sp["member_id"], sp["sponsor_type"]))
        elif sp.get("sponsor_name"):
            row = conn.execute("""
                SELECT member_id FROM members
                WHERE chamber='house' AND (last_name=? OR full_name LIKE ?)
                LIMIT 1
            """, (sp["sponsor_name"], f"%{sp['sponsor_name']}%")).fetchone()
            if row:
                conn.execute("""
                    INSERT OR IGNORE INTO bill_sponsors (bill_pk, member_id, sponsor_type)
                    VALUES (?, ?, ?)
                """, (bill_pk, row["member_id"], sp.get("sponsor_type", "primary")))

    # ── Actions ───────────────────────────────────────────────────────────────
    for action in bill.get("actions", []):
        action["bill_pk"] = bill_pk
        insert_action(conn, action)

    # ── Roll calls ────────────────────────────────────────────────────────────
    _vote_norm = {
        "Yea": "yes", "Nay": "no", "NV": "NV",
        "Absent": "absent", "Present": "present",
        "yes": "yes", "no": "no", "absent": "absent",
    }
    for rc in bill.get("roll_calls", []):
        pdf_url    = rc.get("roll_call_pdf") or ""
        raw_motion = rc.get("motion_text") or rc.get("motion") or ""
        # Encode URL into motion_text so journals.py can later retrieve and parse it
        motion_stored = (
            f"{pdf_url}|{raw_motion}" if (pdf_url.startswith("http") and raw_motion)
            else pdf_url if pdf_url.startswith("http")
            else raw_motion or None
        )

        roll_call_id = insert_floor_vote(
            conn,
            bill_pk          = bill_pk,
            session_id       = bill["session_id"],
            chamber          = rc.get("chamber", "house"),
            vote_date        = rc.get("vote_date") or "",
            motion_text      = motion_stored or "",
            yes_count        = rc.get("yes_count", 0),
            no_count         = rc.get("no_count", 0),
            present_count    = rc.get("present_count", 0),
            absent_count     = rc.get("absent_count", 0),
            journal_page     = rc.get("journal_page"),
            parse_confidence = "pdf_parse" if pdf_url.startswith("http") else "xml_summary",
        )

        if roll_call_id and rc.get("member_votes"):
            for member_name, vote_raw in rc["member_votes"]:
                vote_cast  = _vote_norm.get(vote_raw, "NV")
                member_row = conn.execute("""
                    SELECT mnv.member_id FROM member_name_variants mnv
                    JOIN members m ON mnv.member_id = m.member_id
                    WHERE mnv.name_variant = ? AND m.chamber = 'house'
                    LIMIT 1
                """, (member_name.upper(),)).fetchone()
                if not member_row:
                    member_row = conn.execute("""
                        SELECT member_id FROM members
                        WHERE chamber='house' AND UPPER(last_name) = ?
                        LIMIT 1
                    """, (member_name.upper(),)).fetchone()
                if member_row:
                    conn.execute("""
                        INSERT OR IGNORE INTO member_votes
                            (roll_call_id, member_id, vote_cast, name_raw, match_confidence)
                        VALUES (?, ?, ?, ?, ?)
                    """, (roll_call_id, member_row["member_id"], vote_cast, member_name, 1.0))

    # ── Bill versions + RSMo citations ────────────────────────────────────────
    bill_clean = bill.get("bill_label", "").replace(" ", "")
    session_id = bill["session_id"]

    def _date_for_stage(stage: str) -> str | None:
        for action in bill.get("actions", []):
            if any(kw in (action.get("action_text") or "").lower()
                   for kw in _STAGE_DATE_KW.get(stage, [])):
                return action["action_date"]
        return None

    def _insert_version(version_label: str, stage: str, storage_path,
                         citations: list, text_for_hash: str | None = None,
                         source_url: str | None = None, version_date: str | None = None):
        c_hash = hashlib.sha256(text_for_hash.encode()).hexdigest() if text_for_hash else None
        wc     = len(text_for_hash.split()) if text_for_hash else 0
        vdate  = version_date or _date_for_stage(stage)
        conn.execute("""
            INSERT OR IGNORE INTO bill_versions
                (bill_pk, version_label, version_date, stage,
                 full_text, url, storage_path, content_hash, word_count, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (bill_pk, version_label, vdate, stage,
              text_for_hash, source_url, storage_path, c_hash, wc, now_utc()))

        row = conn.execute("""
            SELECT version_id FROM bill_versions
            WHERE bill_pk=? AND version_label=? AND stage=?
        """, (bill_pk, version_label, stage)).fetchone()
        if not row:
            return
        version_id = row["version_id"]
        for section in citations:
            try:
                chapter = int(section.split(".")[0])
            except ValueError:
                continue
            conn.execute("""
                INSERT OR IGNORE INTO bill_rsmo_citations
                    (bill_pk, version_id, chapter, section, citation_type, extracted_at)
                VALUES (?, ?, ?, ?, 'references', ?)
            """, (bill_pk, version_id, chapter, section, now_utc()))

    # Summary versions (from PDFs we already downloaded and parsed)
    for s in bill.get("summaries", []):
        vc = s["version_code"].upper()
        _insert_version(
            f"{vc}_sum", _STAGE_MAP.get(vc, "introduced"),
            s.get("storage_path"), s.get("rsmo_citations", []),
            text_for_hash=s.get("text"), source_url=s.get("source_url"),
        )

    # Full-text bill versions (download PDFs not yet stored)
    for tv in bill.get("text_versions", []):
        vc  = (tv.get("version_code") or tv.get("doc_name") or "").upper()
        url = tv.get("url") or ""
        if not vc or not url:
            continue
        stage = _STAGE_MAP.get(vc, "introduced")
        if conn.execute("""
            SELECT 1 FROM bill_versions WHERE bill_pk=? AND version_label=? AND stage=?
        """, (bill_pk, vc, stage)).fetchone():
            continue

        storage_path = extracted_text = None
        try:
            r_pdf = _get(url, timeout=20)
            if r_pdf and r_pdf.status_code == 200 and len(r_pdf.content) >= 500:
                fname        = f"text_{bill_clean}{vc}.pdf"
                storage_path = str(_save_doc(session_id, bill_clean, "texts", fname, r_pdf.content))
                with pdfplumber.open(io.BytesIO(r_pdf.content)) as pdf:
                    extracted_text = (
                        "\n".join(p.extract_text() or "" for p in pdf.pages).strip() or None
                    )
        except Exception as e:
            log.debug("Full-text PDF download failed %s: %s", url, e)

        _insert_version(
            vc, stage, storage_path or url,
            extract_rsmo_citations(extracted_text) if extracted_text else [],
            text_for_hash=extracted_text, source_url=url,
        )

    return bill_pk


# ─────────────────────────────────────────────────────────────────────────────
# LIVE SESSION ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────────

def run(year: int = DEFAULT_YEAR, session: str = DEFAULT_SESSION) -> None:
    """
    Scrape all bills for a current/live session.

    Strategy:
      1. Discover bill numbers from archive.house.mo.gov (server-rendered).
      2. For each bill, scrape BillContent.aspx + download summary/rollcall PDFs.
      3. Merge titles/sponsors/roll-call totals from BillList.xml supplement.
      4. Persist everything to DB, skipping bills already scraped today.
    """
    log.info("Scraping House bills (live): %d/%s", year, session)
    session_id = f"{year}{session}"
    assembly   = _assembly_from_year(year)

    # ── Step 1: discover bills ────────────────────────────────────────────────
    bill_numbers = _discover_bills_live(year, session)
    if not bill_numbers:
        log.error("Bill list returned 0 bills — aborting")
        return

    # ── Step 2: load XML supplement (optional enrichment) ────────────────────
    xml_bill_map: dict[str, dict] = {}
    xml_list_url = f"{HOUSE_XML_BASE}/BillList.xml"
    xml_list_raw = fetch_bytes(xml_list_url)
    if xml_list_raw:
        root = _parse_xml(xml_list_raw)
        if root is not None:
            # BillList.xml may contain per-bill URLs or inline bill records
            for tag in ["BillXMLLink", "BillLink", "XMLLink", "Link", "URL"]:
                urls = [e.text.strip() for e in root.iter(tag)
                        if e.text and e.text.strip().startswith("http")]
                if urls:
                    log.info("BillList.xml: %d bill XML URLs", len(urls))
                    for url in urls:
                        raw = fetch_bytes(url)
                        if raw:
                            b = parse_bill_xml(raw, url, session_id)
                            if b:
                                xml_bill_map[b["bill_label"]] = b
                    break
    if xml_bill_map:
        log.info("XML supplement: %d bills loaded", len(xml_bill_map))

    # ── Step 3: load existing records from DB (for skip/dedup logic) ─────────
    scraped_today:      set[str]       = set()
    existing_summaries: dict[str, set] = {}
    existing_rc_urls:   dict[str, set] = {}
    today = now_utc()[:10]

    with get_db() as conn:
        for row in conn.execute("""
            SELECT b.bill_label, b.last_scraped, bv.version_label
            FROM bills b
            LEFT JOIN bill_versions bv ON bv.bill_pk = b.bill_pk
                AND bv.version_label LIKE '%_sum'
            WHERE b.session_id = ? AND b.chamber = 'house'
        """, (session_id,)):
            label = row["bill_label"]
            if row["last_scraped"] and row["last_scraped"][:10] == today:
                scraped_today.add(label)
            if row["version_label"]:
                vc = row["version_label"].replace("_sum", "")
                existing_summaries.setdefault(label, set()).add(vc)

        for row in conn.execute("""
            SELECT b.bill_label, rc.motion_text
            FROM bills b
            JOIN roll_calls rc ON rc.bill_pk = b.bill_pk
            WHERE b.session_id = ? AND b.chamber = 'house'
              AND rc.parse_confidence = 'pdf_parse'
              AND rc.motion_text LIKE 'http%'
        """, (session_id,)):
            url_part = (row["motion_text"] or "").split("|", 1)[0]
            if url_part:
                existing_rc_urls.setdefault(row["bill_label"], set()).add(url_part)

    # ── Step 4: scrape each bill ──────────────────────────────────────────────
    saved = skipped = errors = 0
    total = len(bill_numbers)

    for i, bill_number in enumerate(bill_numbers):
        try:
            if bill_number in scraped_today:
                skipped += 1
                continue

            bill = _build_bill_from_html(
                bill_number, year, assembly, session_id, session,
                existing_summary_versions=existing_summaries.get(bill_number),
                existing_rollcall_urls=existing_rc_urls.get(bill_number),
            )
            if not bill:
                errors += 1
                continue

            # Merge XML supplement data
            xml_b = xml_bill_map.get(bill_number)
            if xml_b:
                if not bill["title"]     and xml_b.get("title"):     bill["title"]     = xml_b["title"]
                if not bill["lr_number"] and xml_b.get("lr_number"): bill["lr_number"] = xml_b["lr_number"]
                if xml_b.get("sponsors"):                             bill["sponsors"]  = xml_b["sponsors"]
                seen_motions = {rc.get("motion") or rc.get("motion_text") for rc in bill["roll_calls"]}
                for rc in xml_b.get("roll_calls", []):
                    if rc.get("motion_text") not in seen_motions:
                        bill["roll_calls"].append(rc)

            with get_db() as conn:
                persist_bill(conn, bill)
            saved += 1

        except Exception as e:
            errors += 1
            log.error("%s: %s", bill_number, e, exc_info=True)

        time.sleep(SLEEP)
        if (i + 1) % 100 == 0:
            log.info("  %d/%d | saved=%d skipped=%d errors=%d",
                     i + 1, total, saved, skipped, errors)

    log.info("House bills done (live): saved=%d skipped=%d errors=%d / %d total",
             saved, skipped, errors, total)


# ─────────────────────────────────────────────────────────────────────────────
# PAST SESSION ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────────

def run_past_session(year: int, session: str = "R") -> None:
    """
    Ingest all bills for a closed past session.

    Strategy:
      1. Download bulk XML zip archive from documents.house.mo.gov/xml/{code}.zip.
      2. Parse each per-bill XML file for metadata, actions, roll-call totals, text links.
      3. Fetch summary PDFs (same endpoint as live sessions — available for past sessions too).
      4. Download full-text PDFs and ancillary docs (fiscal notes, amendments, testimony).
      5. Persist everything to DB, same schema as live sessions.

    Note: BillContent.aspx still works for past sessions, but the zip XML is more
    complete and reliable for closed sessions. We use zip XML as primary source,
    then layer in summary PDFs (which have extracted text + RSMo citations).
    """
    session_id = f"{year}{session}"
    assembly   = _assembly_from_year(year)
    log.info("Scraping House bills (past session): %s", session_id)

    # ── Step 1: discover bills via zip archive ────────────────────────────────
    bill_labels, extract_dir = _discover_bills_from_zip(session_id)
    if not bill_labels or extract_dir is None:
        log.error("No bills discovered for past session %s — aborting", session_id)
        return

    # ── Step 2: parse each XML file and enrich with PDFs ─────────────────────
    saved = errors = 0
    total = len(bill_labels)

    with get_db() as conn:
        for i, bill_label in enumerate(bill_labels):
            try:
                xml_path = next(extract_dir.rglob(f"{bill_label}.xml"), None)
                if not xml_path:
                    log.warning("XML file missing for %s", bill_label)
                    continue

                bill = parse_bill_xml(xml_path.read_bytes(), xml_path.as_uri(), session_id)
                if not bill:
                    continue

                # Fetch summary PDFs (same URL pattern as live sessions)
                # — these contain extracted text + RSMo citations
                existing_summaries = {
                    row["version_label"].replace("_sum", "")
                    for row in conn.execute("""
                        SELECT bv.version_label FROM bills b
                        JOIN bill_versions bv ON bv.bill_pk = b.bill_pk
                        WHERE b.session_id = ? AND b.bill_label = ?
                          AND bv.version_label LIKE '%_sum'
                    """, (session_id, bill_label))
                }
                bill["summaries"] = _fetch_summaries(
                    bill_label, year, assembly, session_id,
                    existing_versions=existing_summaries if existing_summaries else None,
                )

                # Download ancillary docs referenced in XML fiscal_links
                fiscal_urls = [u for u in bill.get("fiscal_links", []) if u.startswith("http")]
                if fiscal_urls:
                    _download_ancillary_docs(
                        bill_label, session_id,
                        fiscal_note_urls=fiscal_urls,
                        amendment_urls=[],
                        testimony_urls=[],
                    )

                # For roll calls that have a PDF URL in XML, attempt to parse member votes
                for rc in bill.get("roll_calls", []):
                    rc_pdf = rc.get("roll_call_pdf", "")
                    if rc_pdf and rc_pdf.startswith("http"):
                        dest = (_bill_dir(session_id, bill_label, "roll_calls")
                                / rc_pdf.rstrip("/").split("/")[-1])
                        if not dest.exists():
                            raw_rc = _fetch_pdf_bytes(rc_pdf)
                            if raw_rc:
                                dest.write_bytes(raw_rc)
                        # Parse for member-level votes (enriches the xml_summary record)
                        parsed = _parse_roll_call_pdf(rc_pdf)
                        if parsed and parsed.get("member_votes"):
                            rc["member_votes"] = parsed["member_votes"]
                        time.sleep(SLEEP)

                persist_bill(conn, bill)
                saved += 1

            except Exception as e:
                errors += 1
                log.error("%s: %s", bill_label, e, exc_info=True)

            time.sleep(SLEEP)
            if (i + 1) % 100 == 0:
                log.info("  %d/%d | saved=%d errors=%d", i + 1, total, saved, errors)

    log.info("House bills done (past session %s): saved=%d errors=%d / %d total",
             session_id, saved, errors, total)