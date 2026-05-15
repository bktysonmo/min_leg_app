"""
scrapers/senate_actions.py
Scrape the Missouri Senate Daily Actions log.

Source (verified):
  senate.mo.gov/BillTracking/Actions/DailyActions
  Index page: list of <a href="...?selectedId=YYYYMMDD&year=YYYY&session=R">
  Date page:  bills acted on that day, each block containing a bill link
              followed by a table or paragraph with action text.

Strategy for action text extraction:
  1. Walk DOM siblings of the bill link's parent looking for <table> → <p> → <div>
  2. Grandparent text fallback (strips bill label, cuts at next bill pattern)

House bills that crossed to the Senate appear in these action logs too
(e.g. "HB 2097 — Hearing Scheduled S Agriculture...") and are handled
by setting chamber based on the bill type prefix.

Changes from prior version:
  - insert_action() now returns (is_new, action_id); callers updated
  - STATUS_KEYWORDS expanded with veto override and governor actions
  - scrape_actions_for_date() more robustly strips journal page refs before
    storing action_text (avoids "S42" noise at start of stored strings)
  - days_back=-1 mode documented and unchanged
"""

import re
import logging
from datetime import date, datetime, timedelta
from bs4 import BeautifulSoup

from config.settings import SENATE_DAILY_ACTIONS, DEFAULT_YEAR, DEFAULT_SESSION
from utils.http import fetch_html
from db.db import get_db, insert_action, log_change, now_utc, get_bill_pk

log = logging.getLogger(__name__)

# Ordered most-specific → least-specific so the first match wins
STATUS_KEYWORDS: list[tuple[str, str]] = [
    (r"truly agreed",               "Truly Agreed and Finally Passed"),
    (r"signed by governor",         "Signed by Governor"),
    (r"vetoed by governor",         "Vetoed by Governor"),
    (r"veto\s+override",            "Veto Override"),
    (r"delivered to governor",      "Delivered to Governor"),
    (r"truly agreed",               "Truly Agreed and Finally Passed"),
    (r"third reading and passed",   "Third Reading and Passed"),
    (r"third reading",              "Third Reading"),
    (r"perfected",                  "Perfected"),
    (r"second reading",             "Second Reading"),
    (r"referred to .+committee",    "Referred to Committee"),
    (r"hearing scheduled",          "Hearing Scheduled"),
    (r"do pass",                    "Do Pass"),
    (r"tabled",                     "Tabled"),
    (r"withdrawn",                  "Withdrawn"),
]

_BILL_LINK_PAT = re.compile(r"BillInformation\?year=\d+&billid=\d+")
_BILL_LABEL_PAT = re.compile(r"((?:SB|HB|SCR|HCR|SJR|HJR|SR|HR)\s*\d+)", re.IGNORECASE)
# Journal page refs embedded in action text — strip these out before storing
_JOURNAL_PAGE_PAT = re.compile(r"\b([SH]\d{1,4})\b")


def scrape_action_dates(year: int, session: str = "R") -> list[str]:
    """Return sorted list of YYYYMMDD date strings (most recent first)."""
    html = fetch_html(SENATE_DAILY_ACTIONS, params={"year": year, "session": session})
    if not html:
        log.error("Failed to fetch Senate daily actions index")
        return []

    soup  = BeautifulSoup(html, "lxml")
    dates: set[str] = set()
    for link in soup.find_all("a", href=re.compile(r"selectedId=\d{8}")):
        m = re.search(r"selectedId=(\d{8})", link["href"])
        if m:
            dates.add(m.group(1))

    result = sorted(dates, reverse=True)
    log.info("Senate action dates: %d found", len(result))
    return result


def scrape_actions_for_date(
    date_str: str,
    year: int,
    session: str = "R",
) -> list[dict]:
    """
    Scrape all bill actions for one day (YYYYMMDD).
    Returns list of action dicts ready for insert_action().
    """
    html = fetch_html(SENATE_DAILY_ACTIONS, params={
        "selectedId": date_str,
        "year":       year,
        "session":    session,
    })
    if not html:
        return []

    # Convert YYYYMMDD → M/D/YYYY (no zero-padding)
    try:
        d = datetime.strptime(date_str, "%Y%m%d")
        formatted_date = f"{d.month}/{d.day}/{d.year}"
    except ValueError:
        formatted_date = date_str

    soup    = BeautifulSoup(html, "lxml")
    actions = []

    for bill_link in soup.find_all("a", href=_BILL_LINK_PAT):
        raw_label = bill_link.get_text(strip=True)
        label_m   = _BILL_LABEL_PAT.match(raw_label)
        if not label_m:
            continue

        bill_label  = label_m.group(1).strip()
        type_num_m  = re.match(r"([A-Z]+)\s*(\d+)", bill_label)
        if not type_num_m:
            continue

        bill_type   = type_num_m.group(1)
        bill_number = int(type_num_m.group(2))
        chamber     = "senate" if bill_type.startswith("S") else "house"

        # ── Locate action text ─────────────────────────────────────────────
        action_text  = ""
        journal_page = None
        parent       = bill_link.find_parent()

        if parent:
            for sibling in parent.next_siblings:
                tag_name = getattr(sibling, "name", None)
                if tag_name == "table":
                    cells       = [td.get_text(strip=True) for td in sibling.find_all("td")]
                    action_text = " ".join(c for c in cells if c)
                    break
                if tag_name in ("p", "div"):
                    action_text = sibling.get_text(strip=True)
                    break
                if tag_name == "a":
                    break  # next bill link — stop

        # Grandparent fallback
        if not action_text and parent:
            gp = parent.parent
            if gp:
                full = gp.get_text(separator=" ", strip=True)
                full = full.replace(raw_label, "").strip()
                # Cut at the next bill-like label to avoid spill into next bill's text
                full = re.split(r"\b(?:SB|HB|SCR|HCR|SJR|HJR)\s+\d+\b", full)[0]
                action_text = full.strip()

        action_text = re.sub(r"\s+", " ", action_text).strip()
        if not action_text or len(action_text) < 4:
            continue

        # Extract and then strip journal page from action_text
        jp_m = _JOURNAL_PAGE_PAT.search(action_text)
        if jp_m:
            journal_page = jp_m.group(1)
            # Only strip it if it looks like a standalone page ref (not part of a word)
            action_text = re.sub(
                r"\b" + re.escape(journal_page) + r"\b", "", action_text
            ).strip()
            action_text = re.sub(r"\s+", " ", action_text).strip()

        actions.append({
            "bill_type":    bill_type,
            "bill_number":  bill_number,
            "bill_label":   bill_label,
            "action_date":  formatted_date,
            "action_text":  action_text,
            "journal_page": journal_page,
            "chamber":      chamber,
        })

    log.debug("Actions for %s: %d", date_str, len(actions))
    return actions


def _infer_status(action_text: str) -> str | None:
    lower = action_text.lower()
    for pattern, status in STATUS_KEYWORDS:
        if re.search(pattern, lower):
            return status
    return None


def run(
    year: int     = DEFAULT_YEAR,
    session: str  = DEFAULT_SESSION,
    days_back: int = 3,
):
    """
    Scrape recent Senate daily actions and persist new rows.

    days_back=3   — rolling window (covers weekends/holidays)
    days_back=-1  — full history rebuild (all available dates)
    """
    log.info("Senate daily actions: %d/%s days_back=%d", year, session, days_back)
    session_id = f"{year}{session}"

    all_dates = scrape_action_dates(year, session)
    if not all_dates:
        log.error("No action dates found — check SENATE_DAILY_ACTIONS URL")
        return

    if days_back >= 0:
        cutoff       = date.today() - timedelta(days=days_back)
        target_dates = [d for d in all_dates
                        if datetime.strptime(d, "%Y%m%d").date() >= cutoff]
    else:
        target_dates = all_dates

    log.info("Processing %d action date(s)", len(target_dates))

    total_new = 0
    with get_db() as conn:
        for date_str in target_dates:
            daily = scrape_actions_for_date(date_str, year, session)
            for action in daily:
                chamber = "senate" if action["bill_type"].startswith("S") else "house"
                bill_pk = get_bill_pk(
                    conn, session_id,
                    chamber,
                    action["bill_type"],
                    action["bill_number"],
                )
                if not bill_pk:
                    log.debug("Bill not in DB yet: %s", action["bill_label"])
                    continue

                action["bill_pk"] = bill_pk
                is_new, _action_id = insert_action(conn, action)

                if is_new:
                    total_new += 1

                    conn.execute("""
                        UPDATE bills SET last_action_date=?, last_scraped=?
                        WHERE bill_pk=?
                    """, (action["action_date"], now_utc(), bill_pk))

                    implied = _infer_status(action["action_text"])
                    if implied:
                        # Fetch current status to avoid redundant change_events
                        cur_status = conn.execute(
                            "SELECT current_status FROM bills WHERE bill_pk=?",
                            (bill_pk,)
                        ).fetchone()
                        old_status = cur_status["current_status"] if cur_status else None

                        if old_status != implied:
                            log_change(
                                conn, "bill_status_change", "bill",
                                str(bill_pk), "current_status",
                                old_status, implied,
                                scraper="senate_actions",
                            )
                            conn.execute(
                                "UPDATE bills SET current_status=? WHERE bill_pk=?",
                                (implied, bill_pk),
                            )

    log.info("Senate daily actions: %d new action(s) recorded", total_new)