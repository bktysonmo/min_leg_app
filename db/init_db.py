"""
db/init_db.py
Initialize the database from schema.sql + extensions.sql, then seed static data.

Run once:
    python3 -m db.init_db

Script order matters:
    1. schema.sql     — core tables, indexes, policy tag weight seed
    2. extensions.sql — persistent intelligence tables + performance indexes
    (analytics.sql is NOT run here — it is run by refresh_views.py after scraping)
"""

import sqlite3
from pathlib import Path

from config.settings import DB_PATH
from config.sessions import SESSIONS

DB_DIR = Path(__file__).parent


def init(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    for sql_file in ["schema.sql", "extensions.sql"]:
        path = DB_DIR / sql_file
        conn.executescript(path.read_text())
        print(f"  Applied {sql_file}")

    # Seed sessions
    seeded = 0
    for year, code, label in SESSIONS:
        session_id = f"{year}{code}"
        conn.execute("""
            INSERT OR IGNORE INTO sessions (session_id, year, session_code, label)
            VALUES (?, ?, ?, ?)
        """, (session_id, year, code, label))
        seeded += 1

    conn.commit()
    conn.close()
    print(f"\nDatabase initialized at: {db_path}")
    print(f"Sessions seeded: {seeded}")


if __name__ == "__main__":
    init()