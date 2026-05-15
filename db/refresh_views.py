"""
db/refresh_views.py
Rebuild all materialized analytics tables.

Called automatically by run.py --watch after each scrape cycle.
Standalone:  python3 -m db.refresh_views [--db path/to/mo_votes.db]
"""

import argparse
import logging
import sqlite3
import time
from pathlib import Path

log = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).resolve().parent.parent
DB_PATH   = BASE_DIR / "mo_votes.db"

# analytics.sql only — extensions.sql tables are updated by intelligence pipelines
SQL_FILES = [
    BASE_DIR / "db" / "analytics.sql",
]

# All analytics tables (for the post-refresh count report)
ANALYTICS_TABLES = [
    "bill_metrics",
    "member_metrics",
    "cross_aisle_votes",
    "member_agreement",
    "cosponsor_network",
    "committee_vote_summary",
    "member_tag_alignment",
]

# Intelligence tables (not rebuilt here, but counted for health check)
INTELLIGENCE_TABLES = [
    "ideology_scores",
    "sponsorship_scores",
    "bill_lineage",
    "caucus_clusters",
]


def refresh(db_path: str = str(DB_PATH), verbose: bool = True) -> dict[str, float]:
    """
    Execute all analytics SQL files in order.
    Returns dict of {filename: elapsed_seconds}.
    """
    timings: dict[str, float] = {}
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = OFF")  # safe for DROP + CREATE cycle

    for sql_path in SQL_FILES:
        if not sql_path.exists():
            log.warning("SQL file not found: %s", sql_path)
            continue
        t = time.perf_counter()
        try:
            conn.executescript(sql_path.read_text())
            conn.commit()
            elapsed = time.perf_counter() - t
            timings[sql_path.name] = elapsed
            if verbose:
                log.info("  %-30s %.1fs", sql_path.name, elapsed)
        except sqlite3.Error as exc:
            log.error("  %s FAILED: %s", sql_path.name, exc)
            conn.rollback()

    conn.close()
    return timings


def get_table_counts(db_path: str = str(DB_PATH)) -> dict[str, int]:
    """Return row counts for analytics and intelligence tables."""
    conn = sqlite3.connect(db_path)
    counts: dict[str, int] = {}
    for table in ANALYTICS_TABLES + INTELLIGENCE_TABLES:
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            counts[table] = n
        except sqlite3.OperationalError:
            counts[table] = -1  # table missing
    conn.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh analytics tables")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to SQLite database")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s — %(message)s",
        datefmt="%H:%M:%S",
    )

    log.info("Refreshing analytics tables…")
    timings = refresh(db_path=args.db)
    total = sum(timings.values())
    log.info("Done in %.1fs", total)

    log.info("\nTable counts after refresh:")
    counts = get_table_counts(db_path=args.db)

    log.info("  [analytics]")
    for table in ANALYTICS_TABLES:
        n = counts.get(table, -1)
        status = f"{n:>10,}" if n >= 0 else "    (missing)"
        log.info("    %-35s %s", table, status)

    log.info("  [intelligence — not rebuilt here]")
    for table in INTELLIGENCE_TABLES:
        n = counts.get(table, -1)
        status = f"{n:>10,}" if n >= 0 else "    (missing)"
        log.info("    %-35s %s", table, status)


if __name__ == "__main__":
    main()