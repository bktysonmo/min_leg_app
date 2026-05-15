"""queries/bills.py — All bill-related database queries."""
import pandas as pd
import streamlit as st
from .db import q, scalar, norm_bill, bill_nospace


@st.cache_data(ttl=300)
def all_bills(session_id: str = None) -> pd.DataFrame:
    base = """
        SELECT
            b.bill_pk,
            b.bill_label,
            b.bill_type,
            b.bill_number,
            b.chamber,
            b.session_id,
            b.title,
            b.short_desc,
            b.current_status,
            b.introduced_date,
            b.last_action_date,
            bm.action_count,
            bm.floor_vote_count,
            bm.sponsor_count,
            bm.rsmo_citation_count,
            bm.version_count
        FROM bills b
        LEFT JOIN bill_metrics bm ON bm.bill_pk = b.bill_pk
    """
    if session_id:
        return q(base + " WHERE b.session_id = ? ORDER BY b.bill_label", (session_id,))
    return q(base + " ORDER BY b.session_id DESC, b.bill_label")


@st.cache_data(ttl=300)
def bill_detail(bill_pk: int) -> dict:
    df = q("SELECT * FROM bills WHERE bill_pk = ?", (bill_pk,))
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=300)
def bill_by_label(bill_label: str) -> dict:
    df = q("SELECT * FROM bills WHERE bill_label = ?", (bill_label,))
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=300)
def bill_floor_votes(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            rc.roll_call_id,
            rc.vote_date,
            rc.reading_stage,
            rc.motion_text,
            rc.yes_count,
            rc.no_count,
            rc.present_count,
            rc.absent_count,
            rc.passed,
            rc.journal_page
        FROM roll_calls rc
        WHERE rc.bill_pk = ?
          AND rc.vote_context = 'floor'
        ORDER BY rc.vote_date DESC
    """, (bill_pk,))


@st.cache_data(ttl=300)
def bill_committee_votes(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            rc.roll_call_id,
            rc.vote_date,
            COALESCE(c.name, rc.committee_name_raw) AS committee_name,
            rc.motion_text,
            rc.yes_count,
            rc.no_count,
            rc.passed
        FROM roll_calls rc
        LEFT JOIN committees c ON c.committee_id = rc.committee_id
        WHERE rc.bill_pk = ?
          AND rc.vote_context = 'committee'
        ORDER BY rc.vote_date DESC
    """, (bill_pk,))


@st.cache_data(ttl=300)
def roll_call_member_votes(roll_call_id: int) -> pd.DataFrame:
    return q("""
        SELECT
            m.member_id,
            m.full_name,
            m.party,
            m.chamber,
            m.district,
            mv.vote_cast
        FROM member_votes mv
        JOIN members m ON m.member_id = mv.member_id
        WHERE mv.roll_call_id = ?
        ORDER BY m.party, mv.vote_cast, m.full_name
    """, (roll_call_id,))


@st.cache_data(ttl=300)
def bill_actions(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            action_date,
            chamber,
            action_text,
            vote_type,
            journal_page
        FROM bill_actions
        WHERE bill_pk = ?
        ORDER BY action_date ASC, action_id ASC
    """, (bill_pk,))


@st.cache_data(ttl=300)
def bill_sponsors(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            m.member_id,
            m.full_name,
            m.party,
            m.chamber,
            m.district,
            bs.sponsor_type,
            bs.added_date
        FROM bill_sponsors bs
        JOIN members m ON m.member_id = bs.member_id
        WHERE bs.bill_pk = ?
        ORDER BY bs.sponsor_type, m.full_name
    """, (bill_pk,))


@st.cache_data(ttl=300)
def bill_rsmo_citations(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            brc.chapter,
            brc.section,
            brc.citation_type,
            rs.section_title
        FROM bill_rsmo_citations brc
        LEFT JOIN rsmo_sections rs ON rs.section_number = brc.section
        WHERE brc.bill_pk = ?
        ORDER BY brc.chapter, brc.section
    """, (bill_pk,))


@st.cache_data(ttl=300)
def bill_versions(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            version_id,
            version_label,
            version_date,
            stage,
            word_count,
            url,
            content_hash
        FROM bill_versions
        WHERE bill_pk = ?
        ORDER BY version_date DESC
    """, (bill_pk,))


@st.cache_data(ttl=300)
def bill_version_text(version_id: int) -> str:
    result = scalar("SELECT full_text FROM bill_versions WHERE version_id = ?", (version_id,))
    return result or ""


@st.cache_data(ttl=300)
def bill_policy_tags(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            pt.label,
            pt.code,
            bpt.tau,
            bpt.rank,
            bpt.source,
            bpt.confirmed
        FROM bill_policy_tags bpt
        JOIN policy_tags pt ON pt.tag_id = bpt.tag_id
        WHERE bpt.bill_pk = ?
        ORDER BY bpt.rank
    """, (bill_pk,))


@st.cache_data(ttl=300)
def bills_sharing_rsmo(bill_pk: int) -> pd.DataFrame:
    """Other bills touching the same RSMo sections as this bill."""
    return q("""
        SELECT DISTINCT
            b2.bill_pk,
            b2.bill_label,
            b2.chamber,
            b2.session_id,
            b2.title,
            b2.current_status,
            brc2.section,
            brc2.citation_type
        FROM bill_rsmo_citations brc1
        JOIN bill_rsmo_citations brc2 ON brc2.section = brc1.section
                                      AND brc2.bill_pk != brc1.bill_pk
        JOIN bills b2 ON b2.bill_pk = brc2.bill_pk
        WHERE brc1.bill_pk = ?
        ORDER BY brc2.section, b2.bill_label
    """, (bill_pk,))


@st.cache_data(ttl=300)
def bill_lineage_related(bill_pk: int) -> pd.DataFrame:
    """Bills related via language lineage."""
    return q("""
        SELECT
            ll.lang_lineage_id,
            CASE WHEN ll.source_bill_pk = ? THEN 'descended-from' ELSE 'ancestor-of' END AS direction,
            CASE WHEN ll.source_bill_pk = ? THEN b_tgt.bill_label ELSE b_src.bill_label END AS related_bill,
            CASE WHEN ll.source_bill_pk = ? THEN b_tgt.title ELSE b_src.title END AS related_title,
            CASE WHEN ll.source_bill_pk = ? THEN ll.target_session_id ELSE ll.source_session_id END AS related_session,
            ll.match_type,
            ll.granularity,
            ll.similarity_score,
            ll.containment_score,
            ll.method,
            ll.detected_at
        FROM language_lineage ll
        JOIN bills b_src ON b_src.bill_pk = ll.source_bill_pk
        JOIN bills b_tgt ON b_tgt.bill_pk = ll.target_bill_pk
        WHERE ll.source_bill_pk = ? OR ll.target_bill_pk = ?
        ORDER BY ll.similarity_score DESC
    """, (bill_pk, bill_pk, bill_pk, bill_pk, bill_pk, bill_pk))


@st.cache_data(ttl=300)
def bill_language_fragments(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT
            blf.fragment_id,
            blf.fragment_index,
            blf.fragment_type,
            blf.char_offset,
            blf.char_length,
            blf.fragment_text,
            blf.content_hash,
            bv.version_label
        FROM bill_language_fragments blf
        LEFT JOIN bill_versions bv ON bv.version_id = blf.version_id
        WHERE blf.bill_pk = ?
        ORDER BY blf.fragment_index
    """, (bill_pk,))


@st.cache_data(ttl=300)
def fragment_matches(fragment_id: int) -> pd.DataFrame:
    """Find where this fragment's text hash appears in other bills."""
    content_hash = scalar(
        "SELECT content_hash FROM bill_language_fragments WHERE fragment_id = ?",
        (fragment_id,)
    )
    if not content_hash:
        return pd.DataFrame()
    return q("""
        SELECT
            blf.fragment_id,
            blf.fragment_index,
            blf.fragment_text,
            b.bill_label,
            b.session_id,
            b.title,
            bv.version_label
        FROM bill_language_fragments blf
        JOIN bills b ON b.bill_pk = blf.bill_pk
        LEFT JOIN bill_versions bv ON bv.version_id = blf.version_id
        WHERE blf.content_hash = ?
          AND blf.fragment_id != ?
        ORDER BY b.session_id DESC
    """, (content_hash, fragment_id))


@st.cache_data(ttl=300)
def all_sessions() -> list[str]:
    df = q("SELECT session_id FROM sessions ORDER BY year DESC, session_code")
    return df["session_id"].tolist() if not df.empty else []


@st.cache_data(ttl=300)
def fiscal_notes(bill_pk: int) -> pd.DataFrame:
    return q("""
        SELECT issued_date, issuing_agency, fiscal_year,
               estimated_cost_min, estimated_cost_max, cost_direction
        FROM fiscal_notes
        WHERE bill_pk = ?
        ORDER BY issued_date DESC
    """, (bill_pk,))