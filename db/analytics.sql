-- db/analytics.sql
-- Materialized analytics layer
-- Rebuilt after every scraper run via db/refresh_views.py
--
-- This file ONLY contains DROP + CREATE AS SELECT materialized tables.
-- Persistent derived intelligence (ideology_scores, sponsorship_scores,
-- bill_lineage, caucus_clusters) lives in extensions.sql and is NOT
-- rebuilt here.

PRAGMA foreign_keys = ON;


-- ─────────────────────────────────────────────────────────────────────────────
-- BILL METRICS
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS bill_metrics;

CREATE TABLE bill_metrics AS
SELECT
    b.bill_pk,
    b.session_id,
    b.bill_label,
    b.chamber,
    b.current_status,
    COUNT(DISTINCT ba.action_id)                                AS action_count,
    -- floor votes only
    COUNT(DISTINCT CASE WHEN rc.vote_context='floor' THEN rc.roll_call_id END)
                                                                AS floor_vote_count,
    -- committee votes
    COUNT(DISTINCT CASE WHEN rc.vote_context='committee' THEN rc.roll_call_id END)
                                                                AS committee_vote_count,
    COUNT(DISTINCT bs.member_id)                                AS sponsor_count,
    COUNT(DISTINCT brc.citation_id)                             AS rsmo_citation_count,
    COUNT(DISTINCT bv.version_id)                               AS version_count,
    MAX(ba.action_date)                                         AS last_action_date
FROM bills b
LEFT JOIN bill_actions   ba  ON ba.bill_pk  = b.bill_pk
LEFT JOIN roll_calls     rc  ON rc.bill_pk  = b.bill_pk
LEFT JOIN bill_sponsors  bs  ON bs.bill_pk  = b.bill_pk
LEFT JOIN bill_rsmo_citations brc ON brc.bill_pk = b.bill_pk
LEFT JOIN bill_versions  bv  ON bv.bill_pk  = b.bill_pk
GROUP BY b.bill_pk;

CREATE UNIQUE INDEX idx_bill_metrics_pk ON bill_metrics(bill_pk);
CREATE INDEX idx_bill_metrics_session   ON bill_metrics(session_id);
CREATE INDEX idx_bill_metrics_status    ON bill_metrics(current_status);


-- ─────────────────────────────────────────────────────────────────────────────
-- MEMBER METRICS
-- Covers floor votes only by default; use committee_member_metrics for committee.
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS member_metrics;

CREATE TABLE member_metrics AS
WITH floor_vote_stats AS (
    SELECT
        mv.member_id,
        COUNT(*)                        AS votes_cast,
        SUM(mv.vote_cast = 'yes')       AS yes_votes,
        SUM(mv.vote_cast = 'no')        AS no_votes,
        SUM(mv.vote_cast = 'present')   AS present_votes,
        SUM(mv.vote_cast = 'absent')    AS absent_votes
    FROM member_votes mv
    JOIN roll_calls rc ON rc.roll_call_id = mv.roll_call_id
    WHERE rc.vote_context = 'floor'
    GROUP BY mv.member_id
),
committee_vote_stats AS (
    SELECT
        mv.member_id,
        COUNT(*)                        AS committee_votes_cast,
        SUM(mv.vote_cast = 'yes')       AS committee_yes,
        SUM(mv.vote_cast = 'no')        AS committee_no
    FROM member_votes mv
    JOIN roll_calls rc ON rc.roll_call_id = mv.roll_call_id
    WHERE rc.vote_context = 'committee'
    GROUP BY mv.member_id
),
sponsor_stats AS (
    SELECT
        member_id,
        SUM(sponsor_type = 'primary')   AS primary_sponsored,
        SUM(sponsor_type = 'cosponsor') AS co_sponsored
    FROM bill_sponsors
    GROUP BY member_id
)
SELECT
    m.member_id,
    m.full_name,
    m.party,
    m.chamber,
    -- Floor votes
    COALESCE(f.votes_cast, 0)           AS votes_cast,
    COALESCE(f.yes_votes, 0)            AS yes_votes,
    COALESCE(f.no_votes, 0)             AS no_votes,
    COALESCE(f.present_votes, 0)        AS present_votes,
    COALESCE(f.absent_votes, 0)         AS absent_votes,
    ROUND(
        CASE WHEN COALESCE(f.votes_cast,0) > 0
             THEN 1.0 * COALESCE(f.absent_votes,0) / f.votes_cast
             ELSE NULL END, 4
    )                                   AS absence_rate,
    -- Committee votes
    COALESCE(c.committee_votes_cast, 0) AS committee_votes_cast,
    COALESCE(c.committee_yes, 0)        AS committee_yes,
    COALESCE(c.committee_no, 0)         AS committee_no,
    -- Sponsorship
    COALESCE(s.primary_sponsored, 0)    AS primary_sponsored,
    COALESCE(s.co_sponsored, 0)         AS co_sponsored
FROM members m
LEFT JOIN floor_vote_stats     f ON f.member_id = m.member_id
LEFT JOIN committee_vote_stats c ON c.member_id = m.member_id
LEFT JOIN sponsor_stats        s ON s.member_id = m.member_id;

CREATE UNIQUE INDEX idx_member_metrics_pk ON member_metrics(member_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- CROSS-AISLE VOTES
-- Members who voted against their party's majority on a floor roll call.
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS cross_aisle_votes;

CREATE TABLE cross_aisle_votes AS
WITH party_vote AS (
    SELECT
        mv.roll_call_id,
        m.party,
        mv.vote_cast,
        COUNT(*) AS n
    FROM member_votes mv
    JOIN members m   ON m.member_id = mv.member_id
    JOIN roll_calls rc ON rc.roll_call_id = mv.roll_call_id
    WHERE mv.vote_cast IN ('yes','no')
      AND rc.vote_context = 'floor'
    GROUP BY mv.roll_call_id, m.party, mv.vote_cast
),
party_majority AS (
    SELECT * FROM (
        SELECT *,
               ROW_NUMBER() OVER (
                   PARTITION BY roll_call_id, party
                   ORDER BY n DESC
               ) AS rn
        FROM party_vote
    )
    WHERE rn = 1
)
SELECT
    mv.roll_call_id,
    mv.member_id,
    m.party,
    mv.vote_cast,
    pm.vote_cast AS party_majority_vote
FROM member_votes mv
JOIN members      m  ON m.member_id      = mv.member_id
JOIN party_majority pm ON pm.roll_call_id = mv.roll_call_id
                       AND pm.party        = m.party
WHERE mv.vote_cast IN ('yes','no')
  AND mv.vote_cast != pm.vote_cast;

CREATE INDEX idx_cross_aisle_member    ON cross_aisle_votes(member_id);
CREATE INDEX idx_cross_aisle_rollcall  ON cross_aisle_votes(roll_call_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- MEMBER AGREEMENT MATRIX
-- Pairwise floor-vote agreement scores (≥ 20 shared votes)
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS member_agreement;

CREATE TABLE member_agreement AS
SELECT
    a.member_id                                         AS member_a,
    b.member_id                                         AS member_b,
    COUNT(*)                                            AS shared_votes,
    SUM(a.vote_cast = b.vote_cast)                      AS agree_votes,
    ROUND(1.0 * SUM(a.vote_cast = b.vote_cast) / COUNT(*), 4)
                                                        AS agreement_score
FROM member_votes a
JOIN member_votes b ON a.roll_call_id = b.roll_call_id
                    AND a.member_id < b.member_id
JOIN roll_calls rc  ON rc.roll_call_id = a.roll_call_id
WHERE a.vote_cast IN ('yes','no')
  AND b.vote_cast IN ('yes','no')
  AND rc.vote_context = 'floor'
GROUP BY a.member_id, b.member_id
HAVING shared_votes >= 20;

CREATE INDEX idx_member_agreement_a     ON member_agreement(member_a);
CREATE INDEX idx_member_agreement_b     ON member_agreement(member_b);
CREATE INDEX idx_member_agreement_score ON member_agreement(agreement_score DESC);


-- ─────────────────────────────────────────────────────────────────────────────
-- COSPONSOR NETWORK
-- Member pairs who cosponsored ≥ 2 bills together
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS cosponsor_network;

CREATE TABLE cosponsor_network AS
SELECT
    a.member_id AS sponsor_a,
    b.member_id AS sponsor_b,
    COUNT(*)    AS shared_bills
FROM bill_sponsors a
JOIN bill_sponsors b ON a.bill_pk = b.bill_pk
                     AND a.member_id < b.member_id
GROUP BY a.member_id, b.member_id
HAVING shared_bills >= 2;

CREATE INDEX idx_cosponsor_a ON cosponsor_network(sponsor_a);
CREATE INDEX idx_cosponsor_b ON cosponsor_network(sponsor_b);


-- ─────────────────────────────────────────────────────────────────────────────
-- COMMITTEE VOTE SUMMARY
-- Aggregated pass/fail rates per committee for the current session
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS committee_vote_summary;

CREATE TABLE committee_vote_summary AS
SELECT
    rc.committee_id,
    COALESCE(c.name, rc.committee_name_raw) AS committee_name,
    rc.session_id,
    rc.chamber,
    COUNT(*)                                AS total_votes,
    SUM(rc.passed = 1)                      AS passed_count,
    SUM(rc.passed = 0)                      AS failed_count,
    ROUND(1.0 * SUM(rc.passed = 1) / COUNT(*), 4) AS pass_rate,
    SUM(rc.yes_count)                       AS total_yes,
    SUM(rc.no_count)                        AS total_no
FROM roll_calls rc
LEFT JOIN committees c ON c.committee_id = rc.committee_id
WHERE rc.vote_context = 'committee'
GROUP BY rc.committee_id, rc.session_id;

CREATE INDEX idx_cvs_committee ON committee_vote_summary(committee_id);
CREATE INDEX idx_cvs_session   ON committee_vote_summary(session_id);


-- ─────────────────────────────────────────────────────────────────────────────
-- POLICY TAG VOTE ALIGNMENT
-- For each member × tag: how often did they vote yes on tagged bills?
-- Drives tag-filtered ideology and influence scores.
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS member_tag_alignment;

CREATE TABLE member_tag_alignment AS
SELECT
    mv.member_id,
    bpt.tag_id,
    COUNT(*)                            AS tag_votes,
    SUM(mv.vote_cast = 'yes')           AS yes_on_tag,
    SUM(mv.vote_cast = 'no')            AS no_on_tag,
    ROUND(
        1.0 * SUM(mv.vote_cast = 'yes') / NULLIF(
            SUM(mv.vote_cast IN ('yes','no')), 0
        ), 4
    )                                   AS yes_rate
FROM member_votes mv
JOIN roll_calls rc        ON rc.roll_call_id = mv.roll_call_id
JOIN bill_policy_tags bpt ON bpt.bill_pk     = rc.bill_pk
WHERE mv.vote_cast IN ('yes','no','present','absent')
  AND rc.vote_context = 'floor'
  AND bpt.tau >= 0.5
GROUP BY mv.member_id, bpt.tag_id
HAVING tag_votes >= 3;

CREATE INDEX idx_mta_member ON member_tag_alignment(member_id);
CREATE INDEX idx_mta_tag    ON member_tag_alignment(tag_id);