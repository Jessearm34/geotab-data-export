from __future__ import annotations

from typing import Any

import plotly.graph_objects as go
from plotly.io import to_html


def chart_html(fig: go.Figure) -> str:
    fig.update_layout(template="plotly_dark", margin={"l": 28, "r": 18, "t": 36, "b": 28}, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return to_html(fig, include_plotlyjs=False, full_html=False, config={"displayModeBar": False, "responsive": True})


def line_chart(rows: list[dict[str, Any]] | None, x: str, y: str, title: str) -> str | None:
    rows = rows or []
    if not rows:
        return None
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[row.get(x) for row in rows], y=[row.get(y) for row in rows], mode="lines+markers", line={"color": "#38bdf8"}))
    fig.update_layout(title=title)
    return chart_html(fig)


def bar_chart(rows: list[dict[str, Any]] | None, x: str, y: str, title: str) -> str | None:
    rows = rows or []
    if not rows:
        return None
    fig = go.Figure()
    fig.add_trace(go.Bar(x=[row.get(x) for row in rows], y=[row.get(y) for row in rows], marker={"color": "#22c55e"}))
    fig.update_layout(title=title)
    return chart_html(fig)


def histogram(values: list[float] | None, title: str) -> str | None:
    values = values or []
    if not values:
        return None
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=values, marker={"color": "#f59e0b"}))
    fig.update_layout(title=title)
    return chart_html(fig)


def map_chart(points: list[dict[str, Any]] | None, title: str) -> str | None:
    points = points or []
    if not points:
        return None
    fig = go.Figure()
    fig.add_trace(
        go.Scattermapbox(
            lat=[point["latitude"] for point in points],
            lon=[point["longitude"] for point in points],
            mode="markers",
            marker={"size": 11, "color": ["#22c55e" if point.get("status") == "moving" else "#f59e0b" for point in points]},
            text=[point.get("vehicle", "") for point in points],
        )
    )
    fig.update_layout(title=title, mapbox={"style": "open-street-map", "zoom": 3}, height=520)
    return chart_html(fig)
