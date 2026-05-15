"""pages/legislators.py — Legislator lookup with full vote history, metrics, and alignment."""
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from components.ui import (
    page_header, stat_row, sec_label, info_grid,
    party_badge, vote_badge, vote_bar_html, callout, empty_state,
    PARTY_COLORS, VOTE_COLORS, NAVY, GOLD
)
from components.charts import (
    vote_donut, party_vote_bars, scatter_chart, apply_theme
)


def render():
    page_header("Legislators", "Member search · vote history · agreement · alignment")
    session = st.session_state.get("session_filter")

    try:
        from queries.members import all_members
        members_df = all_members()
    except Exception as e:
        callout(f"Could not load members: {e}", "danger")
        return

    if members_df.empty:
        callout("No member data found. Run the member scrapers first.", "warn")
        return

    # ── Search panel ──────────────────────────────────────────────────────
    st.markdown('<div class="search-panel">', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
    with c1:
        name_q = st.text_input("Name (first, last, or partial)",
                               placeholder="Smith, Jane, etc.", key="leg_name_q")
    with c2:
        chamber_f = st.selectbox("Chamber", ["All", "senate", "house"], key="leg_chamber_f")
    with c3:
        party_f = st.selectbox("Party", ["All", "R", "D", "I"], key="leg_party_f")
    with c4:
        active_f = st.checkbox("Active only", value=True, key="leg_active_f")
    st.markdown("</div>", unsafe_allow_html=True)

    # Filter
    df = members_df.copy()
    if name_q.strip():
        toks = name_q.strip().lower().split()
        mask = df["full_name"].str.lower().apply(lambda n: all(t in n for t in toks))
        df = df[mask]
    if chamber_f != "All":
        df = df[df["chamber"] == chamber_f]
    if party_f != "All":
        df = df[df["party"] == party_f]
    if active_f:
        df = df[df["active"] == 1]

    if df.empty:
        empty_state("No members match those filters.")
        return

    # Format selector labels
    def _label(row):
        ch = "Sen." if row["chamber"] == "senate" else "Rep."
        p  = row.get("party","")
        return f"{ch} {row['full_name']} ({p})"

    options = ["— select a member —"] + [_label(r) for _, r in df.iterrows()]
    id_map  = {_label(r): r["member_id"] for _, r in df.iterrows()}
    st.caption(f"{len(df)} member(s) match")

    # Check nav
    nav_id = st.session_state.pop("nav_member_id", None)
    default_idx = 0
    if nav_id:
        for i, (_, r) in enumerate(df.iterrows()):
            if r["member_id"] == nav_id:
                default_idx = i + 1
                break

    chosen_label = st.selectbox("Select member", options, index=default_idx, key="leg_select")
    if chosen_label == "— select a member —":
        _render_member_list_overview(df)
        return

    member_id = id_map[chosen_label]
    _render_member_detail(member_id)


def _render_member_list_overview(df: pd.DataFrame):
    """Show list-level summary when no specific member selected."""
    sec_label("Member Roster")
    cols = [c for c in ["full_name","chamber","party","district","county_list","email"]
            if c in df.columns]
    disp = df[cols].copy()
    disp["party"] = disp["party"].apply(
        lambda p: p if pd.isna(p) else p
    )
    st.dataframe(disp, use_container_width=True, hide_index=True,
                 column_config={
                     "full_name": st.column_config.TextColumn("Name"),
                     "chamber":   st.column_config.TextColumn("Chamber"),
                     "party":     st.column_config.TextColumn("Party"),
                     "district":  st.column_config.NumberColumn("District"),
                     "county_list": st.column_config.TextColumn("Counties"),
                     "email":     st.column_config.TextColumn("Email"),
                 })

    # Party/chamber breakdown
    c1, c2 = st.columns(2)
    with c1:
        sec_label("By Party")
        pc = df["party"].value_counts().reset_index()
        pc.columns = ["party","count"]
        fig = px.pie(pc, names="party", values="count",
                     color="party", color_discrete_map=PARTY_COLORS,
                     hole=0.4)
        fig.update_traces(textfont=dict(family="IBM Plex Mono, monospace", size=10))
        st.plotly_chart(apply_theme(fig), use_container_width=True,
                        config={"displayModeBar": False})
    with c2:
        sec_label("By Chamber")
        cc = df["chamber"].value_counts().reset_index()
        cc.columns = ["chamber","count"]
        fig2 = px.bar(cc, x="chamber", y="count", color="chamber",
                      color_discrete_map={"senate": NAVY, "house": GOLD})
        st.plotly_chart(apply_theme(fig2), use_container_width=True,
                        config={"displayModeBar": False})


def _render_member_detail(member_id: int):
    from queries.members import (
        member_detail, member_metrics_row, member_floor_votes,
        member_committee_votes, member_sponsored_bills,
        member_cross_aisle_votes, member_agreement_peers,
        member_tag_alignment
    )

    detail  = member_detail(member_id)
    metrics = member_metrics_row(member_id)
    floor_df   = member_floor_votes(member_id)
    comm_df    = member_committee_votes(member_id)
    sponsored  = member_sponsored_bills(member_id)
    cross_df   = member_cross_aisle_votes(member_id)
    peers_df   = member_agreement_peers(member_id, 25)
    tag_df     = member_tag_alignment(member_id)

    # Profile header
    party = detail.get("party","")
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:1.2rem;
                background:{NAVY};padding:1rem 1.4rem;border-radius:3px;margin-bottom:1rem">
        <div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:1.2rem;
                        font-weight:600;color:#fff">{detail.get('full_name','')}</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;color:#c8a96e;margin-top:0.2rem">
                {detail.get('chamber','').title()} · District {detail.get('district','—')} · {party}
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    info_grid({
        "Phone":   detail.get("phone",""),
        "Email":   detail.get("email",""),
        "Counties": detail.get("county_list",""),
        "First Elected": detail.get("first_elected",""),
        "Term End": detail.get("term_end",""),
    })

    # Metrics
    vc   = metrics.get("votes_cast",0) or 0
    yeas = metrics.get("yes_votes",0) or 0
    nays = metrics.get("no_votes",0) or 0
    pres = metrics.get("present_votes",0) or 0
    abs_ = metrics.get("absent_votes",0) or 0
    ar   = metrics.get("absence_rate",0) or 0
    ps   = metrics.get("primary_sponsored",0) or 0
    cos  = metrics.get("co_sponsored",0) or 0

    stat_row([
        ("Floor Votes", f"{vc:,}"),
        ("Yea", f"{yeas:,}"),
        ("Nay", f"{nays:,}"),
        ("Absent", f"{abs_:,}"),
        ("Absence Rate", f"{ar*100:.1f}%"),
        ("Primary Sponsor", f"{ps:,}"),
        ("Co-Sponsor", f"{cos:,}"),
        ("Cross-Aisle", f"{len(cross_df):,}"),
    ])

    # ── Tabs ──────────────────────────────────────────────────────────────
    tabs = st.tabs([
        "Floor Votes", "Committee Votes", "Sponsored Bills",
        "Cross-Aisle", "Agreement Peers", "Policy Alignment"
    ])

    with tabs[0]:
        _tab_floor_votes(floor_df, member_id)

    with tabs[1]:
        _tab_committee_votes(comm_df)

    with tabs[2]:
        _tab_sponsored(sponsored)

    with tabs[3]:
        _tab_cross_aisle(cross_df, metrics)

    with tabs[4]:
        _tab_peers(peers_df)

    with tabs[5]:
        _tab_tags(tag_df)


def _tab_floor_votes(floor_df: pd.DataFrame, member_id: int):
    sec_label("Floor Vote History")
    if floor_df.empty:
        empty_state("No floor votes on record."); return

    # Visualise vote breakdown
    vote_counts = floor_df["vote_cast"].value_counts()
    c1, c2 = st.columns([1, 2])
    with c1:
        fig = vote_donut(
            vote_counts.get("yes",0), vote_counts.get("no",0),
            vote_counts.get("present",0), vote_counts.get("absent",0),
            "Vote Breakdown"
        )
        st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
    with c2:
        # Vote trend over time
        tdf = floor_df.copy()
        tdf["vote_date"] = pd.to_datetime(tdf["vote_date"], errors="coerce")
        tdf = tdf.dropna(subset=["vote_date"])
        tdf["month"] = tdf["vote_date"].dt.to_period("M").astype(str)
        trend = tdf.groupby(["month","vote_cast"]).size().reset_index(name="count")
        if not trend.empty:
            fig2 = px.bar(trend, x="month", y="count", color="vote_cast",
                          color_discrete_map=VOTE_COLORS, barmode="stack",
                          title="Votes by Month")
            fig2.update_xaxes(tickangle=45)
            st.plotly_chart(apply_theme(fig2), use_container_width=True,
                            config={"displayModeBar": False})

    # Filters
    sec_label("Vote Detail")
    fc1, fc2, fc3 = st.columns([3, 1, 1])
    with fc1: kw = st.text_input("Filter (bill, description, motion)", key="leg_fv_kw")
    with fc2:
        vote_opts = ["All"] + sorted(floor_df["vote_cast"].dropna().unique().tolist())
        vf = st.selectbox("Vote", vote_opts, key="leg_fv_vf")
    with fc3:
        stage_opts = ["All"] + sorted(floor_df["reading_stage"].dropna().unique().tolist())
        sf = st.selectbox("Stage", stage_opts, key="leg_fv_sf")

    disp = floor_df.copy()
    if kw.strip():
        for t in kw.strip().lower().split():
            disp = disp[disp.apply(lambda r: t in " ".join(str(v) for v in r.values).lower(), axis=1)]
    if vf != "All": disp = disp[disp["vote_cast"] == vf]
    if sf != "All": disp = disp[disp["reading_stage"] == sf]

    st.caption(f"{len(disp)} of {len(floor_df)} votes")

    for _, row in disp.head(200).iterrows():
        b  = row.get("bill_label","")
        t  = str(row.get("title","") or row.get("short_desc","") or "")[:80]
        d  = row.get("vote_date","")
        vc = row.get("vote_cast","")
        mo = row.get("motion_text","")
        y  = row.get("yes_count",0) or 0
        n  = row.get("no_count",0) or 0
        ps = row.get("passed")
        ps_icon = "✓" if ps else "✗"

        col_b, col_info = st.columns([1, 8])
        with col_b:
            if b and st.button(b, key=f"leg_fv_nav_{row.get('roll_call_id','')}_{b}",
                               help=f"Go to {b}"):
                # Get bill_pk then navigate
                from queries.db import scalar
                bpk = scalar("SELECT bill_pk FROM bills WHERE bill_label=?", (b,))
                if bpk:
                    st.session_state["nav_bill_pk"] = bpk
                    st.session_state["nav_bill_label"] = b
        with col_info:
            st.markdown(
                f'{vote_badge(vc)} &nbsp;'
                f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.74rem;color:#374151">'
                f'{d} &nbsp;· &nbsp;{t[:60]} &nbsp;· &nbsp;{y}Y/{n}N &nbsp;{ps_icon}'
                f'</span>',
                unsafe_allow_html=True
            )


def _tab_committee_votes(comm_df: pd.DataFrame):
    sec_label("Committee Vote History")
    if comm_df.empty:
        empty_state("No committee votes on record."); return

    kw = st.text_input("Filter", key="leg_cv_kw")
    disp = comm_df.copy()
    if kw.strip():
        for t in kw.strip().lower().split():
            disp = disp[disp.apply(lambda r: t in " ".join(str(v) for v in r.values).lower(), axis=1)]

    st.caption(f"{len(disp)} records")
    show_cols = [c for c in ["vote_date","committee_name","bill_label","motion_text",
                              "yes_count","no_count","passed","vote_cast"] if c in disp.columns]
    st.dataframe(disp[show_cols], use_container_width=True, hide_index=True)


def _tab_sponsored(sponsored: pd.DataFrame):
    sec_label("Sponsored Legislation")
    if sponsored.empty:
        empty_state("No sponsored bills found."); return

    # Summary
    primary = sponsored[sponsored["sponsor_type"] == "primary"]
    cospon  = sponsored[sponsored["sponsor_type"] == "cosponsor"]

    stat_row([
        ("Primary Sponsor", len(primary)),
        ("Co-Sponsor", len(cospon)),
    ])

    kw = st.text_input("Filter bills", key="leg_spon_kw")
    disp = sponsored.copy()
    if kw.strip():
        for t in kw.strip().lower().split():
            disp = disp[disp.apply(lambda r: t in " ".join(str(v) for v in r.values).lower(), axis=1)]

    for _, row in disp.iterrows():
        bl = row.get("bill_label","")
        t  = str(row.get("title","") or "")[:90]
        st_ = row.get("current_status","")
        stype = row.get("sponsor_type","")

        col1, col2 = st.columns([1, 7])
        with col1:
            st.markdown(f"""
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.78rem;
                        font-weight:600;color:{NAVY};padding-top:0.2rem">{bl}</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.6rem;color:#c8a96e">{stype}</div>
            """, unsafe_allow_html=True)
        with col2:
            st.markdown(f"""
            <div style="font-size:0.88rem;color:#111827;padding-top:0.1rem">{t}</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#6b7590">{st_}</div>
            """, unsafe_allow_html=True)
        st.markdown('<hr style="border:none;border-top:1px solid #f3f4f6;margin:0.25rem 0">',
                    unsafe_allow_html=True)


def _tab_cross_aisle(cross_df: pd.DataFrame, metrics: dict):
    sec_label("Cross-Aisle Votes")
    vc = metrics.get("votes_cast",0) or 0
    pct = f"{len(cross_df)/vc*100:.1f}%" if vc > 0 else "—"
    stat_row([("Cross-Aisle Votes", len(cross_df)), ("of Floor Votes", pct)])

    if cross_df.empty:
        empty_state("No cross-aisle votes recorded."); return

    st.caption("Votes where this member voted against their party's majority position.")
    show = [c for c in ["vote_date","bill_label","title","vote_cast","party_majority_vote"]
            if c in cross_df.columns]
    st.dataframe(cross_df[show], use_container_width=True, hide_index=True)


def _tab_peers(peers_df: pd.DataFrame):
    sec_label("Agreement Peers (≥20 shared floor votes)")
    if peers_df.empty:
        empty_state("No agreement data yet (requires member_agreement table populated)."); return

    c1, c2 = st.columns([2, 1])
    with c1:
        fig = px.bar(
            peers_df.head(15).sort_values("agreement_score"),
            x="agreement_score", y="peer_name",
            color="peer_party", orientation="h",
            color_discrete_map=PARTY_COLORS,
            title="Top Agreement Peers",
            range_x=[0, 1],
            text="agreement_score",
        )
        fig.update_traces(texttemplate="%{text:.2f}",
                          textfont=dict(family="IBM Plex Mono, monospace", size=9))
        st.plotly_chart(apply_theme(fig), use_container_width=True,
                        config={"displayModeBar": False})
    with c2:
        st.dataframe(peers_df[["peer_name","peer_party","shared_votes","agreement_score"]],
                     use_container_width=True, hide_index=True)


def _tab_tags(tag_df: pd.DataFrame):
    sec_label("Policy Tag Alignment")
    if tag_df.empty:
        empty_state("No tag alignment data yet (requires tagger to run)."); return

    fig = px.bar(
        tag_df.head(15).sort_values("yes_rate"),
        x="yes_rate", y="tag_label",
        orientation="h",
        color="yes_rate",
        color_continuous_scale=[[0, "#fee2e2"], [0.5, "#fef3c7"], [1, "#d1fae5"]],
        title="Yes-Vote Rate by Policy Tag",
        text="tag_votes",
    )
    fig.update_traces(texttemplate="%{text} votes",
                      textfont=dict(family="IBM Plex Mono, monospace", size=9))
    fig.update_layout(xaxis_range=[0, 1], xaxis_tickformat=".0%")
    st.plotly_chart(apply_theme(fig), use_container_width=True,
                    config={"displayModeBar": False})
    st.dataframe(tag_df, use_container_width=True, hide_index=True)