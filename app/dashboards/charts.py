from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
from plotly.io import to_html

# Professional fleet palette
COLORS = {
    "primary": "#2563eb",
    "secondary": "#0e7490",
    "success": "#16a34a",
    "warning": "#ea580c",
    "danger": "#dc2626",
    "primary_fill": "rgba(37,99,235,0.15)",
    "secondary_fill": "rgba(14,116,144,0.15)",
    "success_fill": "rgba(22,163,74,0.15)",
    "warning_fill": "rgba(234,88,12,0.15)",
    "danger_fill": "rgba(220,38,38,0.15)",
}

BASE_LAYOUT = {
    "template": "plotly_dark",
    "margin": {"l": 32, "r": 16, "t": 8, "b": 24},
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "xaxis": {
        "tickfont": {"size": 10, "color": "#94a3b8"},
    },
    "yaxis": {
        "tickfont": {"size": 10, "color": "#94a3b8"},
    },
    "hoverlabel": {
        "bgcolor": "#1e293b",
        "font_size": 12,
    },
}


def chart_html(fig: go.Figure) -> str:
    fig.update_layout(**BASE_LAYOUT)
    return to_html(fig, include_plotlyjs=False, full_html=False, config={"displayModeBar": False, "responsive": True})


def line_chart(rows: list[dict[str, Any]] | None, x: str, y: str, title: str, color: str = COLORS["primary"]) -> str | None:
    rows = rows or []
    if not rows:
        return None
    fig = go.Figure()
    x_vals = [row.get(x) for row in rows]
    y_vals = [row.get(y) for row in rows]
    fill_color = COLORS.get(f"{color}_fill", COLORS["primary_fill"]) if color == COLORS["primary"] else color.replace(")", ",0.15)").replace("rgb", "rgba") if "rgb" in color else COLORS["primary_fill"]
    fig.add_trace(go.Scatter(
        x=x_vals,
        y=y_vals,
        mode="lines+markers",
        line={"color": color, "shape": "spline", "smoothing": 0.8, "width": 2.5},
        marker={"size": 5, "color": color},
        fill="tozeroy",
        fillcolor=fill_color,
        hovertemplate="%{x}<br>%{y:,.1f}<extra></extra>",
    ))
    fig.update_layout(
        xaxis_title=None,
        yaxis_title=None,
    )
    return chart_html(fig)


def bar_chart(rows: list[dict[str, Any]] | None, x: str, y: str, title: str, color: str = COLORS["success"]) -> str | None:
    rows = rows or []
    if not rows:
        return None
    # Horizontal bars for rankings
    labels = [row.get(x) for row in rows]
    values = [row.get(y) for row in rows]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=labels,
        x=values,
        orientation="h",
        marker={"color": color, "line": {"width": 0}},
        text=[f"{v:,.1f}" for v in values],
        textposition="outside",
        textfont={"size": 10, "color": "#e2e8f0"},
        hovertemplate="%{y}<br>%{x:,.1f}<extra></extra>",
    ))
    fig.update_layout(
        xaxis_title=None,
        yaxis_title=None,
        yaxis={"autorange": "reversed", "tickfont": {"size": 10}},
    )
    return chart_html(fig)


def histogram(values: list[float] | None, title: str, color: str = COLORS["warning"]) -> str | None:
    values = values or []
    if not values:
        return None
    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=values,
        marker={"color": color, "line": {"width": 0}},
        hovertemplate="%{x:.0f} mph<br>%{y} records<extra></extra>",
    ))
    fig.update_layout(
        xaxis_title=None,
        yaxis_title=None,
    )
    return chart_html(fig)


def map_chart(points: list[dict[str, Any]] | None, title: str) -> str | None:
    points = points or []
    if not points:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Scattergeo(
            lat=[point["latitude"] for point in points],
            lon=[point["longitude"] for point in points],
            mode="markers",
            marker={
                "size": 8,
                "color": [COLORS["success"] if point.get("status") == "moving" else COLORS["warning"] for point in points],
            },
            text=[point.get("vehicle", "") for point in points],
            hovertemplate="%{text}<br>%{lat:.3f}, %{lon:.3f}<extra></extra>",
        )
    )
    fig.update_layout(
        geo={"projection_type": "natural earth"},
        height=520,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
    )
    return chart_html(fig)
