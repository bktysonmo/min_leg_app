"""pages/dashboard.py — Overview dashboard."""
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from components.ui import page_header, stat_row, sec_label, apply_theme, callout, GOLD, NAVY
from components.charts import status_funnel, timeline_chart, bar_chart


def render():
    page_header("Dashboard", "Missouri Legislative Session Overview")
    session = st.session_state.get("session_filter")

    try:
        from queries.analytics import (
            dashboard_stats, bills_by_status, bills_by_type,
            vote_timeline, tag_distribution
        )
        stats = dashboard_stats()
        status_df = bills_by_status(session)
        type_df   = bills_by_type(session)
        timeline_df = vote_timeline(session)
        tag_df    = tag_distribution()
        db_ok = True
    except Exception as e:
        st.warning(f"Could not load data: {e}")
        db_ok = False

    if not db_ok:
        callout(
            "⚠ Database not connected. Set the <code>mo_leg_DB</code> environment variable "
            "to the path of your <code>mo_leg.db</code> file, then reload.",
            "warn"
        )
        st.code("export mo_leg_DB=/path/to/mo_leg.db\nstreamlit run app.py")
        return

    # ── Stats row ────────────────────────────────────────────────────────
    stat_row([
        ("Bills", f"{stats['bills']:,}"),
        ("Members", f"{stats['members']:,}"),
        ("Floor Votes", f"{stats['floor_votes']:,}"),
        ("Committee Votes", f"{stats['comm_votes']:,}"),
        ("Sessions", f"{stats['sessions']:,}"),
        ("Tagged Bills", f"{stats['tags']:,}"),
        ("Language Pairs", f"{stats['lineage_pairs']:,}"),
        ("Text Fragments", f"{stats['fragments']:,}"),
    ])

    # ── Population status ─────────────────────────────────────────────────
    sec_label("Data Population Status")
    populated = {
        "House Bills":        ("✓", "green"),
        "House Members":      ("✓", "green"),
        "Senate Bills":       ("✓", "green"),
        "Senate Members":     ("✓", "green"),
        "Journal Actions":    ("✓", "green"),
        "Senate Actions":     ("✓", "green"),
        "Bill Lineage":       ("✓", "green"),
        "MEC Finance":        ("○", "orange"),
        "Coalition Intel":    ("○", "orange"),
        "Ideology Scores":    ("○", "orange"),
        "Policy Tagger":      ("○", "orange"),
        "Sponsorship Scores": ("○", "orange"),
    }
    cols = st.columns(6)
    for i, (label, (icon, color)) in enumerate(populated.items()):
        c = "#4caf7d" if color == "green" else "#e8c84a"
        with cols[i % 6]:
            st.markdown(f"""
            <div style="background:#f8f7f4;border-left:3px solid {c};
                        padding:0.4rem 0.6rem;border-radius:2px;margin-bottom:0.4rem">
                <span style="color:{c};font-family:'IBM Plex Mono',monospace;
                             font-size:0.65rem">{icon}</span>
                <span style="font-family:'IBM Plex Mono',monospace;font-size:0.65rem;
                             margin-left:0.3rem;color:#374151">{label}</span>
            </div>
            """, unsafe_allow_html=True)

    # ── Charts row 1 ──────────────────────────────────────────────────────
    c1, c2 = st.columns([3, 2])

    with c1:
        sec_label("Bill Status Distribution")
        if not status_df.empty:
            fig = status_funnel(status_df)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("No status data.")

    with c2:
        sec_label("Bills by Type")
        if not type_df.empty:
            fig = bar_chart(type_df, x="bill_type", y="count", color="chamber",
                            title="", horizontal=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # ── Vote timeline ─────────────────────────────────────────────────────
    sec_label("Vote Activity Over Time")
    if not timeline_df.empty:
        tl = timeline_df.copy()
        tl["vote_date"] = tl["vote_date"].astype(str)
        fig = px.bar(
            tl, x="vote_date", y="votes",
            color="vote_context",
            color_discrete_map={"floor": NAVY, "committee": GOLD},
            title="",
            barmode="stack",
        )
        fig.update_layout(
            xaxis_title="", yaxis_title="Votes",
            legend_title="Context",
        )
        from components.ui import apply_theme
        st.plotly_chart(apply_theme(fig), use_container_width=True,
                        config={"displayModeBar": False})

    # ── Policy tags ───────────────────────────────────────────────────────
    if not tag_df.empty:
        sec_label("Top Policy Tags (primary only)")
        fig = px.treemap(
            tag_df.head(20),
            path=["label"], values="bill_count",
            color="avg_confidence",
            color_continuous_scale=[[0, "#e5e7eb"], [0.5, GOLD], [1, NAVY]],
            title="",
        )
        fig.update_traces(textfont=dict(family="IBM Plex Mono, monospace"))
        from components.ui import apply_theme
        st.plotly_chart(apply_theme(fig), use_container_width=True,
                        config={"displayModeBar": False})