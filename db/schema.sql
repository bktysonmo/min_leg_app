-- db/schema.sql
-- Missouri Legislative Intelligence Database
-- SQLite with WAL mode for concurrent reads during scraping
--
-- Design principles:
--   · All votes (floor + committee) share a unified votes/member_votes model
--   · bill_actions.vote_type distinguishes floor / committee / procedural
--   · analytics tables live in analytics.sql; lineage + ideology in extensions.sql
--   · Duplicate bill_lineage definition removed — canonical copy is in extensions.sql

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous  = NORMAL;
-- ─────────────────────────────────────────────────────────────────────────────
-- SESSIONS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,  -- e.g. "2026R"
    year            INTEGER NOT NULL,
    session_code    TEXT NOT NULL,     -- R / S / V
    label           TEXT,
    convene_date    TEXT,
    adjourn_date    TEXT
);


-- ─────────────────────────────────────────────────────────────────────────────
-- MEMBERS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS members (
    member_id       INTEGER PRIMARY KEY,   -- senate.mo.gov ?id=N
    chamber         TEXT NOT NULL CHECK(chamber IN ('senate','house')),
    first_name      TEXT,
    last_name       TEXT NOT NULL,
    full_name       TEXT,
    party           TEXT,                  -- R / D / I
    district        INTEGER,
    county_list     TEXT,                  -- comma-separated
    first_elected   TEXT,
    term_end        TEXT,
    phone           TEXT,
    email           TEXT,
    photo_url       TEXT,
    active          INTEGER NOT NULL DEFAULT 1,
    last_scraped    TEXT
);

-- Name variants for fuzzy journal matching
CREATE TABLE IF NOT EXISTS member_name_variants (
    variant_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id       INTEGER NOT NULL REFERENCES members(member_id) ON DELETE CASCADE,
    name_variant    TEXT NOT NULL,
    source          TEXT                   -- "journal" / "bill_page" / "manual"
);
CREATE INDEX IF NOT EXISTS idx_name_variant ON member_name_variants(name_variant);
CREATE UNIQUE INDEX IF NOT EXISTS idx_name_variant_unique
    ON member_name_variants(member_id, name_variant);


-- ─────────────────────────────────────────────────────────────────────────────
-- COMMITTEES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS committees (
    committee_id    INTEGER PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    chamber         TEXT NOT NULL CHECK(chamber IN ('senate','house','joint')),
    name            TEXT NOT NULL,
    committee_type  TEXT                   -- Standing / Select / Joint
);
CREATE INDEX IF NOT EXISTS idx_committee_session ON committees(session_id, chamber);

CREATE TABLE IF NOT EXISTS committee_assignments (
    assignment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    member_id       INTEGER NOT NULL REFERENCES members(member_id),
    session_id      TEXT    NOT NULL REFERENCES sessions(session_id),
    committee_id    INTEGER NOT NULL REFERENCES committees(committee_id),
    role            TEXT,                  -- Chair / Vice-Chair / Member
    UNIQUE(member_id, committee_id)
);


-- ─────────────────────────────────────────────────────────────────────────────
-- BILLS
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS bills (
    bill_pk         INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id         INTEGER,               -- senate.mo.gov billid (nullable for house)
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    chamber         TEXT NOT NULL CHECK(chamber IN ('senate','house')),
    bill_type       TEXT,                  -- SB / HB / SCR / HCR / SJR / HJR / SR / HR
    bill_number     INTEGER NOT NULL,
    bill_label      TEXT,                  -- "SB 834"
    lr_number       TEXT,
    title           TEXT,
    short_desc      TEXT,
    introduced_date TEXT,
    effective_date  TEXT,
    current_status  TEXT,
    current_committee_id    INTEGER REFERENCES committees(committee_id),
    house_handler_id        INTEGER REFERENCES members(member_id),
    last_action_date TEXT,
    last_scraped    TEXT,
    source_url      TEXT,
    UNIQUE(session_id, chamber, bill_type, bill_number)
);
CREATE INDEX IF NOT EXISTS idx_bills_session ON bills(session_id);
CREATE INDEX IF NOT EXISTS idx_bills_status  ON bills(current_status);
CREATE INDEX IF NOT EXISTS idx_bills_label   ON bills(bill_label);    -- common lookup

-- Sponsors (primary + cosponsors)
CREATE TABLE IF NOT EXISTS bill_sponsors (
    bill_pk         INTEGER NOT NULL REFERENCES bills(bill_pk) ON DELETE CASCADE,
    member_id       INTEGER NOT NULL REFERENCES members(member_id),
    sponsor_type    TEXT NOT NULL CHECK(sponsor_type IN ('primary','cosponsor')),
    added_date      TEXT,
    PRIMARY KEY (bill_pk, member_id, sponsor_type)
);
CREATE INDEX IF NOT EXISTS idx_sponsors_member ON bill_sponsors(member_id);
CREATE INDEX IF NOT EXISTS idx_sponsors_bill   ON bill_sponsors(bill_pk);

-- Action timeline
CREATE TABLE IF NOT EXISTS bill_actions (
    action_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_pk         INTEGER NOT NULL REFERENCES bills(bill_pk) ON DELETE CASCADE,
    action_date     TEXT NOT NULL,
    action_text     TEXT NOT NULL,
    chamber         TEXT,
    -- 'floor' | 'committee' | 'procedural' | NULL (legacy rows treated as floor)
    vote_type       TEXT CHECK(vote_type IN ('floor','committee','procedural')),
    journal_page    TEXT,
    action_hash     TEXT                   -- 16-char SHA-1 dedup key
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_hash ON bill_actions(action_hash);
CREATE INDEX IF NOT EXISTS idx_actions_bill      ON bill_actions(bill_pk);
CREATE INDEX IF NOT EXISTS idx_actions_date      ON bill_actions(action_date);
CREATE INDEX IF NOT EXISTS idx_actions_vote_type ON bill_actions(vote_type)
    WHERE vote_type IS NOT NULL;

-- Bill text versions
CREATE TABLE IF NOT EXISTS bill_versions (
    version_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_pk         INTEGER NOT NULL REFERENCES bills(bill_pk) ON DELETE CASCADE,
    version_label   TEXT,                  -- "Introduced" / "SS" / "SCS" / "TATFP"
    version_date    TEXT,
    stage           TEXT,                  -- introduced/committee_sub/perfected/conference/truly_agreed
    full_text       TEXT,
    url             TEXT,
    storage_path    TEXT,
    content_hash    TEXT,
    word_count      INTEGER,
    scraped_at      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_version_hash    ON bill_versions(content_hash);
CREATE INDEX IF NOT EXISTS idx_versions_bill          ON bill_versions(bill_pk);

-- RSMo citations extracted from bill text
CREATE TABLE IF NOT EXISTS bill_rsmo_citations (
    citation_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_pk         INTEGER NOT NULL REFERENCES bills(bill_pk) ON DELETE CASCADE,
    version_id      INTEGER REFERENCES bill_versions(version_id),
    chapter         INTEGER,
    section         TEXT,                  -- e.g. "130.011"
    citation_type   TEXT                   -- amends / creates / repeals / references
        CHECK(citation_type IN ('amends','creates','repeals','references')),
    extracted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_citations_section ON bill_rsmo_citations(section);
CREATE INDEX IF NOT EXISTS idx_citations_chapter ON bill_rsmo_citations(chapter);
CREATE INDEX IF NOT EXISTS idx_citations_bill    ON bill_rsmo_citations(bill_pk);


-- ─────────────────────────────────────────────────────────────────────────────
-- VOTES  (floor + committee unified)
--
-- roll_calls covers both floor votes (chamber-wide) and committee votes.
-- vote_context distinguishes them; committee_id is NULL for floor votes.
-- This replaces the separate committee_votes table from the migration while
-- keeping full backwards compatibility via the vote_context column.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS roll_calls (
    roll_call_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_pk         INTEGER REFERENCES bills(bill_pk),   -- NULL for procedural votes
    session_id      TEXT NOT NULL REFERENCES sessions(session_id),
    chamber         TEXT NOT NULL CHECK(chamber IN ('senate','house')),
    vote_date       TEXT NOT NULL,

    -- 'floor' | 'committee' — drives how this row is filtered in queries
    vote_context    TEXT NOT NULL DEFAULT 'floor'
        CHECK(vote_context IN ('floor','committee')),

    -- NULL for floor votes; populated for committee votes
    committee_id    INTEGER REFERENCES committees(committee_id),
    committee_name_raw TEXT,              -- raw name when committee_id unresolvable

    reading_stage   TEXT,                 -- 1st/2nd/3rd/perfection/final/amendment/veto_override
    motion_text     TEXT,
    yes_count       INTEGER,
    no_count        INTEGER,
    present_count   INTEGER DEFAULT 0,
    absent_count    INTEGER DEFAULT 0,
    total_counted   INTEGER,              -- validated sum
    passed          INTEGER,              -- 1/0; NULL if unknown

    -- Links companion bill_actions row so timeline stays complete
    action_id       INTEGER REFERENCES bill_actions(action_id),

    journal_page    TEXT,
    journal_pdf_path TEXT,
    source_url      TEXT,
    parse_confidence TEXT CHECK(parse_confidence IN ('high','medium','low','manual_review')),
    parsed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_rollcall_bill     ON roll_calls(bill_pk);
CREATE INDEX IF NOT EXISTS idx_rollcall_date     ON roll_calls(vote_date);
CREATE INDEX IF NOT EXISTS idx_rollcall_session  ON roll_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_rollcall_context  ON roll_calls(vote_context);
CREATE INDEX IF NOT EXISTS idx_rollcall_committee ON roll_calls(committee_id)
    WHERE committee_id IS NOT NULL;

-- Per-member votes — covers both floor and committee roll calls
CREATE TABLE IF NOT EXISTS member_votes (
    roll_call_id    INTEGER NOT NULL REFERENCES roll_calls(roll_call_id) ON DELETE CASCADE,
    member_id       INTEGER NOT NULL REFERENCES members(member_id),
    vote_cast       TEXT NOT NULL CHECK(vote_cast IN ('yes','no','present','absent','NV')),
    name_raw        TEXT,                  -- original string from source (audit)
    match_confidence REAL,                 -- 0–1 fuzzy match score
    PRIMARY KEY (roll_call_id, member_id)
);
CREATE INDEX IF NOT EXISTS idx_votes_member    ON member_votes(member_id);
CREATE INDEX IF NOT EXISTS idx_mv_member_roll  ON member_votes(member_id, roll_call_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- FISCAL NOTES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fiscal_notes (
    fiscal_note_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_pk             INTEGER NOT NULL REFERENCES bills(bill_pk) ON DELETE CASCADE,
    version_id          INTEGER REFERENCES bill_versions(version_id),
    issued_date         TEXT,
    issuing_agency      TEXT,
    fiscal_year         TEXT,
    estimated_cost_min  REAL,
    estimated_cost_max  REAL,
    cost_direction      TEXT CHECK(cost_direction IN ('savings','cost','indeterminate')),
    storage_path        TEXT,
    scraped_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_fiscal_bill ON fiscal_notes(bill_pk);


-- ─────────────────────────────────────────────────────────────────────────────
-- RSMo STATUTES
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS rsmo_sections (
    section_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter             INTEGER NOT NULL,
    section_number      TEXT NOT NULL UNIQUE,  -- e.g. "130.011"
    section_title       TEXT,
    full_text           TEXT,
    effective_date      TEXT,
    last_amended_by_bill_pk INTEGER REFERENCES bills(bill_pk),
    content_hash        TEXT,
    last_scraped        TEXT
);
CREATE INDEX IF NOT EXISTS idx_rsmo_chapter ON rsmo_sections(chapter);

CREATE TABLE IF NOT EXISTS rsmo_history (
    history_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    section_number  TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    effective_date  TEXT,
    full_text       TEXT,
    recorded_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_rsmo_history_section ON rsmo_history(section_number);


-- ─────────────────────────────────────────────────────────────────────────────
-- CAMPAIGN FINANCE
-- ─────────────────────────────────────────────────────────────────────────────

-- Canonical entity registry (deduplicates contributor names across filings)
CREATE TABLE IF NOT EXISTS canonical_entities (
    entity_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name  TEXT NOT NULL UNIQUE,
    entity_type     TEXT,                  -- PAC / individual / corporation / union / party
    industry_label  TEXT,
    aliases         TEXT                   -- JSON array of known name variants
);

CREATE TABLE IF NOT EXISTS mec_committees (
    mec_id                  TEXT PRIMARY KEY,   -- "C012345"
    committee_name          TEXT NOT NULL,
    committee_type          TEXT,               -- Campaign / PAC / Party / Continuing
    treasurer_name          TEXT,
    candidate_member_id     INTEGER REFERENCES members(member_id),
    active                  INTEGER NOT NULL DEFAULT 1,
    last_scraped            TEXT
);
CREATE INDEX IF NOT EXISTS idx_mec_candidate ON mec_committees(candidate_member_id)
    WHERE candidate_member_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS contributions (
    contribution_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mec_id                  TEXT NOT NULL REFERENCES mec_committees(mec_id),
    contributor_name        TEXT,
    contributor_canonical_id INTEGER REFERENCES canonical_entities(entity_id),
    amount                  REAL NOT NULL,
    contribution_date       TEXT,
    contribution_type       TEXT CHECK(contribution_type IN ('monetary','in-kind','loan')),
    report_period           TEXT,
    filing_id               TEXT,
    scraped_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_contrib_mec      ON contributions(mec_id);
CREATE INDEX IF NOT EXISTS idx_contrib_date     ON contributions(contribution_date);
CREATE INDEX IF NOT EXISTS idx_contrib_entity   ON contributions(contributor_canonical_id)
    WHERE contributor_canonical_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS expenditures (
    expenditure_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mec_id              TEXT NOT NULL REFERENCES mec_committees(mec_id),
    payee_name          TEXT,
    amount              REAL NOT NULL,
    expenditure_date    TEXT,
    purpose             TEXT,
    report_period       TEXT,
    filing_id           TEXT,
    scraped_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_expenditure_mec  ON expenditures(mec_id);
CREATE INDEX IF NOT EXISTS idx_expenditure_date ON expenditures(expenditure_date);


-- ─────────────────────────────────────────────────────────────────────────────
-- POLICY TAGS  (D&G 28-code taxonomy)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS policy_tags (
    tag_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code            TEXT NOT NULL UNIQUE,  -- e.g. "G0300"
    label           TEXT NOT NULL,         -- e.g. "Health"
    description     TEXT,
    keywords        TEXT,                  -- JSON array (seed keywords)
    parent_code     TEXT,                  -- future hierarchy (unused)
    active          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_tag_code ON policy_tags(code);

CREATE TABLE IF NOT EXISTS bill_policy_tags (
    bpt_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_pk         INTEGER NOT NULL REFERENCES bills(bill_pk) ON DELETE CASCADE,
    tag_id          INTEGER NOT NULL REFERENCES policy_tags(tag_id),
    tau             REAL NOT NULL,         -- model confidence 0.0–1.0
    rank            INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 3),  -- 1 = top tag
    source          TEXT NOT NULL DEFAULT 'keyword'
        CHECK(source IN ('keyword','bert','roberta','xlnet','human')),
    model_version   TEXT,
    tagged_at       TEXT NOT NULL,
    confirmed       INTEGER,               -- NULL=unreviewed / 1=confirmed / 0=rejected
    confirmed_by    TEXT,
    confirmed_at    TEXT,
    UNIQUE(bill_pk, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_bpt_bill   ON bill_policy_tags(bill_pk);
CREATE INDEX IF NOT EXISTS idx_bpt_tag    ON bill_policy_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_bpt_tau    ON bill_policy_tags(tau DESC);
CREATE INDEX IF NOT EXISTS idx_bpt_source ON bill_policy_tags(source);

-- Append-only human feedback — drives fine-tuning
CREATE TABLE IF NOT EXISTS tag_feedback (
    feedback_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_pk         INTEGER NOT NULL REFERENCES bills(bill_pk),
    tag_id          INTEGER NOT NULL REFERENCES policy_tags(tag_id),
    action          TEXT NOT NULL CHECK(action IN ('add','remove','confirm')),
    prior_tau       REAL,
    prior_source    TEXT,
    bill_title_snap TEXT,
    bill_desc_snap  TEXT,
    submitted_by    TEXT,
    submitted_at    TEXT NOT NULL,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_feedback_bill ON tag_feedback(bill_pk);
CREATE INDEX IF NOT EXISTS idx_feedback_tag  ON tag_feedback(tag_id);
CREATE INDEX IF NOT EXISTS idx_feedback_date ON tag_feedback(submitted_at);

-- Configurable tag-weighting profiles
CREATE TABLE IF NOT EXISTS tag_weight_profiles (
    profile_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name            TEXT NOT NULL UNIQUE,
    description             TEXT,
    sponsor_primary_weight  REAL NOT NULL DEFAULT 3.0,
    sponsor_co_weight       REAL NOT NULL DEFAULT 1.5,
    vote_yes_weight         REAL NOT NULL DEFAULT 1.0,
    vote_no_weight          REAL NOT NULL DEFAULT 1.0,
    vote_present_weight     REAL NOT NULL DEFAULT 0.25,
    vote_absent_weight      REAL NOT NULL DEFAULT 0.0,
    min_tau                 REAL NOT NULL DEFAULT 0.5,
    max_tags_per_bill       INTEGER NOT NULL DEFAULT 3
        CHECK(max_tags_per_bill BETWEEN 1 AND 3),
    is_default              INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT,
    updated_at              TEXT
);

INSERT OR IGNORE INTO tag_weight_profiles (
    profile_name, description,
    sponsor_primary_weight, sponsor_co_weight,
    vote_yes_weight, vote_no_weight,
    vote_present_weight, vote_absent_weight,
    min_tau, max_tags_per_bill, is_default, created_at
) VALUES (
    'default', 'Standard weights: sponsors count 3× more than votes',
    3.0, 1.5, 1.0, 1.0, 0.25, 0.0,
    0.5, 3, 1, datetime('now')
);

-- Tagging run audit log
CREATE TABLE IF NOT EXISTS tag_run_log (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version   TEXT NOT NULL,
    pass_type       TEXT NOT NULL CHECK(pass_type IN ('keyword','bert','retrain')),
    bills_processed INTEGER,
    tags_written    INTEGER,
    tau_threshold   REAL,
    started_at      TEXT,
    completed_at    TEXT,
    notes           TEXT
);


-- ─────────────────────────────────────────────────────────────────────────────
-- AUDIT & OPERATIONS
-- ─────────────────────────────────────────────────────────────────────────────

-- Append-only change log — audit trail and alert trigger source
CREATE TABLE IF NOT EXISTS change_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    entity_pk       TEXT,
    field_changed   TEXT,
    old_value       TEXT,
    new_value       TEXT,
    detected_at     TEXT NOT NULL,
    scraper         TEXT,
    source_url      TEXT,
    raw_source_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_type      ON change_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_entity    ON change_events(entity_type, entity_pk);
CREATE INDEX IF NOT EXISTS idx_events_date      ON change_events(detected_at);
CREATE INDEX IF NOT EXISTS idx_event_entity_pk  ON change_events(entity_pk);

-- Rows that fail validation land here for human review
CREATE TABLE IF NOT EXISTS parse_review_queue (
    queue_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type     TEXT,
    source_path     TEXT,
    raw_content     TEXT,
    error_reason    TEXT,
    detected_at     TEXT,
    resolved        INTEGER NOT NULL DEFAULT 0,
    resolved_at     TEXT,
    resolved_by     TEXT,
    resolution_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_parse_queue_resolved ON parse_review_queue(resolved, detected_at);