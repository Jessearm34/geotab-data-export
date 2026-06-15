from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import plotly.graph_objects as go
from plotly.io import to_html


def chart_html(fig: go.Figure) -> str:
    fig.update_layout(template="plotly_dark", margin={"l": 28, "r": 18, "t": 36, "b": 28}, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    return to_html(fig, include_plotlyjs=False, full_html=False, config={"displayModeBar": False, "responsive": True})


def line_chart(rows: list[dict[str, Any]] | None, x: str, y: str, title: str) -> str:
    rows = rows or []
    fig = go.Figure()
    if rows:
        fig.add_trace(go.Scatter(x=[row.get(x) for row in rows], y=[row.get(y) for row in rows], mode="lines+markers", line={"color": "#38bdf8"}))
    fig.update_layout(title=title)
    return chart_html(fig)


def monthly_trend_chart(rows: list[dict[str, Any]] | None, x: str, y: str, title: str) -> str:
    """Trend chart with a rule for the current month's preliminary data.

    Before the 25th of the current month, the current month point is rendered
    as an unfilled marker only (not connected to the line). On or after the
    25th, it appears as a normal connected point.
    """
    rows = rows or []
    fig = go.Figure()
    if not rows:
        fig.update_layout(title=title)
        return chart_html(fig)

    now = datetime.now(timezone.utc)
    current_month_str = now.strftime("%Y-%m")

    if now.day < 25 and any(current_month_str in (row.get(x) or "") for row in rows):
        completed = [row for row in rows if current_month_str not in (row.get(x) or "")]
        preliminary = [row for row in rows if current_month_str in (row.get(x) or "")]

        if completed:
            fig.add_trace(go.Scatter(
                x=[row.get(x) for row in completed],
                y=[row.get(y) for row in completed],
                mode="lines+markers",
                line={"color": "#38bdf8"},
                name=title,
            ))
        if preliminary:
            fig.add_trace(go.Scatter(
                x=[row.get(x) for row in preliminary],
                y=[row.get(y) for row in preliminary],
                mode="markers",
                marker={"color": "#38bdf8", "symbol": "circle-open", "size": 10, "line": {"width": 2}},
                name="Preliminary",
                showlegend=False,
            ))
    else:
        fig.add_trace(go.Scatter(
            x=[row.get(x) for row in rows],
            y=[row.get(y) for row in rows],
            mode="lines+markers",
            line={"color": "#38bdf8"},
        ))

    fig.update_layout(title=title)
    return chart_html(fig)


def bar_chart(rows: list[dict[str, Any]] | None, x: str, y: str, title: str) -> str:
    rows = rows or []
    fig = go.Figure()
    if rows:
        fig.add_trace(go.Bar(x=[row.get(x) for row in rows], y=[row.get(y) for row in rows], marker={"color": "#22c55e"}))
    fig.update_layout(title=title)
    return chart_html(fig)


def histogram(values: list[float] | None, title: str) -> str:
    values = values or []
    fig = go.Figure()
    if values:
        fig.add_trace(go.Histogram(x=values, marker={"color": "#f59e0b"}))
    fig.update_layout(title=title)
    return chart_html(fig)


def map_chart(points: list[dict[str, Any]] | None, title: str) -> str:
    points = points or []
    fig = go.Figure()
    if points:
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
