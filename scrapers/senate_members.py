"""scrapers/senate_members.py
Scrape all Missouri Senate member profiles and committee assignments.
Sources verified:
  - senate.mo.gov/Senators — full member list with ID links
  - senate.mo.gov/Senators/Member?id=N — individual profiles
    Contains: party, district, counties, first elected, committees"""
import re
import logging
from bs4 import BeautifulSoup
from config.settings import SENATE_BASE, DEFAULT_YEAR
from utils.http import fetch_html
from db.db import get_db, upsert_member, now_utc
log = logging.getLogger(__name__)
MEMBER_LIST_URL = SENATE_BASE + "/Senators"
MEMBER_URL      = SENATE_BASE + "/Senators/Member"
def scrape_member_list() -> list[dict]:
    html = fetch_html(MEMBER_LIST_URL)
    if not html:
        log.error("Failed to fetch senator list")
        return []
    soup = BeautifulSoup(html, "lxml")
    members = []
    for link in soup.find_all("a", href=re.compile(r"/Senators/Member\?id=\d+")):
        m = re.search(r"id=(\d+)", link["href"])
        if not m:
            continue
        member_id = int(m.group(1))
        img = link.find("img")
        if img and img.get("alt"):
            raw_name = img["alt"].strip()
        else:
            raw_name = link.get_text(separator="\n", strip=True).split("\n")[0].strip()
        name = re.sub(r"^Senator\s+", "", raw_name).strip()
        members.append({"member_id": member_id, "name_raw": name})
    log.info(f"Found {len(members)} senators in list")
    return members
def scrape_member_detail(member_id: int) -> dict | None:
    """
      - Page <title> is "- Senators" (useless)
      - Senator name is in a <p> or standalone text block after the photo img:
          "Senator Doug Beck"
      - Photo: <img src="/WebPhotos/SenatorPortraits/Beck01.jpg" alt="Senator Doug Beck">
      - Party line: "Minority Floor Leader - Democrat"
        Pattern: optional role text, then " - Democrat/Republican/Independent"
      - District: "District\nDistrict 1"  (label then value, both containing "District")
      - First Elected: "First Elected\n2020"
      - Counties: "Counties\nParts of St. Louis County"
      - Phone: in Contact Information block, e.g. "573-751-0220"
      - Committee links: href contains "CommitteeDetail?year=YYYY&id=NNNN"
    """
    html = fetch_html(MEMBER_URL, params={"id": member_id})
    if not html:
        return None

    soup = BeautifulSoup(html, "lxml")
    data = {
        "member_id":    member_id,
        "chamber":      "senate",
        "last_scraped": now_utc(),
    }
    portrait_img = soup.find("img", src=re.compile(r"WebPhotos/SenatorPortraits"))
    if portrait_img and portrait_img.get("alt"):
        full_name = re.sub(r"^Senator\s+", "", portrait_img["alt"]).strip()
    else:
        full_name = ""
        for tag in soup.find_all(string=re.compile(r"^Senator\s+\w+\s+\w")):
            candidate = tag.strip()
            if re.match(r"Senator\s+\w+", candidate):
                full_name = re.sub(r"^Senator\s+", "", candidate).strip()
                break
    data["full_name"] = full_name
    if full_name:
        parts = full_name.split()
        data["first_name"] = parts[0] if parts else ""
        data["last_name"]  = parts[-1] if len(parts) > 1 else full_name
    else:
        data["first_name"] = ""
        data["last_name"]  = ""
    if portrait_img:
        src = portrait_img.get("src", "")
        data["photo_url"] = SENATE_BASE + src if src.startswith("/") else src
    page_text = soup.get_text(separator="\n")

    # ── Party ──────────────────────────────────────────────────────────────
    party_m = re.search(
        r"(?:^|\n)\s*(?:[^\n]*?[-–]\s*)?(Republican|Democrat|Independent)\s*(?:\n|$)",
        page_text, re.MULTILINE
    )
    if party_m:
        p = party_m.group(1)
        data["party"] = "R" if p == "Republican" else ("D" if p == "Democrat" else "I")
    else:
        data["party"] = None

    # ── District ───────────────────────────────────────────────────────────
    dist_matches = re.findall(r"District\s+(\d+)", page_text)
    if dist_matches:
        data["district"] = int(dist_matches[-1])

    # ── First Elected ──────────────────────────────────────────────────────
    elect_m = re.search(r"First\s+Elected\s*\n\s*(\d{4})\b", page_text)
    if elect_m:
        data["first_elected"] = elect_m.group(1)

    # ── Term Ends ──────────────────────────────────────────────────────────
    term_m = re.search(r"Term\s+Ends?\s*\n\s*(\d{4})\b", page_text)
    if term_m:
        data["term_end"] = term_m.group(1)

    # ── Counties ───────────────────────────────────────────────────────────
    _KNOWN_LABELS = r"(?:Phone|Term|First|District|Committee|Party|Email|Contact)"
    county_block = re.search(
        rf"Counties\s*\n((?:(?!{_KNOWN_LABELS})\S[^\n]*\n?)+)",
        page_text
    )
    if county_block:
        county_lines = [l.strip() for l in county_block.group(1).splitlines() if l.strip()]
        data["county_list"] = ", ".join(county_lines) if county_lines else None
    else:
        county_m = re.search(r"Counties\s*\n\s*([^\n]+)", page_text)
        if county_m:
            data["county_list"] = county_m.group(1).strip()

    # ── Phone ──────────────────────────────────────────────────────────────
    phone_m = re.search(r"(\d{3}[-.\s]\d{3}[-.\s]\d{4})", page_text)
    if phone_m:
        data["phone"] = phone_m.group(1)

    # ── Email ──────────────────────────────────────────────────────────────
    email_tag = soup.find("a", href=re.compile(r"^mailto:", re.I))
    if email_tag:
        data["email"] = email_tag["href"].replace("mailto:", "").strip()

# Committee assignments — links to CommitteeDetail
    committees = []
    for link in soup.find_all("a", href=re.compile(r"CommitteeDetail")):
        c_m = re.search(r"id=(\d+).*?year=(\d+)", link["href"])
        if c_m:
            committees.append({
                "committee_id": int(c_m.group(1)),
                "year":         int(c_m.group(2)),
                "name":         link.get_text(strip=True),
                "role":         _extract_role(link),
            })
    data["committees"] = committees
    # Defaults for missing fields
    data.setdefault("first_name", "")
    data.setdefault("last_name", "")
    data.setdefault("full_name", "")
    data.setdefault("party", None)
    data.setdefault("district", None)
    data.setdefault("county_list", None)
    data.setdefault("first_elected", None)
    data.setdefault("term_end", None)
    data.setdefault("phone", None)
    data.setdefault("email", None)
    return data
def _extract_role(link_tag) -> str:
    """Extract Chair/Vice-Chair role from committee link context."""
    text = link_tag.get_text(strip=True)
    parent_text = link_tag.parent.get_text(strip=True) if link_tag.parent else ""
    if "Chair" in text or "Chair" in parent_text:
        if "Vice" in text or "Vice" in parent_text:
            return "Vice-Chair"
        return "Chair"
    return "Member"
def run(year: int = DEFAULT_YEAR):
    """Scrape all senators and persist to database."""
    log.info("Starting senate member scrape")
    members_list = scrape_member_list()
    if not members_list:
        log.error("No members found — aborting")
        return
    scraped = 0
    with get_db() as conn:
        session_id = f"{year}R"
        for stub in members_list:
            mid = stub["member_id"]
            detail = scrape_member_detail(mid)
            if not detail:
                log.warning(f"Could not fetch member {mid}")
                continue
            # If scraper couldn't extract name from detail page, use list name
            if not detail.get("full_name") and stub.get("name_raw"):
                name = stub["name_raw"]
                detail["full_name"] = name
                parts = name.split()
                detail["first_name"] = parts[0] if parts else ""
                detail["last_name"]  = parts[-1] if len(parts) > 1 else name
            upsert_member(conn, detail)
            # Seed name variant from last name (for journal matching)
            if detail.get("last_name"):
                conn.execute("""
                    INSERT OR IGNORE INTO member_name_variants
                        (member_id, name_variant, source)
                    VALUES (?, ?, 'scraper')
                """, (mid, detail["last_name"].upper()))
            # Committee assignments
            for c in detail.get("committees", []):
                # Upsert committee
                conn.execute("""
                    INSERT OR IGNORE INTO committees
                        (committee_id, session_id, chamber, name)
                    VALUES (?, ?, 'senate', ?)
                """, (c["committee_id"], session_id, c["name"]))
                conn.execute("""
                    INSERT OR IGNORE INTO committee_assignments
                        (member_id, session_id, committee_id, role)
                    VALUES (?, ?, ?, ?)
                """, (mid, session_id, c["committee_id"], c["role"]))
            scraped += 1
    log.info(f"Senate members scraped and saved: {scraped}")