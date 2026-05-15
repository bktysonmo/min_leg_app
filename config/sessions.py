"""
config/sessions.py
Known Missouri legislative sessions for historical scraping.
Each entry: (year, session_code, label)
"""

SESSIONS = [
    (2026, "R", "103rd General Assembly, 2nd Regular Session"),
    (2025, "R", "103rd General Assembly, 1st Regular Session"),
    (2024, "R", "102nd General Assembly, 2nd Regular Session"),
    (2023, "R", "102nd General Assembly, 1st Regular Session"),
    (2022, "R", "101st General Assembly, 2nd Regular Session"),
    (2021, "R", "101st General Assembly, 1st Regular Session"),
    (2020, "R", "100th General Assembly, 2nd Regular Session"),
    (2019, "R", "100th General Assembly, 1st Regular Session"),
    (2018, "R", "99th General Assembly, 2nd Regular Session"),
    (2017, "R", "99th General Assembly, 1st Regular Session"),
    (2016, "R", "98th General Assembly, 2nd Regular Session"),
    (2015, "R", "98th General Assembly, 1st Regular Session"),
]

# Senate bill number ranges by year (for targeted scraping)
# These are approximate upper bounds — scraper stops when 404s exceed threshold
BILL_NUMBER_RANGES = {
    2026: {"SB": (834, 1200), "HB": (1200, 3000)},
    2025: {"SB": (1,   900),  "HB": (1,   2500)},
    2024: {"SB": (1,   900),  "HB": (1,   2500)},
    2023: {"SB": (1,   900),  "HB": (1,   2500)},
}

def get_session(year: int, code: str = "R") -> dict | None:
    for y, c, label in SESSIONS:
        if y == year and c == code:
            return {"year": y, "code": c, "label": label}
    return None

def all_years() -> list[int]:
    return sorted({s[0] for s in SESSIONS}, reverse=True)