"""queries/members.py — All member-related database queries."""
import pandas as pd
import streamlit as st
from .db import q, scalar, table_exists


@st.cache_data(ttl=300)
def all_members() -> pd.DataFrame:
    return q("""
        SELECT
            member_id,
            full_name,
            chamber,
            party,
            district,
            county_list,
            email,
            phone,
            active
        FROM members
        WHERE active = 1
        ORDER BY chamber, full_name
    """)


@st.cache_data(ttl=300)
def member_detail(member_id: int) -> dict:
    df = q("SELECT * FROM members WHERE member_id = ?", (member_id,))
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=300)
def member_floor_votes(member_id: int) -> pd.DataFrame:
    return q("""
        SELECT
            rc.roll_call_id,
            rc.vote_date,
            b.bill_label,
            b.bill_type,
            b.bill_number,
            b.title,
            b.short_desc,
            rc.motion_text,
            rc.reading_stage,
            rc.yes_count,
            rc.no_count,
            rc.present_count,
            rc.absent_count,
            rc.passed,
            mv.vote_cast
        FROM member_votes mv
        JOIN roll_calls rc ON rc.roll_call_id = mv.roll_call_id
        LEFT JOIN bills b ON b.bill_pk = rc.bill_pk
        WHERE mv.member_id = ?
          AND rc.vote_context = 'floor'
        ORDER BY rc.vote_date DESC
    """, (member_id,))


@st.cache_data(ttl=300)
def member_committee_votes(member_id: int) -> pd.DataFrame:
    return q("""
        SELECT
            rc.roll_call_id,
            rc.vote_date,
            COALESCE(c.name, rc.committee_name_raw) AS committee_name,
            b.bill_label,
            b.title,
            rc.motion_text,
            rc.yes_count,
            rc.no_count,
            rc.passed,
            mv.vote_cast
        FROM member_votes mv
        JOIN roll_calls rc ON rc.roll_call_id = mv.roll_call_id
        LEFT JOIN bills b ON b.bill_pk = rc.bill_pk
        LEFT JOIN committees c ON c.committee_id = rc.committee_id
        WHERE mv.member_id = ?
          AND rc.vote_context = 'committee'
        ORDER BY rc.vote_date DESC
    """, (member_id,))


@st.cache_data(ttl=300)
def member_sponsored_bills(member_id: int) -> pd.DataFrame:
    return q("""
        SELECT
            b.bill_label,
            b.bill_type,
            b.bill_number,
            b.chamber,
            b.title,
            b.short_desc,
            b.current_status,
            b.introduced_date,
            b.last_action_date,
            bs.sponsor_type,
            bm.action_count,
            bm.floor_vote_count,
            bm.sponsor_count
        FROM bill_sponsors bs
        JOIN bills b ON b.bill_pk = bs.bill_pk
        LEFT JOIN bill_metrics bm ON bm.bill_pk = b.bill_pk
        WHERE bs.member_id = ?
        ORDER BY bs.sponsor_type, b.bill_label
    """, (member_id,))


@st.cache_data(ttl=300)
def member_cross_aisle_votes(member_id: int) -> pd.DataFrame:
    return q("""
        SELECT
            ca.roll_call_id,
            rc.vote_date,
            b.bill_label,
            b.title,
            ca.vote_cast,
            ca.party_majority_vote
        FROM cross_aisle_votes ca
        JOIN roll_calls rc ON rc.roll_call_id = ca.roll_call_id
        LEFT JOIN bills b ON b.bill_pk = rc.bill_pk
        WHERE ca.member_id = ?
        ORDER BY rc.vote_date DESC
    """, (member_id,))


@st.cache_data(ttl=300)
def member_agreement_peers(member_id: int, limit: int = 20) -> pd.DataFrame:
    return q("""
        SELECT
            CASE WHEN ma.member_a = ? THEN ma.member_b ELSE ma.member_a END AS peer_id,
            m.full_name AS peer_name,
            m.party AS peer_party,
            m.chamber AS peer_chamber,
            ma.shared_votes,
            ma.agree_votes,
            ma.agreement_score
        FROM member_agreement ma
        JOIN members m ON m.member_id = CASE WHEN ma.member_a = ? THEN ma.member_b ELSE ma.member_a END
        WHERE (ma.member_a = ? OR ma.member_b = ?)
        ORDER BY ma.agreement_score DESC
        LIMIT ?
    """, (member_id, member_id, member_id, member_id, limit))


@st.cache_data(ttl=300)
def member_metrics_row(member_id: int) -> dict:
    df = q("SELECT * FROM member_metrics WHERE member_id = ?", (member_id,))
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=300)
def member_tag_alignment(member_id: int) -> pd.DataFrame:
    return q("""
        SELECT
            pt.label AS tag_label,
            pt.code,
            mta.tag_votes,
            mta.yes_on_tag,
            mta.no_on_tag,
            mta.yes_rate
        FROM member_tag_alignment mta
        JOIN policy_tags pt ON pt.tag_id = mta.tag_id
        WHERE mta.member_id = ?
        ORDER BY mta.tag_votes DESC
    """, (member_id,))


@st.cache_data(ttl=300)
def party_vote_breakdown(roll_call_id: int) -> pd.DataFrame:
    return q("""
        SELECT
            m.party,
            mv.vote_cast,
            COUNT(*) as n
        FROM member_votes mv
        JOIN members m ON m.member_id = mv.member_id
        WHERE mv.roll_call_id = ?
          AND mv.vote_cast IN ('yes','no','present','absent')
        GROUP BY m.party, mv.vote_cast
    """, (roll_call_id,))