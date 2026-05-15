"""pages/bills.py — Bill lookup with votes, actions, RSMo, language, lineage."""
import streamlit as st
import pandas as pd
import plotly.express as px
from components.ui import (
    page_header, stat_row, sec_label, info_grid,
    vote_badge, vote_bar_html, callout, empty_state, fragment_block,
    NAVY, GOLD, PARTY_COLORS, VOTE_COLORS
)
from components.charts import vote_donut, party_vote_bars, apply_theme


def render():
    page_header("Bills", "Search · votes · text · RSMo · language lineage")
    session = st.session_state.get("session_filter")

    try:
        from queries.bills import all_bills, all_sessions
        bills_df = all_bills(session)
        sessions = all_sessions()
    except Exception as e:
        callout(f"Could not load bills: {e}", "danger"); return

    if bills_df.empty:
        callout("No bill data found.", "warn"); return

    # ── Search panel ──────────────────────────────────────────────────────
    st.markdown('<div class="search-panel">', unsafe_allow_html=True)
    r1c1, r1c2, r1c3, r1c4 = st.columns([1, 2, 1, 1])
    with r1c1:
        b_num = st.text_input("Bill label/number", placeholder="SB 100, HB 45…",
                              value=st.session_state.pop("nav_bill_label",""),
                              key="bill_num_q")
    with r1c2:
        b_kw  = st.text_input("Title keyword(s)", placeholder="education, tax, healthcare…", key="bill_kw_q")
    with r1c3:
        b_type = st.selectbox("Type", ["All"] + sorted(bills_df["bill_type"].dropna().unique().tolist()),
                              key="bill_type_q")
    with r1c4:
        b_ch = st.selectbox("Chamber", ["All","house","senate"], key="bill_ch_q")
    st.markdown("</div>", unsafe_allow_html=True)

    # Filter
    df = bills_df.copy()
    if b_num.strip():
        df = df[df["bill_label"].str.upper().str.contains(b_num.strip().upper(), na=False) |
                df["bill_number"].astype(str).str.contains(b_num.strip(), na=False)]
    if b_kw.strip():
        for t in b_kw.strip().lower().split():
            df = df[df["title"].str.lower().str.contains(t, na=False) |
                    df["short_desc"].str.lower().str.contains(t, na=False)]
    if b_type != "All": df = df[df["bill_type"] == b_type]
    if b_ch   != "All": df = df[df["chamber"] == b_ch]

    if df.empty:
        empty_state("No bills match those filters."); return

    # Nav from session state
    nav_pk = st.session_state.pop("nav_bill_pk", None)
    default_idx = 0

    def _label(r):
        t = str(r.get("title","") or "")
        snip = t[:70]+"…" if len(t) > 70 else t
        return f"{r['bill_label']}  —  {snip}"

    options = ["— select a bill —"] + [_label(r) for _, r in df.iterrows()]
    pk_map  = {_label(r): r["bill_pk"] for _, r in df.iterrows()}

    if nav_pk:
        for i, (_, r) in enumerate(df.iterrows()):
            if r["bill_pk"] == nav_pk:
                default_idx = i + 1
                break

    st.caption(f"{len(df)} bill(s) match")
    chosen = st.selectbox("Select bill", options, index=default_idx, key="bill_select")
    if chosen == "— select a bill —":
        _render_bill_list(df); return

    bill_pk = pk_map[chosen]
    _render_bill_detail(bill_pk)


def _render_bill_list(df: pd.DataFrame):
    sec_label("Bill List")
    show_cols = [c for c in ["bill_label","chamber","session_id","title",
                              "current_status","introduced_date","last_action_date",
                              "floor_vote_count","sponsor_count"] if c in df.columns]
    st.dataframe(df[show_cols].head(500), use_container_width=True, hide_index=True,
                 column_config={
                     "bill_label": st.column_config.TextColumn("Bill"),
                     "title": st.column_config.TextColumn("Title", width="large"),
                     "current_status": st.column_config.TextColumn("Status"),
                     "floor_vote_count": st.column_config.NumberColumn("Floor Votes"),
                     "sponsor_count": st.column_config.NumberColumn("Sponsors"),
                 })


def _render_bill_detail(bill_pk: int):
    from queries.bills import (
        bill_detail, bill_floor_votes, bill_committee_votes,
        roll_call_member_votes, bill_actions, bill_sponsors,
        bill_rsmo_citations, bill_versions, bill_version_text,
        bill_policy_tags, bills_sharing_rsmo, bill_lineage_related,
        bill_language_fragments, fragment_matches, fiscal_notes
    )

    detail     = bill_detail(bill_pk)
    floor_df   = bill_floor_votes(bill_pk)
    comm_df    = bill_committee_votes(bill_pk)
    actions_df = bill_actions(bill_pk)
    sponsors_df= bill_sponsors(bill_pk)
    rsmo_df    = bill_rsmo_citations(bill_pk)
    versions_df= bill_versions(bill_pk)
    tags_df    = bill_policy_tags(bill_pk)
    lineage_df = bill_lineage_related(bill_pk)
    frags_df   = bill_language_fragments(bill_pk)
    fiscal_df  = fiscal_notes(bill_pk)

    label  = detail.get("bill_label","")
    title  = detail.get("title","")
    status = detail.get("current_status","")
    chamber= detail.get("chamber","")

    # Header
    st.markdown(f"""
    <div style="background:{NAVY};padding:1rem 1.4rem;border-radius:3px;margin-bottom:1rem">
        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;
                    color:{GOLD};margin-bottom:0.2rem">{label} · {chamber.title()} · {detail.get('session_id','')}</div>
        <div style="font-size:1.15rem;font-weight:600;color:#fff;line-height:1.3">{title}</div>
        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.68rem;
                    color:#6b7590;margin-top:0.3rem">{status}</div>
    </div>
    """, unsafe_allow_html=True)

    info_grid({
        "Bill Type":      detail.get("bill_type",""),
        "LR Number":      detail.get("lr_number",""),
        "Introduced":     detail.get("introduced_date",""),
        "Effective Date": detail.get("effective_date",""),
        "Last Action":    detail.get("last_action_date",""),
    })

    # Short desc
    if detail.get("short_desc"):
        callout(detail["short_desc"])

    # Tags
    if not tags_df.empty:
        tag_html = " &nbsp;".join(
            f'<span style="background:#e0f2fe;color:#0369a1;border-radius:2px;padding:0.1rem 0.4rem;'
            f'font-family:\'IBM Plex Mono\',monospace;font-size:0.65rem">'
            f'{row["label"]} {row["tau"]:.0%}</span>'
            for _, row in tags_df.iterrows()
        )
        st.markdown(f"**Tags:** {tag_html}", unsafe_allow_html=True)

    stat_row([
        ("Floor Votes", len(floor_df)),
        ("Committee Votes", len(comm_df)),
        ("Actions", len(actions_df)),
        ("Sponsors", len(sponsors_df)),
        ("RSMo Citations", len(rsmo_df)),
        ("Versions", len(versions_df)),
        ("Language Fragments", len(frags_df)),
        ("Lineage Links", len(lineage_df)),
    ])

    # ── Tabs ──────────────────────────────────────────────────────────────
    tabs = st.tabs([
        "Votes", "Action History", "Sponsors",
        "RSMo & Related", "Bill Text & Fragments",
        "Language Lineage", "Fiscal Notes"
    ])

    with tabs[0]:
        _tab_votes(bill_pk, floor_df, comm_df)

    with tabs[1]:
        _tab_actions(actions_df)

    with tabs[2]:
        _tab_sponsors(sponsors_df)

    with tabs[3]:
        _tab_rsmo(rsmo_df, bill_pk)

    with tabs[4]:
        _tab_text(versions_df, frags_df, bill_pk)

    with tabs[5]:
        _tab_lineage(lineage_df, bill_pk)

    with tabs[6]:
        _tab_fiscal(fiscal_df)


def _tab_votes(bill_pk, floor_df, comm_df):
    sec_label("Floor Votes")
    if floor_df.empty:
        empty_state("No floor votes.")
    else:
        for _, vrow in floor_df.iterrows():
            rc_id  = vrow.get("roll_call_id")
            date   = vrow.get("vote_date","")
            stage  = vrow.get("reading_stage","")
            motion = vrow.get("motion_text","")
            y = int(vrow.get("yes_count",0) or 0)
            n = int(vrow.get("no_count",0)  or 0)
            p = int(vrow.get("present_count",0) or 0)
            a = int(vrow.get("absent_count",0)  or 0)
            passed = vrow.get("passed")
            result = "✓ PASSED" if passed else "✗ FAILED"
            result_col = GOLD if passed else "#c95454"

            label = f"{date}  ·  {stage or motion or 'Vote'}  —  {y}Y / {n}N  [{result}]"
            with st.expander(label):
                st.markdown(vote_bar_html(y, n, p, a), unsafe_allow_html=True)
                st.markdown("<br>", unsafe_allow_html=True)

                from queries.bills import roll_call_member_votes
                mv = roll_call_member_votes(rc_id)
                if mv.empty:
                    st.info("No individual member votes on record.")
                else:
                    _render_member_vote_grid(mv)

                    # Party breakdown chart
                    party_breakdown = mv.groupby(["party","vote_cast"]).size().reset_index(name="n")
                    if not party_breakdown.empty:
                        fig = party_vote_bars(party_breakdown, "Party Breakdown")
                        st.plotly_chart(fig, use_container_width=True,
                                        config={"displayModeBar": False})

    sec_label("Committee Votes")
    if comm_df.empty:
        empty_state("No committee votes.")
    else:
        st.dataframe(comm_df, use_container_width=True, hide_index=True)


def _render_member_vote_grid(mv: pd.DataFrame):
    """Render member votes in a compact yes/no/present/absent grid."""
    kw = st.text_input("Filter members", key=f"mv_kw_{hash(str(mv.shape))}")
    vf = st.selectbox("Vote type", ["All"] + sorted(mv["vote_cast"].dropna().unique().tolist()),
                      key=f"mv_vf_{hash(str(mv.shape))}")
    disp = mv.copy()
    if kw.strip():
        disp = disp[disp["full_name"].str.lower().str.contains(kw.lower(), na=False)]
    if vf != "All":
        disp = disp[disp["vote_cast"] == vf]

    # Colour-coded grid
    for vote_type, colour, icon in [
        ("yes",     "#d1fae5", "✓"),
        ("no",      "#fee2e2", "✗"),
        ("present", "#fef3c7", "○"),
        ("absent",  "#f3f4f6", "–"),
    ]:
        subset = disp[disp["vote_cast"] == vote_type]
        if subset.empty: continue
        st.markdown(f"""
        <div style="margin:0.5rem 0 0.2rem">
            <span style="font-family:'IBM Plex Mono',monospace;font-size:0.62rem;
                         letter-spacing:0.1em;text-transform:uppercase;color:#6b7590">
                {icon} {vote_type.title()} ({len(subset)})
            </span>
        </div>
        <div style="display:flex;flex-wrap:wrap;gap:0.3rem;margin-bottom:0.6rem">
        """, unsafe_allow_html=True)
        chips = ""
        for _, mrow in subset.iterrows():
            p = mrow.get("party","")
            pcolors = {"R":"#fee2e2;color:#991b1b","D":"#dbeafe;color:#1e3a8a"}
            pc = pcolors.get(p, "#f3f4f6;color:#374151")
            chips += (f'<span style="background:{pc};border-radius:2px;padding:0.1rem 0.4rem;'
                      f'font-family:\'IBM Plex Mono\',monospace;font-size:0.65rem">'
                      f'{mrow.get("full_name","")}</span>')
        st.markdown(chips + "</div>", unsafe_allow_html=True)


def _tab_actions(actions_df: pd.DataFrame):
    sec_label("Action History")
    if actions_df.empty:
        empty_state("No actions on record."); return

    kw = st.text_input("Filter actions", key="act_kw")
    disp = actions_df.copy()
    if kw.strip():
        disp = disp[disp["action_text"].str.lower().str.contains(kw.strip().lower(), na=False)]

    st.caption(f"{len(disp)} of {len(actions_df)} actions")

    for _, row in disp.iterrows():
        date   = row.get("action_date","")
        ch     = row.get("chamber","")
        text   = row.get("action_text","")
        vt     = row.get("vote_type","")
        jpage  = row.get("journal_page","")
        vt_tag = f' <span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;background:#fef3c7;padding:0 3px;border-radius:1px">{vt}</span>' if vt else ""
        st.markdown(f"""
        <div style="display:flex;gap:0.8rem;padding:0.4rem 0;
                    border-bottom:1px solid #f3f4f6;align-items:baseline">
            <span style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;
                         color:#6b7590;white-space:nowrap;min-width:95px">{date}</span>
            <span style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;
                         color:{GOLD};min-width:50px">{ch}</span>
            <span style="font-size:0.87rem;color:#111827">{text}{vt_tag}</span>
            {f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;color:#9ca3af;white-space:nowrap">p.{jpage}</span>' if jpage else ''}
        </div>
        """, unsafe_allow_html=True)


def _tab_sponsors(sponsors_df: pd.DataFrame):
    sec_label("Sponsors")
    if sponsors_df.empty:
        empty_state("No sponsors on record."); return

    primary = sponsors_df[sponsors_df["sponsor_type"] == "primary"]
    cospon  = sponsors_df[sponsors_df["sponsor_type"] == "cosponsor"]

    if not primary.empty:
        st.markdown("**Primary Sponsor(s)**")
        for _, row in primary.iterrows():
            p = row.get("party","")
            pcolors = {"R":"#fee2e2;color:#991b1b","D":"#dbeafe;color:#1e3a8a"}
            pc = pcolors.get(p,"#f3f4f6;color:#374151")
            st.markdown(f"""
            <span style="background:{pc};border-radius:2px;padding:0.15rem 0.6rem;
                         font-family:'IBM Plex Mono',monospace;font-size:0.78rem;
                         font-weight:600">{row['full_name']} ({p})</span>
            &nbsp; District {row.get('district','—')}
            """, unsafe_allow_html=True)

    if not cospon.empty:
        sec_label(f"Co-Sponsors ({len(cospon)})")
        chips = ""
        for _, row in cospon.iterrows():
            p = row.get("party","")
            pcolors = {"R":"#fee2e2;color:#991b1b","D":"#dbeafe;color:#1e3a8a"}
            pc = pcolors.get(p,"#f3f4f6;color:#374151")
            chips += (f'<span style="background:{pc};border-radius:2px;padding:0.1rem 0.4rem;'
                      f'font-family:\'IBM Plex Mono\',monospace;font-size:0.7rem;margin:0.1rem">'
                      f'{row["full_name"]}</span> ')
        st.markdown(f'<div style="line-height:2">{chips}</div>', unsafe_allow_html=True)


def _tab_rsmo(rsmo_df: pd.DataFrame, bill_pk: int):
    sec_label("RSMo Citations")
    if rsmo_df.empty:
        empty_state("No RSMo citations extracted for this bill."); return

    st.dataframe(rsmo_df, use_container_width=True, hide_index=True,
                 column_config={
                     "chapter": st.column_config.NumberColumn("Chapter"),
                     "section": st.column_config.TextColumn("Section"),
                     "citation_type": st.column_config.TextColumn("Type"),
                     "section_title": st.column_config.TextColumn("Title"),
                 })

    sec_label("Other Bills Touching the Same Sections")
    from queries.bills import bills_sharing_rsmo
    related = bills_sharing_rsmo(bill_pk)
    if related.empty:
        st.caption("No other bills found sharing RSMo sections."); return

    kw = st.text_input("Filter related bills", key="rsmo_kw")
    disp = related.copy()
    if kw.strip():
        for t in kw.strip().lower().split():
            disp = disp[disp.apply(lambda r: t in " ".join(str(v) for v in r.values).lower(), axis=1)]

    st.caption(f"{len(disp)} related bill(s)")
    for _, row in disp.head(50).iterrows():
        col1, col2 = st.columns([1, 8])
        with col1:
            bl = row.get("bill_label","")
            if bl and st.button(bl, key=f"rsmo_nav_{row.get('bill_pk','')}_{bl}"):
                st.session_state["nav_bill_pk"] = row.get("bill_pk")
                st.session_state["nav_bill_label"] = bl
                st.rerun()
        with col2:
            st.markdown(f"""
            <span style="font-size:0.88rem;color:#111827">{str(row.get('title',''))[:90]}</span>
            <span style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;color:#6b7590">
            &nbsp;· {row.get('session_id','')} · §{row.get('section','')} ({row.get('citation_type','')})
            </span>
            """, unsafe_allow_html=True)


def _tab_text(versions_df: pd.DataFrame, frags_df: pd.DataFrame, bill_pk: int):
    sec_label("Bill Text Versions")
    if versions_df.empty:
        empty_state("No bill text versions on record."); return

    # Version selector
    ver_labels = versions_df["version_label"].fillna("Unknown").tolist()
    ver_ids    = versions_df["version_id"].tolist()
    chosen_ver = st.selectbox("Version", ver_labels, key="bill_ver_select")
    ver_idx    = ver_labels.index(chosen_ver)
    ver_id     = ver_ids[ver_idx]

    ver_row = versions_df.iloc[ver_idx]
    info_grid({
        "Label":       ver_row.get("version_label",""),
        "Date":        ver_row.get("version_date",""),
        "Stage":       ver_row.get("stage",""),
        "Word Count":  f"{int(ver_row.get('word_count',0) or 0):,}",
        "Source URL":  ver_row.get("url",""),
    })

    kw_text = st.text_input("Highlight keyword in text", key="bill_text_kw")

    from queries.bills import bill_version_text
    full_text = bill_version_text(ver_id)
    if full_text:
        with st.expander("Full Text", expanded=False):
            if kw_text.strip():
                highlighted = full_text.replace(
                    kw_text, f"**{kw_text}**"
                )
                st.text_area("", value=highlighted[:20000], height=500,
                             disabled=True, key=f"full_text_{ver_id}")
            else:
                st.text_area("", value=full_text[:20000], height=500,
                             disabled=True, key=f"full_text2_{ver_id}")
    else:
        st.info("Full text not yet fetched for this version.")

    # Fragments
    sec_label(f"Language Fragments ({len(frags_df)})")
    if frags_df.empty:
        st.caption("No fragments extracted yet (run resurrection.py)."); return

    kw_frag = st.text_input("Search within fragments", key="bill_frag_kw")
    fdf = frags_df[frags_df["version_label"] == chosen_ver] if not frags_df.empty else frags_df

    if kw_frag.strip():
        fdf = fdf[fdf["fragment_text"].str.lower().str.contains(kw_frag.strip().lower(), na=False)]

    st.caption(f"{len(fdf)} fragment(s) for this version")

    for _, frow in fdf.head(30).iterrows():
        frag_id   = frow.get("fragment_id")
        ftype     = frow.get("fragment_type","")
        fidx      = frow.get("fragment_index","")
        ftext     = frow.get("fragment_text","")[:600]

        col1, col2 = st.columns([6, 1])
        with col1:
            fragment_block(
                ftext,
                meta=f"Fragment #{fidx} · {ftype}",
                highlight=kw_frag.strip() if kw_frag.strip() else ""
            )
        with col2:
            if st.button("Find matches", key=f"frag_match_{frag_id}"):
                st.session_state[f"show_matches_{frag_id}"] = True

        if st.session_state.get(f"show_matches_{frag_id}"):
            from queries.bills import fragment_matches
            matches = fragment_matches(frag_id)
            if matches.empty:
                st.caption("No exact-hash matches found in other bills.")
            else:
                st.success(f"Found {len(matches)} match(es) in other bills:")
                for _, mrow in matches.iterrows():
                    st.markdown(f"""
                    <div style="background:#eff6ff;border-left:3px solid #7ba7d8;
                                padding:0.5rem 0.8rem;margin:0.3rem 0;border-radius:2px">
                        <span style="font-family:'IBM Plex Mono',monospace;font-weight:600;
                                     font-size:0.75rem">{mrow.get('bill_label','')}</span>
                        <span style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;
                                     color:#6b7590"> · {mrow.get('session_id','')} · {mrow.get('version_label','')}</span>
                        <div style="font-size:0.82rem;color:#111827;margin-top:0.2rem">
                            {str(mrow.get('title',''))[:80]}
                        </div>
                        <div style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;
                                     color:#6b7590;margin-top:0.2rem">
                            Fragment #{mrow.get('fragment_index','')}: {str(mrow.get('fragment_text',''))[:150]}…
                        </div>
                    </div>
                    """, unsafe_allow_html=True)


def _tab_lineage(lineage_df: pd.DataFrame, bill_pk: int):
    sec_label("Language Lineage")
    if lineage_df.empty:
        empty_state("No lineage relationships found (requires resurrection.py to have run)."); return

    # Summary badges
    match_types = lineage_df["match_type"].value_counts()
    badges = " &nbsp;".join(
        f'<span style="background:#f3f4f6;border-radius:2px;padding:0.1rem 0.45rem;'
        f'font-family:\'IBM Plex Mono\',monospace;font-size:0.65rem">'
        f'{mt}: {cnt}</span>'
        for mt, cnt in match_types.items()
    )
    st.markdown(f"**Match types:** {badges}", unsafe_allow_html=True)

    min_score = st.slider("Min similarity score", 0.0, 1.0, 0.5, 0.05, key="lin_score")
    disp = lineage_df[lineage_df["similarity_score"] >= min_score].copy()

    c1, c2 = st.columns([2, 1])
    with c1:
        # Scatter: similarity vs containment
        if not disp.empty and "containment_score" in disp.columns:
            fig = px.scatter(
                disp, x="similarity_score", y="containment_score",
                color="match_type", hover_name="related_bill",
                title="Similarity vs Containment",
                color_discrete_sequence=[GOLD, "#7ba7d8", "#4caf7d", "#c95454", "#9b7ed8"],
            )
            st.plotly_chart(apply_theme(fig), use_container_width=True,
                            config={"displayModeBar": False})
    with c2:
        sec_label("Lineage Cards")

    st.markdown("---")
    for _, row in disp.iterrows():
        direction  = row.get("direction","")
        related_bl = row.get("related_bill","")
        rel_title  = str(row.get("related_title","") or "")[:90]
        rel_sess   = row.get("related_session","")
        match_type = row.get("match_type","")
        sim        = row.get("similarity_score", 0) or 0
        contain    = row.get("containment_score", 0)
        method     = row.get("method","")
        gran       = row.get("granularity","")

        dir_color = GOLD if direction == "ancestor-of" else "#7ba7d8"
        dir_icon  = "↑ ancestor" if direction == "ancestor-of" else "↓ descended"

        st.markdown(f"""
        <div style="border:1px solid #e5e7eb;border-left:4px solid {dir_color};
                    padding:0.7rem 1rem;border-radius:3px;margin:0.4rem 0">
            <div style="display:flex;justify-content:space-between;align-items:baseline">
                <div>
                    <span style="font-family:'IBM Plex Mono',monospace;font-weight:600;
                                 font-size:0.82rem;color:{NAVY}">{related_bl}</span>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;
                                 color:#6b7590"> · {rel_sess} · {match_type} · {gran}</span>
                </div>
                <div style="text-align:right">
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:0.62rem;
                                 color:{dir_color}">{dir_icon}</span>
                    <br>
                    <span style="font-family:'IBM Plex Mono',monospace;font-size:0.7rem;
                                 font-weight:600">{sim:.0%} similar</span>
                    {f'<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.62rem;color:#6b7590"> · {contain:.0%} contained</span>' if contain else ''}
                </div>
            </div>
            <div style="font-size:0.87rem;color:#374151;margin-top:0.3rem">{rel_title}</div>
            <div style="font-family:'IBM Plex Mono',monospace;font-size:0.6rem;
                         color:#9ca3af;margin-top:0.15rem">method: {method}</div>
        </div>
        """, unsafe_allow_html=True)

        if st.button(f"Go to {related_bl}", key=f"lin_nav_{row.get('lang_lineage_id','')}"):
            from queries.db import scalar
            bpk = scalar("SELECT bill_pk FROM bills WHERE bill_label=?", (related_bl,))
            if bpk:
                st.session_state["nav_bill_pk"] = bpk
                st.session_state["nav_bill_label"] = related_bl
                st.rerun()


def _tab_fiscal(fiscal_df: pd.DataFrame):
    sec_label("Fiscal Notes")
    if fiscal_df.empty:
        empty_state("No fiscal notes on record."); return
    st.dataframe(fiscal_df, use_container_width=True, hide_index=True)