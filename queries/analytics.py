"""queries/analytics.py — Cross-cutting analytics queries."""
import pandas as pd
import streamlit as st
from .db import q, scalar


@st.cache_data(ttl=300)
def dashboard_stats() -> dict:
    return {
        "bills":        scalar("SELECT COUNT(*) FROM bills") or 0,
        "members":      scalar("SELECT COUNT(*) FROM members WHERE active=1") or 0,
        "floor_votes":  scalar("SELECT COUNT(*) FROM roll_calls WHERE vote_context='floor'") or 0,
        "comm_votes":   scalar("SELECT COUNT(*) FROM roll_calls WHERE vote_context='committee'") or 0,
        "sessions":     scalar("SELECT COUNT(DISTINCT session_id) FROM bills") or 0,
        "tags":         scalar("SELECT COUNT(*) FROM bill_policy_tags") or 0,
        "lineage_pairs":scalar("SELECT COUNT(*) FROM language_lineage") or 0,
        "fragments":    scalar("SELECT COUNT(*) FROM bill_language_fragments") or 0,
    }


@st.cache_data(ttl=300)
def bills_by_status(session_id: str = None) -> pd.DataFrame:
    if session_id:
        return q("""
            SELECT current_status, COUNT(*) as count
            FROM bills WHERE session_id = ?
            GROUP BY current_status ORDER BY count DESC
        """, (session_id,))
    return q("""
        SELECT current_status, COUNT(*) as count
        FROM bills GROUP BY current_status ORDER BY count DESC
    """)


@st.cache_data(ttl=300)
def bills_by_type(session_id: str = None) -> pd.DataFrame:
    if session_id:
        return q("""
            SELECT bill_type, chamber, COUNT(*) as count
            FROM bills WHERE session_id = ?
            GROUP BY bill_type, chamber ORDER BY count DESC
        """, (session_id,))
    return q("""
        SELECT bill_type, chamber, COUNT(*) as count
        FROM bills GROUP BY bill_type, chamber ORDER BY count DESC
    """)


@st.cache_data(ttl=300)
def vote_timeline(session_id: str = None) -> pd.DataFrame:
    if session_id:
        return q("""
            SELECT vote_date, vote_context,
                   COUNT(*) as votes,
                   SUM(passed) as passed
            FROM roll_calls
            WHERE session_id = ?
            GROUP BY vote_date, vote_context
            ORDER BY vote_date
        """, (session_id,))
    return q("""
        SELECT vote_date, vote_context, session_id,
               COUNT(*) as votes,
               SUM(passed) as passed
        FROM roll_calls
        GROUP BY vote_date, vote_context, session_id
        ORDER BY vote_date
    """)


@st.cache_data(ttl=300)
def cross_aisle_leaders(limit: int = 20) -> pd.DataFrame:
    return q("""
        SELECT
            m.member_id,
            m.full_name,
            m.party,
            m.chamber,
            COUNT(*) as cross_aisle_count,
            mm.votes_cast,
            ROUND(100.0 * COUNT(*) / NULLIF(mm.votes_cast, 0), 1) as cross_aisle_pct
        FROM cross_aisle_votes ca
        JOIN members m ON m.member_id = ca.member_id
        LEFT JOIN member_metrics mm ON mm.member_id = m.member_id
        GROUP BY m.member_id
        ORDER BY cross_aisle_count DESC
        LIMIT ?
    """, (limit,))


@st.cache_data(ttl=300)
def absence_leaders(limit: int = 20) -> pd.DataFrame:
    return q("""
        SELECT
            m.full_name,
            m.party,
            m.chamber,
            mm.votes_cast,
            mm.absent_votes,
            mm.absence_rate
        FROM member_metrics mm
        JOIN members m ON m.member_id = mm.member_id
        WHERE mm.votes_cast > 10
        ORDER BY mm.absence_rate DESC
        LIMIT ?
    """, (limit,))


@st.cache_data(ttl=300)
def sponsorship_leaders(limit: int = 20) -> pd.DataFrame:
    return q("""
        SELECT
            m.full_name,
            m.party,
            m.chamber,
            mm.primary_sponsored,
            mm.co_sponsored,
            (mm.primary_sponsored + mm.co_sponsored) as total_sponsored
        FROM member_metrics mm
        JOIN members m ON m.member_id = mm.member_id
        ORDER BY total_sponsored DESC
        LIMIT ?
    """, (limit,))


@st.cache_data(ttl=300)
def committee_pass_rates() -> pd.DataFrame:
    return q("""
        SELECT
            committee_name,
            chamber,
            session_id,
            total_votes,
            passed_count,
            failed_count,
            pass_rate
        FROM committee_vote_summary
        ORDER BY pass_rate DESC
    """)


@st.cache_data(ttl=300)
def tag_distribution() -> pd.DataFrame:
    return q("""
        SELECT
            pt.label,
            pt.code,
            COUNT(bpt.bill_pk) as bill_count,
            AVG(bpt.tau) as avg_confidence
        FROM bill_policy_tags bpt
        JOIN policy_tags pt ON pt.tag_id = bpt.tag_id
        WHERE bpt.rank = 1
        GROUP BY pt.tag_id
        ORDER BY bill_count DESC
    """)


@st.cache_data(ttl=300)
def bipartisan_votes(min_crossover_pct: float = 0.2, limit: int = 50) -> pd.DataFrame:
    """Floor votes where significant cross-party agreement occurred."""
    return q("""
        SELECT
            rc.roll_call_id,
            rc.vote_date,
            b.bill_label,
            b.title,
            rc.motion_text,
            rc.yes_count,
            rc.no_count,
            rc.passed,
            COUNT(ca.member_id) as cross_aisle_count,
            ROUND(100.0 * COUNT(ca.member_id) / NULLIF(rc.yes_count + rc.no_count, 0), 1) as cross_aisle_pct
        FROM roll_calls rc
        LEFT JOIN bills b ON b.bill_pk = rc.bill_pk
        LEFT JOIN cross_aisle_votes ca ON ca.roll_call_id = rc.roll_call_id
        WHERE rc.vote_context = 'floor'
        GROUP BY rc.roll_call_id
        HAVING cross_aisle_count > 0
        ORDER BY cross_aisle_pct DESC
        LIMIT ?
    """, (limit,))


@st.cache_data(ttl=300)
def agreement_matrix_sample(limit: int = 200) -> pd.DataFrame:
    """Sample of the member agreement matrix for visualisation."""
    return q("""
        SELECT
            ma.member_a,
            ma.member_b,
            ma.shared_votes,
            ma.agree_votes,
            ma.agreement_score,
            ma_name.full_name as name_a,
            ma_name.party as party_a,
            mb_name.full_name as name_b,
            mb_name.party as party_b
        FROM member_agreement ma
        JOIN members ma_name ON ma_name.member_id = ma.member_a
        JOIN members mb_name ON mb_name.member_id = ma.member_b
        WHERE ma.shared_votes >= 30
        ORDER BY ma.agreement_score DESC
        LIMIT ?
    """, (limit,))


@st.cache_data(ttl=300)
def cosponsor_network_data() -> pd.DataFrame:
    return q("""
        SELECT
            cn.sponsor_a,
            cn.sponsor_b,
            cn.shared_bills,
            ma.full_name as name_a,
            ma.party as party_a,
            mb.full_name as name_b,
            mb.party as party_b
        FROM cosponsor_network cn
        JOIN members ma ON ma.member_id = cn.sponsor_a
        JOIN members mb ON mb.member_id = cn.sponsor_b
        WHERE cn.shared_bills >= 3
        ORDER BY cn.shared_bills DESC
        LIMIT 300
    """)


@st.cache_data(ttl=300)
def language_lineage_overview() -> pd.DataFrame:
    return q("""
        SELECT
            ll.match_type,
            ll.granularity,
            ll.method,
            COUNT(*) as pair_count,
            AVG(ll.similarity_score) as avg_similarity,
            AVG(ll.containment_score) as avg_containment
        FROM language_lineage ll
        GROUP BY ll.match_type, ll.granularity, ll.method
        ORDER BY pair_count DESC
    """)


@st.cache_data(ttl=300)
def zombie_bills() -> pd.DataFrame:
    """Bills marked as zombie/reintroduced in language_lineage."""
    return q("""
        SELECT
            b_src.bill_label as original_bill,
            b_src.session_id as original_session,
            b_src.title as original_title,
            b_tgt.bill_label as reintroduced_as,
            b_tgt.session_id as new_session,
            b_tgt.current_status as new_status,
            ll.similarity_score,
            ll.match_type,
            m_src.full_name as original_sponsor,
            m_tgt.full_name as new_sponsor
        FROM language_lineage ll
        JOIN bills b_src ON b_src.bill_pk = ll.source_bill_pk
        JOIN bills b_tgt ON b_tgt.bill_pk = ll.target_bill_pk
        LEFT JOIN members m_src ON m_src.member_id = ll.source_member_id
        LEFT JOIN members m_tgt ON m_tgt.member_id = ll.target_member_id
        WHERE ll.match_type IN ('zombie', 'reintroduced')
        ORDER BY ll.similarity_score DESC
    """)


@st.cache_data(ttl=300)
def bill_text_search(keyword: str) -> pd.DataFrame:
    """Full-text search across bill fragments."""
    if len(keyword.strip()) < 3:
        return pd.DataFrame()
    return q("""
        SELECT
            b.bill_label,
            b.session_id,
            b.title,
            b.current_status,
            blf.fragment_type,
            blf.fragment_index,
            SUBSTR(blf.fragment_text, 1, 400) as excerpt,
            bv.version_label
        FROM bill_language_fragments blf
        JOIN bills b ON b.bill_pk = blf.bill_pk
        LEFT JOIN bill_versions bv ON bv.version_id = blf.version_id
        WHERE blf.fragment_text LIKE ?
        ORDER BY b.session_id DESC, b.bill_label
        LIMIT 100
    """, (f"%{keyword}%",))


@st.cache_data(ttl=300)
def shared_language_pairs(min_score: float = 0.7) -> pd.DataFrame:
    return q("""
        SELECT
            b_src.bill_label as source_bill,
            b_src.session_id as source_session,
            b_tgt.bill_label as target_bill,
            b_tgt.session_id as target_session,
            ll.match_type,
            ll.similarity_score,
            ll.containment_score,
            ll.granularity,
            m_src.full_name as source_sponsor,
            m_tgt.full_name as target_sponsor,
            m_src.party as source_party,
            m_tgt.party as target_party
        FROM language_lineage ll
        JOIN bills b_src ON b_src.bill_pk = ll.source_bill_pk
        JOIN bills b_tgt ON b_tgt.bill_pk = ll.target_bill_pk
        LEFT JOIN members m_src ON m_src.member_id = ll.source_member_id
        LEFT JOIN members m_tgt ON m_tgt.member_id = ll.target_member_id
        WHERE ll.similarity_score >= ?
        ORDER BY ll.similarity_score DESC
        LIMIT 200
    """, (min_score,))