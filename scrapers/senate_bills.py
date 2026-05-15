"""
scrapers/senate_bills.py
Scrape Missouri Senate bill list and individual bill detail pages.

Sources:
  BillList        — senate.mo.gov/BillTracking/Bills/BillList?year=YYYY&session=R
  BillDetail      — senate.mo.gov/BillTracking/Bills/BillInformation?year=YYYY&billid=NNNN
  BillText modal  — ...handler=BillText
  Summaries modal — ...handler=Summaries&billPrefix=SB&billSuffix=NNN
  Amendments      — ...handler=Amendments
  FiscalNotes     — ...handler=FiscalNotes
  Witnesses       — ...handler=Witnesses
  CommitteeVotes  — ...handler=CommitteeActions

Committee vote strategy:
  1. CommitteeActions AJAX modal → structured table rows
  2. Fallback: action_text rows containing "Do Pass" patterns

Changes from prior version:
  - insert_committee_vote() → insert_committee_vote() (new unified API in db.py;
    signature is keyword-only, vote_context='committee' is set automatically)
  - insert_action() return type changed to (is_new, action_id); callers updated
  - _extract_rsmo_citations() replaced by utils.pdf.extract_rsmo_citations()
  - All f-strings converted to %-style for consistency with rest of codebase
"""

import hashlib
import io
import logging
import re
import time
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

from config.settings import (
    SENATE_BILL_LIST, SENATE_BILL_DETAIL,
    DEFAULT_YEAR, DEFAULT_SESSION, STORAGE,
)
from utils.http import fetch_html
from utils.pdf import extract_rsmo_citations
from db.db import (
    get_db, upsert_bill, insert_action, log_change, now_utc,
    insert_committee_vote, resolve_committee_id,
)

log = logging.getLogger(__name__)

_HEADERS     = {"User-Agent": "Mozilla/5.0"}
_RETRY_WAITS = [2, 5, 10]
SLEEP        = 0.35

# ── Stage mappings ────────────────────────────────────────────────────────────

_STAGE_MAP = {
    "T": "introduced",
    "C": "committee_substitute",
    "S": "senate_substitute",
    "P": "perfected",
    "I": "truly_agreed",
}
_ANCHOR_STAGE_HINTS = [
    ("truly agreed",      "I"),
    ("tatfp",             "I"),
    ("perfected",         "P"),
    ("senate substitute", "S"),
    ("committee sub",     "C"),
    ("introduced",        "T"),
]
_URL_STAGE_HINTS = [
    ("/tatfp/", "I"),
    ("/perf/",  "P"),
    ("/ss/",    "S"),
    ("/cs/",    "C"),
    ("/intro/", "T"),
]
_STAGE_DATE_KW: dict[str, list[str]] = {
    "introduced":           ["introduced"],
    "committee_substitute": ["committee substitute", "committee sub", "do pass"],
    "perfected":            ["perfected"],
    "conference":           ["conference"],
    "truly_agreed":         ["truly agreed", "tatfp", "finally passed"],
}

# ── Committee vote patterns ───────────────────────────────────────────────────

_COMMITTEE_PASS_RE    = re.compile(r"\b(Do\s+(?:Not\s+)?Pass(?:\s+with\s+\w+)?)\b", re.IGNORECASE)
_VOTE_COUNT_INLINE_RE = re.compile(
    r"(?:Voted?[:\s]+)?(?:Yeas?|Yes)[:\s]+(\d+)[,\s]+(?:Nays?|No)[:\s]+(\d+)",
    re.IGNORECASE,
)
_VOTE_COUNT_DASH_RE = re.compile(r"\b(\d+)\s*[-–]\s*(\d+)\b")
_COMMITTEE_NAME_RE  = re.compile(
    r"committee|judiciary|education|agriculture|appropriations|"
    r"transportation|commerce|health|general laws|ways and means",
    re.IGNORECASE,
)

# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(url: str, **kwargs) -> requests.Response | None:
    kwargs.setdefault("headers", _HEADERS)
    kwargs.setdefault("timeout", 20)
    for i, wait in enumerate(_RETRY_WAITS):
        try:
            return requests.get(url, **kwargs)
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            if i < len(_RETRY_WAITS) - 1:
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


# ── Storage ───────────────────────────────────────────────────────────────────

def _bill_dir(session_id: str, bill_label: str, subdir: str) -> Path:
    d = (STORAGE / "bills" / session_id / "senate"
         / bill_label.replace(" ", "") / subdir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_doc(session_id: str, bill_label: str, subdir: str,
              filename: str, content: bytes) -> Path:
    dest = _bill_dir(session_id, bill_label, subdir) / filename
    dest.write_bytes(content)
    return dest


def _fetch_and_save_pdf(
    url: str,
    session_id: str,
    bill_label: str,
    subdir: str,
    filename: str,
) -> tuple[str | None, Path | None]:
    """Download a PDF, save it, return (extracted_text, storage_path)."""
    try:
        r = _get(url, timeout=20)
        if not r or r.status_code != 200 or len(r.content) < 500:
            return None, None
        if r.content[:4] != b"%PDF":
            return None, None
        path = _save_doc(session_id, bill_label, subdir, filename, r.content)
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
        return text.strip() or None, path
    except Exception as e:
        log.debug("PDF fetch/save failed %s: %s", url, e)
        return None, None


# ── Stage derivation ──────────────────────────────────────────────────────────

def _version_label_from_anchor_and_url(anchor_text: str, url: str) -> str:
    for keyword, vc in _ANCHOR_STAGE_HINTS:
        if keyword in anchor_text.lower():
            return vc
    for path_seg, vc in _URL_STAGE_HINTS:
        if path_seg in url.lower():
            return vc
    m = re.search(r"([TSPCI])(?=\.pdf$)", url.rstrip("/").split("/")[-1], re.I)
    return m.group(1).upper() if m else "T"


def _date_for_stage(stage: str, actions: list[dict]) -> str | None:
    for action in actions:
        lower = (action.get("action_text") or "").lower()
        if any(kw in lower for kw in _STAGE_DATE_KW.get(stage, [])):
            return action["action_date"]
    return None


# ── AJAX modal fetchers ───────────────────────────────────────────────────────

def _fetch_modal(bill_id: int, year: int, handler: str,
                 extra: dict | None = None) -> str | None:
    params = {"year": year, "billId": bill_id, "handler": handler}
    if extra:
        params.update(extra)
    try:
        r = requests.get(SENATE_BILL_DETAIL, params=params, headers=_HEADERS, timeout=20)
        if r and r.status_code == 200:
            return r.text
    except Exception as e:
        log.warning("Modal fetch error handler=%s: %s", handler, e)
    return None


def _pdf_links_from_modal(html: str | None) -> list[dict]:
    if not html:
        return []
    soup    = BeautifulSoup(html, "lxml")
    results = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not href.lower().endswith(".pdf"):
            continue
        full = href if href.startswith("http") else "https://www.senate.mo.gov" + href
        if full in seen:
            continue
        seen.add(full)
        results.append({"url": full, "anchor_text": link.get_text(" ", strip=True)})
    return results


def _fetch_bill_text_versions(bill_id: int, year: int) -> list[dict]:
    return [
        {
            "url":           item["url"],
            "anchor_text":   item["anchor_text"],
            "version_label": _version_label_from_anchor_and_url(item["anchor_text"], item["url"]),
            "stage":         _STAGE_MAP.get(
                _version_label_from_anchor_and_url(item["anchor_text"], item["url"]),
                "introduced"
            ),
        }
        for item in _pdf_links_from_modal(_fetch_modal(bill_id, year, "BillText"))
    ]


def _fetch_summary_versions(bill_id: int, year: int,
                             bill_prefix: str, bill_suffix: str) -> list[dict]:
    return [
        {
            "url":           item["url"],
            "anchor_text":   item["anchor_text"],
            "version_label": _version_label_from_anchor_and_url(item["anchor_text"], item["url"]),
            "stage":         _STAGE_MAP.get(
                _version_label_from_anchor_and_url(item["anchor_text"], item["url"]),
                "introduced"
            ),
        }
        for item in _pdf_links_from_modal(
            _fetch_modal(bill_id, year, "Summaries",
                         {"billPrefix": bill_prefix, "billSuffix": bill_suffix})
        )
    ]


def _fetch_amendment_links(bill_id: int, year: int) -> list[dict]:
    html = _fetch_modal(bill_id, year, "Amendments")
    if not html:
        return []
    soup    = BeautifulSoup(html, "lxml")
    results = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if not (href.lower().endswith(".pdf") or "amendmentpdf" in href.lower()):
            continue
        full = href if href.startswith("http") else "https://www.senate.mo.gov" + href
        if full in seen:
            continue
        seen.add(full)
        am_m = re.search(r"amendmentId=(\d+)", href, re.IGNORECASE)
        results.append({
            "url":          full,
            "anchor_text":  link.get_text(" ", strip=True),
            "amendment_id": int(am_m.group(1)) if am_m else None,
        })
    return results


def _fetch_fiscal_note_links(bill_id: int, year: int) -> list[str]:
    return [item["url"] for item in _pdf_links_from_modal(_fetch_modal(bill_id, year, "FiscalNotes"))]


def _fetch_testimony_links(bill_id: int, year: int) -> list[str]:
    return [item["url"] for item in _pdf_links_from_modal(_fetch_modal(bill_id, year, "Witnesses"))]


# ── Committee vote scraping ───────────────────────────────────────────────────

def _parse_vote_counts(text: str) -> tuple[int, int] | None:
    m = _VOTE_COUNT_INLINE_RE.search(text)
    if m:
        return int(m.group(1)), int(m.group(2))
    if _COMMITTEE_PASS_RE.search(text):
        m2 = _VOTE_COUNT_DASH_RE.search(text)
        if m2:
            return int(m2.group(1)), int(m2.group(2))
    return None


def _infer_passed(yes: int, no: int, motion_text: str) -> int | None:
    if yes + no > 0:
        return 1 if yes > no else 0
    if re.search(r"\bdo\s+not\s+pass\b", motion_text, re.IGNORECASE):
        return 0
    if re.search(r"\bdo\s+pass\b", motion_text, re.IGNORECASE):
        return 1
    return None


def _fetch_committee_votes(bill_id: int, year: int) -> list[dict]:
    results: list[dict] = []
    html = _fetch_modal(bill_id, year, "CommitteeActions")
    if not html:
        return results

    soup = BeautifulSoup(html, "lxml")
    for row in soup.find_all("tr"):
        cells    = row.find_all(["td", "th"])
        row_text = " ".join(c.get_text(" ", strip=True) for c in cells)
        if not _COMMITTEE_PASS_RE.search(row_text):
            continue

        vote_date = committee_name = motion_text = ""
        yes_count = no_count = 0

        for cell in cells:
            ct = cell.get_text(" ", strip=True)
            if re.match(r"\d{1,2}/\d{1,2}/\d{4}", ct):
                vote_date = ct
            elif _COMMITTEE_NAME_RE.search(ct):
                committee_name = ct
            elif _COMMITTEE_PASS_RE.search(ct):
                motion_text = ct.strip()
                counts = _parse_vote_counts(ct)
                if counts:
                    yes_count, no_count = counts

        if not yes_count and not no_count:
            counts = _parse_vote_counts(row_text)
            if counts:
                yes_count, no_count = counts

        if not motion_text:
            continue

        results.append({
            "committee_name": committee_name,
            "vote_date":      vote_date,
            "motion_text":    motion_text,
            "yes_count":      yes_count,
            "no_count":       no_count,
            "passed":         _infer_passed(yes_count, no_count, motion_text),
            "source":         "modal",
        })

    log.debug("bill_id=%d: %d committee votes from modal", bill_id, len(results))
    return results


def _committee_votes_from_actions(actions: list[dict]) -> list[dict]:
    results = []
    for action in actions:
        text = action.get("action_text", "")
        if not _COMMITTEE_PASS_RE.search(text):
            continue
        m         = _COMMITTEE_PASS_RE.search(text)
        motion    = m.group(0) if m else text[:100]
        counts    = _parse_vote_counts(text)
        yes, no   = counts if counts else (0, 0)
        results.append({
            "committee_name": "",
            "vote_date":      action.get("action_date", ""),
            "motion_text":    motion,
            "yes_count":      yes,
            "no_count":       no,
            "passed":         _infer_passed(yes, no, motion),
            "source":         "action_table",
        })
    return results


# ── Bill list ─────────────────────────────────────────────────────────────────

def scrape_bill_list(year: int, session: str = "R") -> list[dict]:
    html = fetch_html(SENATE_BILL_LIST, params={"year": year, "session": session})
    if not html:
        log.error("Failed to fetch Senate bill list %d/%s", year, session)
        return []

    soup  = BeautifulSoup(html, "lxml")
    bills = []
    for link in soup.find_all("a", href=re.compile(r"BillInformation\?year=\d+&billid=\d+")):
        m = re.search(r"billid=(\d+)", link["href"])
        if not m:
            continue
        bill_id    = int(m.group(1))
        bill_label = link.get_text(strip=True)
        lm = re.match(r"(S[A-Z]+|H[A-Z]+)\s*(\d+)", bill_label)
        if not lm:
            continue

        bill_type   = lm.group(1)
        bill_number = int(lm.group(2))

        sponsor_id   = None
        sponsor_name = None
        sponsor_link = link.find_next("a", href=re.compile(r"Senators/Member\?id=\d+"))
        if sponsor_link:
            sm = re.search(r"id=(\d+)", sponsor_link["href"])
            if sm:
                sponsor_id   = int(sm.group(1))
                sponsor_name = sponsor_link.get_text(strip=True)

        short_desc = ""
        container  = link.find_parent()
        if container:
            raw = container.get_text(separator=" ", strip=True)
            raw = raw.replace(bill_label, "").replace(sponsor_name or "", "")
            raw = re.sub(r"\s+", " ", raw).strip()
            sm2 = re.match(r"([^|]+?)(?:Actions|Sponsor|$)", raw)
            if sm2:
                short_desc = sm2.group(1).strip(" -–")

        bills.append({
            "bill_id":      bill_id,
            "bill_type":    bill_type,
            "bill_number":  bill_number,
            "bill_label":   bill_label,
            "sponsor_id":   sponsor_id,
            "sponsor_name": sponsor_name,
            "short_desc":   short_desc,
        })

    log.info("Senate bill list: %d bills for %d/%s", len(bills), year, session)
    return bills


# ── Bill detail ───────────────────────────────────────────────────────────────

def scrape_bill_detail(bill_id: int, year: int, session: str = "R") -> dict | None:
    html = fetch_html(SENATE_BILL_DETAIL, params={"year": year, "billid": bill_id})
    if not html:
        return None

    soup      = BeautifulSoup(html, "lxml")
    page_text = soup.get_text(separator="\n")
    detail    = {
        "bill_id":      bill_id,
        "source_url":   f"{SENATE_BILL_DETAIL}?year={year}&billid={bill_id}",
        "last_scraped": now_utc(),
    }

    heading = soup.find("div", class_="main-header-text")
    if heading:
        lm = re.match(r"(S[A-Z]+\s*\d+)", heading.get_text(strip=True))
        if lm:
            detail["bill_label"] = lm.group(1).strip()

        # Title is the next non-empty sibling element after the heading div.
        # On the verified page it renders as a plain <p> or <div> containing
        # e.g. "Modifies provisions relating to mortgage modifications".
        # We stop at the first sibling that looks like navigation/metadata
        # (contains "Print", starts with a link, etc.) to avoid false matches.
        for sib in heading.next_siblings:
            sib_name = getattr(sib, "name", None)
            if not sib_name:
                continue  # NavigableString whitespace
            candidate = sib.get_text(" ", strip=True)
            if not candidate:
                continue
            # Stop conditions: hits the Print button, a <ul>/<table>, or an <a> alone
            if candidate.lower() in ("print", "bill details") or sib_name in ("ul", "table"):
                break
            if sib_name == "a":
                break
            # A real title is plain prose — no colons (those are label:value pairs)
            # and more than a few characters
            if len(candidate) > 10 and ":" not in candidate:
                detail["title"] = candidate
                break

    bill_prefix = bill_suffix = ""
    if detail.get("bill_label"):
        pm = re.match(r"([A-Z]+)\s*(\d+)", detail["bill_label"])
        if pm:
            bill_prefix, bill_suffix = pm.group(1), pm.group(2)

    def _lv(label: str) -> str | None:
        for tag in soup.find_all(True):
            if tag.get_text(strip=True).lower() == label.lower():
                nxt = tag.find_next_sibling()
                if nxt:
                    v = nxt.get_text(" ", strip=True)
                    if v:
                        return v
                parent = tag.parent
                if parent:
                    siblings = parent.find_all(["td", "th", "div", "span"])
                    for i, s in enumerate(siblings):
                        if s is tag and i + 1 < len(siblings):
                            v = siblings[i + 1].get_text(" ", strip=True)
                            if v:
                                return v
        return None

    lr_val = _lv("LR Number") or _lv("LR#")
    if lr_val:
        lm = re.match(r"([0-9A-Z.\-]+)", lr_val)
        if lm:
            detail["lr_number"] = lm.group(1)

    summary_div = soup.find("div", class_="text-content--preformatted")
    if summary_div:
        detail["summary_text"] = summary_div.get_text(" ", strip=True)

    eff = _lv("Effective Date") or _lv("Effective")
    if eff:
        detail["effective_date"] = eff.strip()

    status = _lv("Current Status") or _lv("Status")
    if status:
        detail["current_status"] = status.strip()

    comm_link = soup.find("a", href=re.compile(r"CommitteeDetail"))
    if comm_link:
        cm = re.search(r"id=(\d+)", comm_link["href"])
        if cm:
            detail["committee_id"]   = int(cm.group(1))
            detail["committee_name"] = comm_link.get_text(strip=True)

    sp_link = soup.find("a", href=re.compile(r"Senators/Member\?id=\d+"))
    if sp_link:
        sm = re.search(r"id=(\d+)", sp_link["href"])
        if sm:
            detail["primary_sponsor_id"] = int(sm.group(1))

    hh_link = soup.find("a", href=re.compile(r"house\.mo\.gov/MemberDetails"))
    if hh_link:
        hm = re.search(r"district=(\d+)", hh_link["href"])
        if hm:
            detail["house_handler_district"] = int(hm.group(1))

    detail["actions"] = _parse_actions_table(soup, page_text)
    if detail["actions"]:
        detail["introduced_date"]  = detail["actions"][0]["action_date"]
        detail["last_action_date"] = detail["actions"][-1]["action_date"]
        if not detail.get("current_status"):
            detail["current_status"] = detail["actions"][-1]["action_text"]

    # Amendments
    amendment_links = _fetch_amendment_links(bill_id, year)
    if not amendment_links:
        for link in soup.find_all("a", href=re.compile(r"AmendmentPdf", re.I)):
            href = link["href"]
            am_m = re.search(r"amendmentId=(\d+)", href)
            amendment_links.append({
                "url":          "https://www.senate.mo.gov" + href if href.startswith("/") else href,
                "anchor_text":  link.get_text(strip=True),
                "amendment_id": int(am_m.group(1)) if am_m else None,
            })
    detail["amendments"] = amendment_links

    # Fiscal notes
    fiscal_urls = _fetch_fiscal_note_links(bill_id, year)
    if not fiscal_urls:
        for link in soup.find_all("a", href=re.compile(r"FiscalNote|fiscal", re.I)):
            href = link.get("href", "")
            if href:
                fiscal_urls.append(href if href.startswith("http")
                                   else "https://www.senate.mo.gov" + href)
    detail["fiscal_note_urls"] = fiscal_urls
    detail["testimony_urls"]   = _fetch_testimony_links(bill_id, year)
    detail["text_versions"]    = _fetch_bill_text_versions(bill_id, year)
    detail["summary_versions"] = _fetch_summary_versions(bill_id, year, bill_prefix, bill_suffix)

    cv_modal  = _fetch_committee_votes(bill_id, year)
    cv_action = _committee_votes_from_actions(detail["actions"])
    detail["committee_votes"] = cv_modal or cv_action

    return detail


def _parse_actions_table(soup: BeautifulSoup, page_text: str) -> list[dict]:
    action_pat = re.compile(
        r"(\d{1,2}/\d{1,2}/\d{4})\s+(.+?)(?=\d{1,2}/\d{1,2}/\d{4}|$)",
        re.DOTALL,
    )
    section_m = re.search(r"All Actions(.+?)(?=Quick Links|Available|$)", page_text, re.DOTALL)
    section   = section_m.group(1) if section_m else page_text
    actions   = []
    for m in action_pat.finditer(section):
        body         = m.group(2).strip()
        journal_m    = re.search(r"\b([SH]\d+)\b", body)
        journal_page = journal_m.group(1) if journal_m else None
        text         = re.sub(r"\s+", " ", body).strip()
        if journal_page:
            text = text.replace(journal_page, "").strip()
        if text:
            actions.append({
                "action_date":  m.group(1).strip(),
                "action_text":  text,
                "journal_page": journal_page,
                "chamber":      "senate",
            })
    return actions


def _extract_title_from_pdf_text(text: str) -> str | None:
    m = re.search(
        r"(AN ACT.+?)(?=SECTION\s+\d|Section\s+\d|BE IT ENACTED|$)",
        text, re.DOTALL | re.I,
    )
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if len(title) > 20:
            return title
    return None


# ── Ancillary document downloader ─────────────────────────────────────────────

def _download_ancillary_docs(
    bill_label: str,
    session_id: str,
    fiscal_note_urls: list[str],
    amendment_links: list[dict],
    testimony_urls: list[str],
) -> dict:
    saved = {"fiscal_notes": 0, "amendments": 0, "testimony": 0}

    for url in fiscal_note_urls:
        fname = re.sub(r"[^\w.\-]", "_", url.rstrip("/").split("/")[-1]) or "fiscal_note.pdf"
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        dest = _bill_dir(session_id, bill_label, "fiscal_notes") / fname
        if not dest.exists():
            raw = _fetch_pdf_bytes(url)
            if raw:
                dest.write_bytes(raw)
                saved["fiscal_notes"] += 1
                time.sleep(SLEEP)

    for item in amendment_links:
        url   = item.get("url", "")
        am_id = item.get("amendment_id")
        fname = (f"amendment_{am_id}.pdf" if am_id
                 else re.sub(r"[^\w.\-]", "_", url.rstrip("/").split("/")[-1]) or "amendment.pdf")
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        dest = _bill_dir(session_id, bill_label, "amendments") / fname
        if not dest.exists():
            raw = _fetch_pdf_bytes(url)
            if raw:
                dest.write_bytes(raw)
                saved["amendments"] += 1
                time.sleep(SLEEP)

    for url in testimony_urls:
        fname = re.sub(r"[^\w.\-]", "_", url.rstrip("/").split("/")[-1]) or "testimony.pdf"
        if not fname.lower().endswith(".pdf"):
            fname += ".pdf"
        dest = _bill_dir(session_id, bill_label, "testimony") / fname
        if not dest.exists():
            raw = _fetch_pdf_bytes(url)
            if raw:
                dest.write_bytes(raw)
                saved["testimony"] += 1
                time.sleep(SLEEP)

    return saved


# ── Version writer ────────────────────────────────────────────────────────────

def _write_versions(
    conn,
    bill_pk: int,
    label: str,
    session_id: str,
    versions: list[dict],
    kind: str,
    actions: list[dict] | None = None,
) -> dict[str, int | str]:
    """
    Download, save, and register bill text or summary PDFs.
    Returns {version_code: version_id, "_title_X": title_str, ...}.
    kind: "text" | "summary"
    """
    subdir        = "texts" if kind == "text" else "summaries"
    version_id_map: dict[str, int | str] = {}
    _ANCHOR_DATE  = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")

    for version in versions:
        url   = version["url"]
        vc    = version["version_label"]
        stage = version["stage"]
        anch  = version["anchor_text"]

        vdate = None
        m = _ANCHOR_DATE.search(anch)
        if m:
            vdate = m.group(1)
        elif actions:
            vdate = _date_for_stage(stage, actions)

        existing = conn.execute("""
            SELECT version_id FROM bill_versions
            WHERE bill_pk=? AND version_label=? AND stage=?
        """, (bill_pk, vc, stage)).fetchone()

        fname              = f"{kind}_{label.replace(' ', '')}_{vc}.pdf"
        extracted, storage = _fetch_and_save_pdf(url, session_id, label, subdir, fname)

        if existing:
            if vdate:
                conn.execute("""
                    UPDATE bill_versions SET version_date=?
                    WHERE version_id=? AND version_date IS NULL
                """, (vdate, existing["version_id"]))
            version_id_map[vc] = existing["version_id"]
            continue

        title_from_pdf = None
        if kind == "text" and extracted:
            title_from_pdf = _extract_title_from_pdf_text(extracted)

        c_hash = hashlib.sha256(extracted.encode()).hexdigest() if extracted else None
        wc     = len(extracted.split()) if extracted else 0

        cur = conn.execute("""
            INSERT OR IGNORE INTO bill_versions
                (bill_pk, version_label, version_date, stage,
                 full_text, url, storage_path, content_hash, word_count, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            bill_pk, vc, vdate, stage,
            extracted, url, str(storage) if storage else None,
            c_hash, wc, now_utc(),
        ))
        if cur.lastrowid:
            version_id_map[vc] = cur.lastrowid
            log.info("%s %s version: %s (%s) wc=%d", label, kind, vc, stage, wc)
        if title_from_pdf:
            version_id_map[f"_title_{vc}"] = title_from_pdf

    return version_id_map


# ── Run ───────────────────────────────────────────────────────────────────────

def run(year: int = DEFAULT_YEAR, session: str = DEFAULT_SESSION):
    log.info("Senate bill scrape: %d/%s", year, session)
    session_id = f"{year}{session}"
    bill_stubs = scrape_bill_list(year, session)
    if not bill_stubs:
        log.error("No bills found — aborting")
        return

    saved = errors = 0
    for i, stub in enumerate(bill_stubs):
        try:
            label = stub.get("bill_label", "")
            lm    = re.match(r"(S[A-Z]+|H[A-Z]+)\s*(\d+)", label)
            if not lm:
                continue
            bill_type   = lm.group(1)
            bill_number = int(lm.group(2))

            detail       = scrape_bill_detail(stub["bill_id"], year, session) or {}
            summary_text = (detail.get("summary_text") or "")[:500]
            short_desc   = (stub.get("short_desc") or summary_text).strip() or None

            bill_rec = {
                "bill_id":          stub["bill_id"],
                "session_id":       session_id,
                "chamber":          "senate",
                "bill_type":        bill_type,
                "bill_number":      bill_number,
                "bill_label":       label,
                "lr_number":        detail.get("lr_number"),
                "title":            detail.get("title"),
                "short_desc":       short_desc,
                "introduced_date":  detail.get("introduced_date"),
                "effective_date":   detail.get("effective_date"),
                "current_status":   detail.get("current_status"),
                "last_action_date": detail.get("last_action_date"),
                "last_scraped":     now_utc(),
                "source_url":       detail.get("source_url"),
            }

            with get_db() as conn:
                existing = conn.execute("""
                    SELECT bill_pk, current_status FROM bills
                    WHERE session_id=? AND chamber='senate'
                      AND bill_type=? AND bill_number=?
                """, (session_id, bill_type, bill_number)).fetchone()

                bill_pk = upsert_bill(conn, bill_rec)

                if existing and existing["current_status"] != bill_rec["current_status"]:
                    log_change(
                        conn, "bill_status_change", "bill", str(bill_pk), "current_status",
                        existing["current_status"], bill_rec["current_status"],
                        scraper="senate_bills", source_url=bill_rec["source_url"] or "",
                    )

                # Sponsor
                if stub.get("sponsor_id"):
                    if conn.execute("SELECT 1 FROM members WHERE member_id=?",
                                    (stub["sponsor_id"],)).fetchone():
                        conn.execute("""
                            INSERT OR IGNORE INTO bill_sponsors (bill_pk, member_id, sponsor_type)
                            VALUES (?, ?, 'primary')
                        """, (bill_pk, stub["sponsor_id"]))

                # Actions — skip committee-vote rows (stored separately)
                for action in detail.get("actions", []):
                    if _COMMITTEE_PASS_RE.search(action.get("action_text", "")):
                        continue
                    action["bill_pk"] = bill_pk
                    insert_action(conn, action, vote_type="floor")

                # Committee votes (unified roll_calls model)
                for cv in detail.get("committee_votes", []):
                    cname = cv.get("committee_name", "")
                    cid   = resolve_committee_id(conn, cname, session_id, "senate") if cname else None
                    insert_committee_vote(
                        conn,
                        bill_pk            = bill_pk,
                        session_id         = session_id,
                        chamber            = "senate",
                        vote_date          = cv.get("vote_date", ""),
                        motion_text        = cv.get("motion_text", ""),
                        yes_count          = cv.get("yes_count", 0),
                        no_count           = cv.get("no_count", 0),
                        present_count      = cv.get("present_count", 0),
                        absent_count       = cv.get("absent_count", 0),
                        passed             = cv.get("passed"),
                        committee_id       = cid,
                        committee_name_raw = cname or None,
                        source_url         = detail.get("source_url", ""),
                        parse_confidence   = "high" if cv["source"] == "modal" else "medium",
                    )
                if detail.get("committee_votes"):
                    log.info("%s: %d committee vote(s)", label, len(detail["committee_votes"]))

                # Bill text versions
                text_vid_map = _write_versions(
                    conn, bill_pk, label, session_id,
                    detail.get("text_versions", []), "text",
                    actions=detail.get("actions", []),
                )
                # Summary versions
                _write_versions(
                    conn, bill_pk, label, session_id,
                    detail.get("summary_versions", []), "summary",
                    actions=detail.get("actions", []),
                )

                # Back-fill title from PDF text only if page scrape found nothing
                if not detail.get("title"):
                    for key, val in text_vid_map.items():
                        if key.startswith("_title_") and isinstance(val, str):
                            conn.execute(
                                "UPDATE bills SET title=? WHERE bill_pk=? AND title IS NULL",
                                (val, bill_pk)
                            )
                            break

                # RSMo citations
                for vc, vid in text_vid_map.items():
                    if vc.startswith("_") or not isinstance(vid, int):
                        continue
                    row = conn.execute(
                        "SELECT full_text FROM bill_versions WHERE version_id=?", (vid,)
                    ).fetchone()
                    if not row or not row["full_text"]:
                        continue
                    for section in extract_rsmo_citations(row["full_text"]):
                        try:
                            chapter = int(section.split(".")[0])
                        except ValueError:
                            continue
                        conn.execute("""
                            INSERT OR IGNORE INTO bill_rsmo_citations
                                (bill_pk, version_id, chapter, section,
                                 citation_type, extracted_at)
                            VALUES (?, ?, ?, ?, 'references', ?)
                        """, (bill_pk, vid, chapter, section, now_utc()))

            # Ancillary docs — file I/O outside DB transaction
            anc = _download_ancillary_docs(
                label, session_id,
                detail.get("fiscal_note_urls", []),
                detail.get("amendments", []),
                detail.get("testimony_urls", []),
            )
            if any(anc.values()):
                log.debug("%s: fiscal=%d amend=%d testimony=%d",
                          label, anc["fiscal_notes"], anc["amendments"], anc["testimony"])

            saved += 1

        except Exception as e:
            errors += 1
            log.error("Bill %s: %s", stub.get("bill_label"), e, exc_info=True)

        time.sleep(SLEEP)
        if (i + 1) % 50 == 0:
            log.info("  %d/%d | saved=%d errors=%d", i + 1, len(bill_stubs), saved, errors)

    log.info("Senate bills done: saved=%d errors=%d / %d total",
             saved, errors, len(bill_stubs))