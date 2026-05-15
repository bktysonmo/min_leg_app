"""
intelligence/resurrection.py

Detects language lineage across bills:
  - Whole-bill reintroduction across sessions (Jaccard ≥ threshold)
  - Partial reuse / amendment adoption (containment ≥ contain_threshold)
  - Exact section matches (SHA-1 hash collision)

Populates:
  - bill_language_fragments  (section-level index)
  - language_lineage         (with authorship attribution)
"""

import re
import json
import hashlib
import sqlite3
from datetime import datetime

from datasketch import MinHash, MinHashLSH


# ─────────────────────────────────────────────────────────────────────────────
# Text utilities
# ─────────────────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z]{4,}", normalize(text)))


def shingle(text: str, k: int = 5) -> set[str]:
    """
    Character-level k-shingles on normalized text.
    """
    n = normalize(text)
    if len(n) < k:
        return {n}
    return {n[i:i+k] for i in range(len(n) - k + 1)}


def content_hash(text: str) -> str:
    return hashlib.sha1(normalize(text).encode()).hexdigest()[:16]


def make_minhash(text: str, num_perm: int = 128) -> MinHash:
    m = MinHash(num_perm=num_perm)
    # Combine word tokens + shingles for richer signal
    features = tokenize(text) | shingle(text, k=6)
    for f in features:
        m.update(f.encode("utf8"))
    return m


def containment(set_a: set, set_b: set) -> float:
    """
    How much of set_a is contained in set_b.
    """
    if not set_a:
        return 0.0
    return len(set_a & set_b) / len(set_a)


# ─────────────────────────────────────────────────────────────────────────────
# Fragment splitting
# ─────────────────────────────────────────────────────────────────────────────

def split_into_sections(full_text: str) -> list[str]:
    """
    Split bill full_text into logical sections.
    Prefs statutory section headers; falls back to paragraphs.
    """
    # Missouri bill sections typically start with "Section X." or "X."
    section_re = re.compile(
        r'(?:^|\n)(?:Section\s+)?\d+[\.\)]\s', re.IGNORECASE
    )
    splits = [m.start() for m in section_re.finditer(full_text)]

    if len(splits) >= 2:
        sections = []
        for i, start in enumerate(splits):
            end = splits[i + 1] if i + 1 < len(splits) else len(full_text)
            chunk = full_text[start:end].strip()
            if len(chunk) > 80:   # ignore stub sections
                sections.append(chunk)
        return sections

    # Fallback: paragraph splitting (double newline)
    paragraphs = [p.strip() for p in re.split(r'\n{2,}', full_text)]
    return [p for p in paragraphs if len(p) > 80]


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_bills(conn: sqlite3.Connection) -> list[dict]:
    """
    Load bills with their latest full_text version and primary sponsor.
    Falls back to title+short_desc if no full_text is available yet.
    """
    rows = conn.execute("""
        SELECT
            b.bill_pk,
            b.session_id,
            b.bill_label,
            b.title,
            b.short_desc,
            bv.version_id,
            bv.full_text,
            bs.member_id   AS primary_member_id
        FROM bills b
        LEFT JOIN bill_versions bv ON bv.version_id = (
            -- Latest version by date, preferring non-null full_text
            SELECT version_id FROM bill_versions
            WHERE bill_pk = b.bill_pk
              AND full_text IS NOT NULL
            ORDER BY version_date DESC
            LIMIT 1
        )
        LEFT JOIN bill_sponsors bs ON bs.bill_pk = b.bill_pk
                                   AND bs.sponsor_type = 'primary'
    """).fetchall()

    cols = [
        "bill_pk", "session_id", "bill_label", "title",
        "short_desc", "version_id", "full_text", "primary_member_id"
    ]
    return [dict(zip(cols, r)) for r in rows]


def load_all_sponsors(conn: sqlite3.Connection) -> dict[int, list[int]]:
    """Returns {bill_pk: [member_id, ...]} for all sponsors."""
    rows = conn.execute(
        "SELECT bill_pk, member_id FROM bill_sponsors ORDER BY bill_pk"
    ).fetchall()
    result: dict[int, list[int]] = {}
    for bill_pk, member_id in rows:
        result.setdefault(bill_pk, []).append(member_id)
    return result


def index_fragments(conn: sqlite3.Connection, bills: list[dict]) -> dict[int, list[dict]]:
    """
    For each bill with full_text, split into sections and upsert into
    bill_language_fragments. Returns {bill_pk: [fragment_dict, ...]}.
    """
    now = datetime.utcnow().isoformat()
    fragment_map: dict[int, list[dict]] = {}

    for bill in bills:
        full_text = bill.get("full_text") or ""
        if not full_text:
            continue

        sections = split_into_sections(full_text)
        frags = []
        offset = 0

        for idx, section_text in enumerate(sections):
            chash = content_hash(section_text)
            char_offset = full_text.find(section_text, offset)
            if char_offset == -1:
                char_offset = offset
            offset = char_offset + len(section_text)

            conn.execute("""
                INSERT OR IGNORE INTO bill_language_fragments
                    (bill_pk, version_id, session_id, fragment_index,
                     fragment_type, char_offset, char_length,
                     fragment_text, content_hash)
                VALUES (?, ?, ?, ?, 'section', ?, ?, ?, ?)
            """, (
                bill["bill_pk"], bill["version_id"], bill["session_id"],
                idx, char_offset, len(section_text), section_text, chash
            ))

            frag_id = conn.execute(
                "SELECT fragment_id FROM bill_language_fragments "
                "WHERE version_id=? AND fragment_index=?",
                (bill["version_id"], idx)
            ).fetchone()

            frags.append({
                "fragment_id": frag_id[0] if frag_id else None,
                "fragment_text": section_text,
                "content_hash": chash,
                "tokens": tokenize(section_text),
                "shingles": shingle(section_text, k=6),
            })

        fragment_map[bill["bill_pk"]] = frags

    conn.commit()
    return fragment_map


# ─────────────────────────────────────────────────────────────────────────────
# Core lineage detection
# ─────────────────────────────────────────────────────────────────────────────

def run_lineage(
    db_path: str,
    jaccard_threshold: float = 0.75,    # whole-bill reintroduction
    contain_threshold: float = 0.60,    # partial reuse / amendment absorption
    num_perm: int = 128,
    lsh_threshold: float = 0.45,        # LSH band threshold (cast wide, filter tight)
):
    conn = sqlite3.connect(db_path)
    now = datetime.utcnow().isoformat()

    bills = load_bills(conn)
    all_sponsors = load_all_sponsors(conn)

    # ── Build bill-level text and MinHash ──────────────────────────────────
    # Use full_text when available; fall back to title + short_desc
    bill_texts: dict[int, str] = {}
    bill_tokens: dict[int, set] = {}
    bill_hashes: dict[int, MinHash] = {}

    for b in bills:
        text = b["full_text"] or f"{b['title']} {b['short_desc']}"
        bill_texts[b["bill_pk"]] = text
        bill_tokens[b["bill_pk"]] = tokenize(text) | shingle(text, k=6)
        bill_hashes[b["bill_pk"]] = make_minhash(text, num_perm)

    # ── Index fragments (section-level) ───────────────────────────────────
    fragment_map = index_fragments(conn, bills)

    # ── Build exact-hash index for fragment-level O(1) matching ──────────
    # hash -> [(bill_pk, fragment)]
    hash_index: dict[str, list[tuple[int, dict]]] = {}
    for bill_pk, frags in fragment_map.items():
        for frag in frags:
            hash_index.setdefault(frag["content_hash"], []).append((bill_pk, frag))

    # ── LSH for approximate whole-bill matching ───────────────────────────
    lsh = MinHashLSH(threshold=lsh_threshold, num_perm=num_perm)
    for b in bills:
        pk = b["bill_pk"]
        lsh.insert(str(pk), bill_hashes[pk])

    # ── Build lookup structures ────────────────────────────────────────────
    pk_to_bill: dict[int, dict] = {b["bill_pk"]: b for b in bills}

    lineage_rows: list[tuple] = []
    seen: set[tuple] = set()   # (source_pk, target_pk, source_frag_id, target_frag_id)

    def add_row(
        source_bill_pk, source_version_id, source_fragment_id,
        source_session_id, source_member_id,
        target_bill_pk, target_version_id, target_fragment_id,
        target_session_id, target_member_id,
        match_type, granularity,
        similarity_score, containment_score, method,
        all_source_members, all_target_members,
    ):
        key = (source_bill_pk, target_bill_pk, source_fragment_id, target_fragment_id)
        if key in seen:
            return
        seen.add(key)
        lineage_rows.append((
            source_bill_pk, source_version_id, source_fragment_id,
            source_session_id, source_member_id,
            target_bill_pk, target_version_id, target_fragment_id,
            target_session_id, target_member_id,
            match_type, granularity,
            float(similarity_score),
            float(containment_score) if containment_score is not None else None,
            method,
            json.dumps(all_source_members),
            json.dumps(all_target_members),
            now,
        ))

    # ── Pass 1: Whole-bill comparison via LSH candidates ──────────────────
    pks = [b["bill_pk"] for b in bills]

    for i, pk_a in enumerate(pks):
        b_a = pk_to_bill[pk_a]
        candidates = lsh.query(bill_hashes[pk_a])

        for cand_str in candidates:
            pk_b = int(cand_str)
            if pk_b <= pk_a:
                continue  # avoid double-counting; always source < target by pk

            b_b = pk_to_bill[pk_b]

            # Exact Jaccard via token sets (LSH gave us candidates)
            tok_a = bill_tokens[pk_a]
            tok_b = bill_tokens[pk_b]
            union = tok_a | tok_b
            if not union:
                continue
            jaccard = len(tok_a & tok_b) / len(union)

            cross_session = b_a["session_id"] != b_b["session_id"]

            if jaccard >= jaccard_threshold:
                match_type = "reintroduced" if cross_session else "substitute"
                add_row(
                    pk_a, b_a["version_id"], None,
                    b_a["session_id"], b_a["primary_member_id"],
                    pk_b, b_b["version_id"], None,
                    b_b["session_id"], b_b["primary_member_id"],
                    match_type, "bill",
                    jaccard, None, "jaccard",
                    all_sponsors.get(pk_a, []),
                    all_sponsors.get(pk_b, []),
                )

            else:
                # Check containment in both directions — catches amendment absorption
                c_a_in_b = containment(tok_a, tok_b)
                c_b_in_a = containment(tok_b, tok_a)

                if c_a_in_b >= contain_threshold:
                    # Bill A's language is largely inside Bill B
                    add_row(
                        pk_a, b_a["version_id"], None,
                        b_a["session_id"], b_a["primary_member_id"],
                        pk_b, b_b["version_id"], None,
                        b_b["session_id"], b_b["primary_member_id"],
                        "amendment_adopted", "bill",
                        jaccard, c_a_in_b, "containment",
                        all_sponsors.get(pk_a, []),
                        all_sponsors.get(pk_b, []),
                    )

                elif c_b_in_a >= contain_threshold:
                    # Bill B's language is largely inside Bill A
                    add_row(
                        pk_b, b_b["version_id"], None,
                        b_b["session_id"], b_b["primary_member_id"],
                        pk_a, b_a["version_id"], None,
                        b_a["session_id"], b_a["primary_member_id"],
                        "amendment_adopted", "bill",
                        jaccard, c_b_in_a, "containment",
                        all_sponsors.get(pk_b, []),
                        all_sponsors.get(pk_a, []),
                    )

    # ── Pass 2: Fragment-level exact hash matches ─────────────────────────
    # O(N) — compares every section's SHA-1 across all bills
    for chash, entries in hash_index.items():
        if len(entries) < 2:
            continue

        # All pairs with this exact hash
        for i in range(len(entries)):
            pk_a, frag_a = entries[i]
            b_a = pk_to_bill[pk_a]
            for j in range(i + 1, len(entries)):
                pk_b, frag_b = entries[j]
                b_b = pk_to_bill[pk_b]

                cross_session = b_a["session_id"] != b_b["session_id"]
                match_type = "partial_reuse" if cross_session else "partial_reuse"

                add_row(
                    pk_a, b_a["version_id"], frag_a["fragment_id"],
                    b_a["session_id"], b_a["primary_member_id"],
                    pk_b, b_b["version_id"], frag_b["fragment_id"],
                    b_b["session_id"], b_b["primary_member_id"],
                    match_type, "section",
                    1.0, 1.0, "exact_hash",
                    all_sponsors.get(pk_a, []),
                    all_sponsors.get(pk_b, []),
                )

    # ── Pass 3: Fragment-level approximate matching (MinHash per section) ──
    # Only runs if fragment_map is populated (full_text available)
    # Builds a per-session LSH to keep candidates manageable
    frag_minhashes: dict[tuple[int,int], MinHash] = {}  # (bill_pk, frag_idx) -> MinHash

    for bill_pk, frags in fragment_map.items():
        for idx, frag in enumerate(frags):
            m = MinHash(num_perm=num_perm)
            for f in frag["tokens"] | frag["shingles"]:
                m.update(f.encode("utf8"))
            frag_minhashes[(bill_pk, idx)] = m

    if frag_minhashes:
        frag_lsh = MinHashLSH(threshold=0.5, num_perm=num_perm)
        for (bill_pk, idx), mh in frag_minhashes.items():
            frag_lsh.insert(f"{bill_pk}:{idx}", mh)

        for (bill_pk_a, idx_a), mh_a in frag_minhashes.items():
            b_a = pk_to_bill[bill_pk_a]
            frags_a = fragment_map[bill_pk_a]
            frag_a = frags_a[idx_a]

            candidates = frag_lsh.query(mh_a)
            for cand in candidates:
                bill_pk_b_str, idx_b_str = cand.split(":")
                bill_pk_b, idx_b = int(bill_pk_b_str), int(idx_b_str)

                if bill_pk_b == bill_pk_a:
                    continue  # skip same-bill fragment comparisons

                b_b = pk_to_bill[bill_pk_b]
                frags_b = fragment_map[bill_pk_b]
                frag_b = frags_b[idx_b]

                # Skip if already caught by exact hash
                if frag_a["content_hash"] == frag_b["content_hash"]:
                    continue

                tok_a = frag_a["tokens"]
                tok_b = frag_b["tokens"]
                union = tok_a | tok_b
                if not union:
                    continue

                jaccard = len(tok_a & tok_b) / len(union)
                if jaccard < contain_threshold:
                    continue

                c_a_in_b = containment(tok_a, tok_b)
                cross_session = b_a["session_id"] != b_b["session_id"]

                # Directional: always source is the older / smaller fragment
                if b_a["session_id"] <= b_b["session_id"]:
                    src_pk, src_b, src_frag = bill_pk_a, b_a, frag_a
                    tgt_pk, tgt_b, tgt_frag = bill_pk_b, b_b, frag_b
                    cont = c_a_in_b
                else:
                    src_pk, src_b, src_frag = bill_pk_b, b_b, frag_b
                    tgt_pk, tgt_b, tgt_frag = bill_pk_a, b_a, frag_a
                    cont = containment(tok_b, tok_a)

                add_row(
                    src_pk, src_b["version_id"], src_frag["fragment_id"],
                    src_b["session_id"], src_b["primary_member_id"],
                    tgt_pk, tgt_b["version_id"], tgt_frag["fragment_id"],
                    tgt_b["session_id"], tgt_b["primary_member_id"],
                    "partial_reuse", "passage",
                    jaccard, cont, "minhash",
                    all_sponsors.get(src_pk, []),
                    all_sponsors.get(tgt_pk, []),
                )

    # ── Write results ──────────────────────────────────────────────────────
    conn.executemany("""
        INSERT OR IGNORE INTO language_lineage (
            source_bill_pk, source_version_id, source_fragment_id,
            source_session_id, source_member_id,
            target_bill_pk, target_version_id, target_fragment_id,
            target_session_id, target_member_id,
            match_type, granularity,
            similarity_score, containment_score, method,
            all_source_member_ids, all_target_member_ids,
            detected_at
        ) VALUES (
            ?,?,?,?,?,
            ?,?,?,?,?,
            ?,?,?,?,?,
            ?,?,?
        )
    """, lineage_rows)

    conn.commit()
    conn.close()

    print(f"[resurrection] {len(lineage_rows)} lineage rows written.")