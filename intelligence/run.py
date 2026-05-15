"""
intelligence/run.py
Run intelligence pipeline modules from the command line.

Usage:
    python3 -m intelligence.run --all
    python3 -m intelligence.run --coalition
    python3 -m intelligence.run --ideology
    python3 -m intelligence.run --sponsorship
    python3 -m intelligence.run --lineage
    python3 -m intelligence.run --refresh-analytics
"""

import argparse
import logging
import sys
import time
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("intelligence.run")


def run_coalition():
    log.info("Running coalition analysis…")
    from intelligence.coalition import strongest_pairs, bipartisan_pairs
    strong     = strongest_pairs(25)
    bipartisan = bipartisan_pairs(25)
    log.info(f"  Strongest pairs:   {len(strong)}")
    log.info(f"  Bipartisan pairs:  {len(bipartisan)}")
    log.info("Coalition analysis complete (read-only summary).")
    log.info("  Full agreement matrix is built by db/analytics.sql — run --refresh-analytics.")


def run_ideology():
    log.info("Running ideology model…")
    t = time.time()
    try:
        from intelligence.ideology import run_ideology_model
        n = run_ideology_model(str(DB_PATH))
        log.info(f"  Ideology profiles written: {n}  ({time.time()-t:.1f}s)")
    except ImportError as e:
        log.error(f"Ideology model requires scikit-learn and numpy: {e}")
        log.error("  pip install scikit-learn numpy pandas")


def run_sponsorship():
    log.info("Running sponsorship network…")
    t = time.time()
    try:
        from intelligence.sponsorship import compute_metrics
        compute_metrics(str(DB_PATH))
        log.info(f"  Sponsorship metrics written  ({time.time()-t:.1f}s)")
    except ImportError as e:
        log.error(f"Sponsorship network requires networkx: {e}")
        log.error("  pip install networkx")


def run_lineage():
    log.info("Running bill lineage / resurrection detector…")
    t = time.time()
    try:
        from intelligence.resurrection import run_lineage
        run_lineage(str(DB_PATH))
        log.info(f"  Lineage pairs written  ({time.time()-t:.1f}s)")
    except ImportError as e:
        log.error(f"Lineage detector requires datasketch: {e}")
        log.error("  pip install datasketch")


def run_analytics_refresh():
    log.info("Refreshing materialized analytics tables…")
    import sqlite3
    sql_path = Path(__file__).resolve().parent.parent / "db" / "analytics.sql"
    if not sql_path.exists():
        log.error(f"analytics.sql not found at {sql_path}")
        return
    t = time.time()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(sql_path.read_text())
    conn.commit()
    conn.close()
    log.info(f"  Analytics refresh complete  ({time.time()-t:.1f}s)")


def main():
    parser = argparse.ArgumentParser(description="MO Leg intelligence pipeline")
    parser.add_argument("--all",              action="store_true", help="Run all modules + analytics refresh")
    parser.add_argument("--coalition",        action="store_true", help="Coalition analysis summary")
    parser.add_argument("--ideology",         action="store_true", help="PCA ideology model")
    parser.add_argument("--sponsorship",      action="store_true", help="Co-sponsorship network metrics")
    parser.add_argument("--lineage",          action="store_true", help="Bill resurrection / lineage")
    parser.add_argument("--refresh-analytics", action="store_true", help="Rebuild materialized analytics tables")
    args = parser.parse_args()

    if not any(vars(args).values()):
        parser.print_help()
        return

    t_start = time.time()

    if args.all or args.refresh_analytics:
        run_analytics_refresh()
    if args.all or args.coalition:
        run_coalition()
    if args.all or args.ideology:
        run_ideology()
    if args.all or args.sponsorship:
        run_sponsorship()
    if args.all or args.lineage:
        run_lineage()

    log.info(f"Pipeline complete  ({time.time()-t_start:.1f}s total)")


if __name__ == "__main__":
    main()