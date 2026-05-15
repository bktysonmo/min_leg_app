"""components/charts.py — Reusable Plotly chart builders."""
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import networkx as nx
import numpy as np
from .ui import (
    apply_theme, NAVY, GOLD, SLATE, RED, GREEN, BLUE, YELLOW,
    PURPLE, MUTED, GHOST, PARTY_COLORS, VOTE_COLORS
)


def vote_donut(yes: int, no: int, present: int = 0, absent: int = 0,
               title: str = "") -> go.Figure:
    labels = ["Yes", "No", "Present", "Absent"]
    values = [yes, no, present, absent]
    colors = [GREEN, RED, YELLOW, MUTED]
    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        hole=0.55,
        marker=dict(colors=colors, line=dict(color="#fff", width=2)),
        textinfo="label+percent",
        textfont=dict(family="IBM Plex Mono, monospace", size=10),
        hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
    ))
    total = yes + no
    pct = f"{yes/total*100:.0f}%" if total else "—"
    fig.add_annotation(
        text=f"<b>{pct}</b><br>passed",
        x=0.5, y=0.5, showarrow=False,
        font=dict(family="IBM Plex Mono, monospace", size=12, color=NAVY),
    )
    fig.update_layout(title_text=title, title_font_size=12, **{
        k: v for k, v in apply_theme(go.Figure()).layout.to_plotly_json().items()
    })
    return apply_theme(fig)


def party_vote_bars(df: pd.DataFrame, title: str = "Party Vote Breakdown") -> go.Figure:
    """df must have columns: party, vote_cast, n"""
    parties = df["party"].unique()
    vote_types = ["yes", "no", "present", "absent"]
    fig = go.Figure()
    for vt in vote_types:
        subset = df[df["vote_cast"] == vt]
        fig.add_trace(go.Bar(
            name=vt.title(),
            x=subset["party"],
            y=subset["n"],
            marker_color=VOTE_COLORS.get(vt, MUTED),
            text=subset["n"],
            textposition="auto",
            textfont=dict(family="IBM Plex Mono, monospace", size=9),
        ))
    fig.update_layout(
        title_text=title, barmode="group",
        xaxis_title="Party", yaxis_title="Members",
        **{k: v for k, v in apply_theme(go.Figure()).layout.to_plotly_json().items()}
    )
    return apply_theme(fig)


def timeline_chart(df: pd.DataFrame, x: str, y: str, color: str = None,
                   title: str = "") -> go.Figure:
    if df.empty:
        return go.Figure()
    if color:
        fig = px.line(df, x=x, y=y, color=color,
                      color_discrete_map=PARTY_COLORS,
                      title=title)
    else:
        fig = px.line(df, x=x, y=y, title=title)
    fig.update_traces(line=dict(width=2))
    return apply_theme(fig)


def bar_chart(df: pd.DataFrame, x: str, y: str, color: str = None,
              title: str = "", horizontal: bool = False,
              color_map: dict = None) -> go.Figure:
    if df.empty:
        return go.Figure()
    kwargs = dict(title=title, text_auto=True)
    if color:
        kwargs["color"] = color
        kwargs["color_discrete_map"] = color_map or PARTY_COLORS
    if horizontal:
        fig = px.bar(df, x=y, y=x, orientation="h", **kwargs)
    else:
        fig = px.bar(df, x=x, y=y, **kwargs)
    fig.update_traces(textfont=dict(family="IBM Plex Mono, monospace", size=9))
    return apply_theme(fig)


def scatter_chart(df: pd.DataFrame, x: str, y: str, color: str = None,
                  size: str = None, hover_name: str = None,
                  title: str = "") -> go.Figure:
    if df.empty:
        return go.Figure()
    fig = px.scatter(
        df, x=x, y=y, color=color, size=size, hover_name=hover_name,
        color_discrete_map=PARTY_COLORS if color in ("party", "party_a", "party_b") else None,
        title=title
    )
    return apply_theme(fig)


def cosponsor_network_graph(df: pd.DataFrame) -> go.Figure:
    """Interactive network graph of cosponsor relationships."""
    if df.empty:
        return go.Figure()

    G = nx.Graph()
    for _, row in df.iterrows():
        G.add_edge(
            row["name_a"], row["name_b"],
            weight=row["shared_bills"],
            party_a=row.get("party_a", ""),
            party_b=row.get("party_b", ""),
        )

    # Spring layout
    pos = nx.spring_layout(G, k=2.5, seed=42, weight="weight")

    # Edges
    edge_x, edge_y, edge_w = [], [], []
    for u, v, d in G.edges(data=True):
        x0, y0 = pos[u]; x1, y1 = pos[v]
        edge_x += [x0, x1, None]; edge_y += [y0, y1, None]
        edge_w.append(d.get("weight", 1))

    edge_trace = go.Scatter(
        x=edge_x, y=edge_y, mode="lines",
        line=dict(width=0.8, color="#d1d5db"),
        hoverinfo="none",
    )

    # Nodes
    node_x, node_y, node_text, node_color, node_size = [], [], [], [], []
    party_map = {}
    for _, row in df.iterrows():
        party_map[row["name_a"]] = row.get("party_a", "")
        party_map[row["name_b"]] = row.get("party_b", "")

    for node in G.nodes():
        x, y = pos[node]
        node_x.append(x); node_y.append(y)
        node_text.append(node)
        p = party_map.get(node, "")
        node_color.append(PARTY_COLORS.get(p, MUTED))
        node_size.append(5 + G.degree(node) * 2)

    node_trace = go.Scatter(
        x=node_x, y=node_y, mode="markers+text",
        text=node_text,
        textposition="top center",
        textfont=dict(family="IBM Plex Mono, monospace", size=7),
        marker=dict(
            size=node_size, color=node_color,
            line=dict(width=1, color="#fff"),
        ),
        hovertemplate="%{text}<extra></extra>",
    )

    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(
        title="Cosponsor Network",
        showlegend=False,
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
    )
    return apply_theme(fig)


def agreement_heatmap(df: pd.DataFrame, top_n: int = 30) -> go.Figure:
    """Pairwise agreement score heatmap for top members."""
    if df.empty:
        return go.Figure()
    # Get top N most frequently appearing members
    member_counts = pd.concat([
        df["name_a"], df["name_b"]
    ]).value_counts().head(top_n)
    members = member_counts.index.tolist()

    matrix = pd.DataFrame(index=members, columns=members, dtype=float)
    np.fill_diagonal(matrix.values, 1.0)
    for _, row in df.iterrows():
        a, b = row["name_a"], row["name_b"]
        if a in members and b in members:
            matrix.loc[a, b] = row["agreement_score"]
            matrix.loc[b, a] = row["agreement_score"]

    fig = px.imshow(
        matrix.fillna(0.5),
        color_continuous_scale=["#fee2e2", "#ffffff", "#d1fae5"],
        zmin=0, zmax=1,
        title="Member Agreement Heatmap (floor votes)",
        text_auto=False,
    )
    fig.update_xaxes(tickfont=dict(family="IBM Plex Mono, monospace", size=8))
    fig.update_yaxes(tickfont=dict(family="IBM Plex Mono, monospace", size=8))
    return apply_theme(fig)


def status_funnel(df: pd.DataFrame) -> go.Figure:
    """Bill status distribution as a horizontal funnel/bar."""
    if df.empty:
        return go.Figure()
    status_order = [
        "Introduced", "Referred to Committee", "Hearing Scheduled",
        "Do Pass", "Perfected", "Third Read", "Truly Agreed", "Signed by Governor",
        "Vetoed", "Failed",
    ]
    # Keep top statuses
    df_s = df.sort_values("count", ascending=True).tail(15)
    fig = go.Figure(go.Bar(
        x=df_s["count"],
        y=df_s["current_status"],
        orientation="h",
        marker=dict(
            color=df_s["count"],
            colorscale=[[0, "#e5e7eb"], [0.5, GOLD], [1, NAVY]],
        ),
        text=df_s["count"],
        textposition="outside",
        textfont=dict(family="IBM Plex Mono, monospace", size=9),
    ))
    fig.update_layout(title_text="Bills by Status")
    return apply_theme(fig)


def lineage_sankey(df: pd.DataFrame) -> go.Figure:
    """Sankey diagram of bill language lineage by match type."""
    if df.empty:
        return go.Figure()
    sessions = sorted(set(df["source_session"].tolist() + df["target_session"].tolist()))
    session_idx = {s: i for i, s in enumerate(sessions)}

    links = df.groupby(["source_session","target_session"]).agg(
        value=("similarity_score","count")
    ).reset_index()

    fig = go.Figure(go.Sankey(
        node=dict(
            label=sessions,
            color=[GOLD if i % 2 == 0 else BLUE for i in range(len(sessions))],
            pad=15, thickness=20,
        ),
        link=dict(
            source=[session_idx[s] for s in links["source_session"]],
            target=[session_idx[t] for t in links["target_session"]],
            value=links["value"],
            color="rgba(200,169,110,0.3)",
        ),
    ))
    fig.update_layout(title_text="Language Lineage Flow by Session")
    return apply_theme(fig)