# intelligence/coalition.py

from pathlib import Path
import sqlite3

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "mo_votes.db"


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def strongest_pairs(limit=25):
    conn = db()

    rows = conn.execute("""
        SELECT
            ma.member_a,
            ma.member_b,
            ma.shared_votes,
            ma.agreement_score,
            m1.full_name AS name_a,
            m1.party AS party_a,
            m2.full_name AS name_b,
            m2.party AS party_b
        FROM member_agreement ma
        JOIN members m1 ON m1.member_id = ma.member_a
        JOIN members m2 ON m2.member_id = ma.member_b
        ORDER BY ma.agreement_score DESC, ma.shared_votes DESC
        LIMIT ?
    """, (limit,)).fetchall()

    conn.close()
    return rows


def bipartisan_pairs(limit=25):
    conn = db()

    rows = conn.execute("""
        SELECT
            ma.member_a,
            ma.member_b,
            ma.shared_votes,
            ma.agreement_score,
            m1.full_name AS name_a,
            m1.party AS party_a,
            m2.full_name AS name_b,
            m2.party AS party_b
        FROM member_agreement ma
        JOIN members m1 ON m1.member_id = ma.member_a
        JOIN members m2 ON m2.member_id = ma.member_b
        WHERE m1.party != m2.party
        ORDER BY ma.agreement_score DESC, ma.shared_votes DESC
        LIMIT ?
    """, (limit,)).fetchall()

    conn.close()
    return rows


if __name__ == "__main__":
    print("\nStrongest coalitions:\n")

    for row in strongest_pairs(15):
        print(
            f"{row['name_a']} ({row['party_a']}) ↔ "
            f"{row['name_b']} ({row['party_b']}) | "
            f"{row['agreement_score']:.3f}"
        )