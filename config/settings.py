"""
config/settings.py
Central configuration. Override via .env file or environment variables.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = Path(os.getenv("DB_PATH", BASE_DIR / "mo_votes.db"))
STORAGE = Path(__file__).resolve().parent.parent / "storage"
LOG_DIR  = BASE_DIR / "logs"

# ── Current session defaults (override with --year / --session flags) ─────────
DEFAULT_YEAR    = int(os.getenv("DEFAULT_YEAR", 2026))
DEFAULT_SESSION = os.getenv("DEFAULT_SESSION", "R")   # R=Regular S=Special V=Veto

# ── Missouri Senate URLs ──────────────────────────────────────────────────────
SENATE_BASE = "https://www.senate.mo.gov"

SENATE_BILL_LIST    = SENATE_BASE + "/BillTracking/Bills/BillList"
#   params: year=YYYY, session=R
#   returns: HTML list of all senate bills for session

SENATE_BILL_DETAIL  = SENATE_BASE + "/BillTracking/Bills/BillInformation"
#   params: year=YYYY, billid=NNNN
#   returns: full bill page — sponsor, status, committee, amendments, summaries

SENATE_BILL_SEARCH  = SENATE_BASE + "/BillTracking/Bills/BillSearch"
#   params: Year=YYYY, district=NN, LastName=X
#   returns: member's sponsored bills

SENATE_DAILY_ACTIONS = SENATE_BASE + "/BillTracking/Actions/DailyActions"
#   params: year=YYYY, session=R, selectedId=YYYYMMDD (optional)
#   returns: list of action dates / actions on selected date

SENATE_MEMBER       = SENATE_BASE + "/Senators/Member"
#   params: id=N
#   returns: senator profile, committee assignments, party, district

SENATE_MEMBER_LIST  = SENATE_BASE + "/Senators"
#   returns: full list of senators with member IDs

SENATE_SPONSORED    = SENATE_BASE + "/BillTracking/Bills/sponsoredbills"
#   params: district=NN
SENATE_COSPONSORED  = SENATE_BASE + "/BillTracking/Bills/cosponsoredbills"
#   params: district=NN

SENATE_TRULY_AGREED = SENATE_BASE + "/BillTracking/BillStatus/TATFP"
#   params: year=YYYY, session=R
#   returns: bills that have passed both chambers

SENATE_GOV_ACTION   = SENATE_BASE + "/BillTracking/BillStatus/GovernorActiononTATFP"
#   params: year=YYYY, session=R

SENATE_AFFECTED_STATUTES = SENATE_BASE + "/crossreference"
#   NOTE: This URL returned 500 during verification — use bill-level
#   RSMo citation extraction from bill text instead as primary method.

SENATE_COMMITTEES   = SENATE_BASE + "/Committees"
SENATE_COMMITTEE_DETAIL = SENATE_BASE + "/Committees/CommitteeDetail"
#   params: year=YYYY, id=NNNN

# Amendment PDFs served via:
# SENATE_BASE + "/BillTracking/Bills/BillInformation?handler=AmendmentPdf&year=YYYY&amendmentId=NNN"

# ── Missouri House URLs ───────────────────────────────────────────────────────
HOUSE_BASE = "https://house.mo.gov"

HOUSE_BILL_LIST   = HOUSE_BASE + "/LegislationSP.aspx"
#   NOTE: Returns 403 without full browser headers. Use HOUSE_HEADERS below.
#   params: None (POST form) — scraper uses GET with session filter

HOUSE_BILL_DETAIL = HOUSE_BASE + "/bill.aspx"
#   params: bill=HBNNNn, year=YYYY, code=R
#   example: /bill.aspx?bill=HB2097&year=2026&code=R

HOUSE_MEMBER      = HOUSE_BASE + "/MemberDetails.aspx"
#   params: district=NNN, year=YYYY, code=R

HOUSE_MEMBER_LIST = HOUSE_BASE + "/Members.aspx"
#   params: year=YYYY, code=R

# ── Missouri Revisor (RSMo) ────────────────────────────────────────────────────
REVISOR_BASE = "https://revisor.mo.gov"

REVISOR_SECTION   = REVISOR_BASE + "/main/OneSection.aspx"
#   params: section=NNN.NNN
#   example: ?section=130.011
#   returns: clean HTML of single RSMo section with effective date

REVISOR_CHAPTER   = REVISOR_BASE + "/main/OneChapter.aspx"
#   params: chapter=NNN
#   returns: full chapter HTML

REVISOR_TOC       = REVISOR_BASE + "/main/StatuteIndex.aspx"
#   returns: title/chapter index — use to discover all chapter numbers

# ── Missouri Ethics Commission ─────────────────────────────────────────────────
MEC_BASE = "https://www.mec.mo.gov"

MEC_CF_SEARCH     = MEC_BASE + "/mec/campaign_finance/CFSearch.aspx"
#   POST form: search by committee name, MEC ID, or committee type
#   returns: HTML table of matching committees with MEC IDs

MEC_CF_ELECTION   = MEC_BASE + "/mec/Campaign_Finance/CF12_SearchElection.aspx"
#   params: election year + election date
#   returns: committees active in that election

MEC_COMMITTEE     = MEC_BASE + "/mec/Campaign_Finance/CFSearch.aspx"
#   After selecting a committee, tabs expose:
#   - Summary
#   - Contributions (CD1 section A)
#   - Expenditures (CD1 section B)
#   NOTE: MEC does NOT offer a bulk CSV download endpoint.
#   Scraper navigates per-committee report pages.
#   New reporting requirements took effect August 28, 2025 — verify field names.

# ── Missouri SOS ──────────────────────────────────────────────────────────────
SOS_BASE = "https://www.sos.mo.gov"
SOS_ELECTIONS = SOS_BASE + "/elections/resultsandstats/previouselections"
SOS_CANDIDATE_FILINGS = SOS_BASE + "/elections/candidate"

# ── HTTP behavior ─────────────────────────────────────────────────────────────
REQUEST_DELAY   = float(os.getenv("REQUEST_DELAY", 1.5))   # seconds between requests
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 30))
MAX_RETRIES     = int(os.getenv("MAX_RETRIES", 3))

# House site needs fuller headers to avoid 403
HOUSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MO-Leg-Research-Bot/1.0; "
        "+https://github.com/your-org/mo-leg-scraper)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ── Scrape cadence (seconds) ──────────────────────────────────────────────────
# Used by --watch mode scheduler
CADENCE = {
    "senate_bills":   7_200,   # 2 hours  — during session
    "house_bills":    7_200,
    "senate_actions": 86_400,  # daily
    "senate_members": 604_800, # weekly
    "house_members":  604_800,
    "journals":       86_400,
    "rsmo":           86_400,  # triggered by enactment events primarily
    "mec":            604_800, # weekly
}

# ── Session type codes ────────────────────────────────────────────────────────
SESSION_TYPES = {
    "R": "Regular",
    "S": "Special",
    "V": "Veto",
}

# Missouri chamber sizes (for vote validation)
SENATE_SIZE = 34
HOUSE_SIZE  = 163