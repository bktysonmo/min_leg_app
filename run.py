"""
run.py — Master scraper runner
Usage:
    python3 run.py --help
    python3 run.py --house-members
    python3 run.py --senate-members
    python3 run.py --house-bills
    python3 run.py --senate-bills
    python3 run.py --journals [--chamber senate|house|both]
    python3 run.py --senate-actions [--days-back 3]
    python3 run.py --all
    python3 run.py --past-session 2025
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import DEFAULT_YEAR, DEFAULT_SESSION

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run")


def main():
    parser = argparse.ArgumentParser(description="MO Legislative scraper runner")
    parser.add_argument("--year",          type=int, default=DEFAULT_YEAR)
    parser.add_argument("--session",       type=str, default=DEFAULT_SESSION)
    parser.add_argument("--chamber",       type=str, default="both",
                        choices=["senate", "house", "both"])
    parser.add_argument("--days-back",     type=int, default=3,
                        help="For --senate-actions: how many days to look back")
    parser.add_argument("--past-session",  type=int, default=None,
                        metavar="YEAR",
                        help="Ingest House zip archive for a past session year")

    # Module flags
    parser.add_argument("--house-members",   action="store_true")
    parser.add_argument("--senate-members",  action="store_true")
    parser.add_argument("--house-bills",     action="store_true")
    parser.add_argument("--senate-bills",    action="store_true")
    parser.add_argument("--journals",        action="store_true")
    parser.add_argument("--senate-actions",  action="store_true")
    parser.add_argument("--all",             action="store_true",
                        help="Run full pipeline: members → bills → journals → actions")

    args = parser.parse_args()

    if not any([
        args.house_members, args.senate_members,
        args.house_bills, args.senate_bills,
        args.journals, args.senate_actions,
        args.all, args.past_session,
    ]):
        parser.print_help()
        return

    year    = args.year
    session = args.session

    def step(name, fn):
        log.info(f"{'='*50}")
        log.info(f"  {name}")
        log.info(f"{'='*50}")
        t = time.time()
        try:
            fn()
            log.info(f"  ✓  {name} done  ({time.time()-t:.1f}s)")
        except Exception as e:
            log.error(f"  ✗  {name} failed: {e}", exc_info=True)

    # ── Past session archive ──────────────────────────────────────────────
    if args.past_session:
        from scrapers.house_billsR import run_past_session
        step(f"House past session {args.past_session}",
             lambda: run_past_session(args.past_session))
        return

    # ── Individual modules ────────────────────────────────────────────────
    if args.all or args.senate_members:
        from scrapers.senate_members import run as run_senate_members
        step("Senate members", lambda: run_senate_members(year=year))

    if args.all or args.house_members:
        from scrapers.house_billsR import run_members
        step("House members", lambda: run_members(year=year, session=session))

    if args.all or args.senate_bills:
        from scrapers.senate_bills import run as run_senate_bills
        step("Senate bills", lambda: run_senate_bills(year=year, session=session))

    if args.all or args.house_bills:
        from scrapers.house_billsR import run as run_house_bills
        step("House bills", lambda: run_house_bills(year=year, session=session))

    if args.all or args.journals:
        from scrapers.journals import run as run_journals
        step(f"Journals ({args.chamber})",
             lambda: run_journals(year=year, session=session, chamber=args.chamber))

    # POTENTIALLY ADD BACK LATER if args.all or args.senate_actions:
    #    from scrapers.senate_actions import run as run_senate_actions
    #    step("Senate daily actions",
    #         lambda: run_senate_actions(year=year, session=session,
    #                                    days_back=args.days_back))

    log.info("Runner complete.")


if __name__ == "__main__":
    main()