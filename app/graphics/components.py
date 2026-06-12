"""Reusable HTML component builders for dashboard rendering.

All functions return HTML strings — no dependency on FastHTML, raw ORM
models, or Geotab payload shapes. Consumes refined contracts from
app.data_refining.contracts and app.graphics.kpi.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.graphics.kpi import Kpi, format_delta, format_kpi_value


def kpi_card(kpi: Kpi, active: bool = False) -> str:
    extras = ' data-key="' + kpi.key + '"' if kpi.key else ""
    cls = "kpi" + (" active" if active else "")
    if kpi.tier == "extended":
        cls += " extended"
    html = f'<div class="{cls}"{extras}>'
    html += f'<div class="k-label">{kpi.label}</div>'
    if kpi.tier:
        html += f'<span class="k-tier">{kpi.tier}</span>'
    html += f'<div class="k-value">{format_kpi_value(kpi.value, kpi.unit)}</div>'
    if kpi.delta is not None:
        html += format_delta(kpi.delta, kpi.delta_good_when_up)
    if kpi.hint:
        html += f'<div class="k-hint">{kpi.hint}</div>'
    html += "</div>"
    return html


def kpi_row(kpis: list[Kpi], active_key: str | None = None) -> str:
    cards = "".join(kpi_card(k, active=(k.key == active_key)) for k in kpis)
    return f'<div class="kpis">{cards}</div>'


def panel(content: str, title: str | None = None, span_2: bool = False, dot: str | None = None) -> str:
    cls = "panel" + (" span-2" if span_2 else "")
    html = f'<section class="{cls}">'
    if title:
        dot_html = f'<span class="dot" style="background:{dot}"></span>' if dot else ""
        html += f'<h3>{dot_html}{title}</h3>'
    html += content
    html += "</section>"
    return html


def chart_container(chart_html: str | None, title: str = "", span_2: bool = False, dot: str | None = None) -> str:
    if chart_html:
        return panel(f'<div class="chart-wrap">{chart_html}</div>', title=title, span_2=span_2, dot=dot)
    return empty_state(title or "No data available", span_2=span_2)


def empty_state(message: str = "No data available", span_2: bool = False) -> str:
    cls = "chart-empty" + (" span-2" if span_2 else "")
    return f'<div class="{cls}"><p>{message}</p></div>'


def data_table(headers: list[str], rows: list[list[str]], num_cols: set[int] | None = None) -> str:
    num_cols = num_cols or set()
    thead = "<thead><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead>"
    tbody = "<tbody>"
    for row in rows:
        tbody += "<tr>"
        for i, cell in enumerate(row):
            cls = ' class="num"' if i in num_cols else ""
            tbody += f"<td{cls}>{cell}</td>"
        tbody += "</tr>"
    tbody += "</tbody>"
    return f'<div class="tbl-wrap"><table class="data">{thead}{tbody}</table></div>'


def badge(text: str, variant: str = "") -> str:
    cls = "badge" + (f" {variant}" if variant else "")
    return f'<span class="{cls}">{text}</span>'


def page_header(title: str, subtitle: str | None = None, refreshed: datetime | None = None) -> str:
    html = f'<div class="header"><h1>{title}</h1>'
    if subtitle:
        html += f'<div class="crumbs">{subtitle}</div>'
    if refreshed:
        html += f'<div class="refreshed">Updated {_timeago(refreshed)}</div>'
    html += "</div>"
    return html


def _timeago(dt: datetime) -> str:
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        mins = secs // 60
        return f"{mins}m ago"
    if secs < 86400:
        hours = secs // 3600
        return f"{hours}h ago"
    days = secs // 86400
    return f"{days}d ago"


def date_controls(active: str = "30d", hx_target: str | None = None, range: str | None = None, start: str | None = None, end: str | None = None) -> str:
    target = hx_target or "#main-content"
    presets = [
        ("ytd", "Year to date"),
        ("30d", "30 days"),
        ("90d", "90 days"),
        ("12m", "12 months"),
    ]
    btns = "".join(
        f'<a class="preset{" active" if key == active else ""}" href="/?range={key}" hx-get="/?range={key}" hx-target="{target}" hx-push-url="true">{label}</a>'
        for key, label in presets
    )
    html = f'<div class="controls">{btns}'
    html += f'<form class="custom-date" hx-get="/" hx-target="{target}">'
    html += f'<input type="hidden" name="range" value="custom">'
    html += f'<input type="date" name="start" value="{start or ""}" class="date-input" title="Start date">'
    html += f'<span class="sep">→</span>'
    html += f'<input type="date" name="end" value="{end or ""}" class="date-input" title="End date">'
    html += '<button type="submit" class="apply">Apply</button>'
    html += "</form></div>"
    return html


def resolve_date_range(range: str | None, start: str | None, end: str | None) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    if range == "custom" and start and end:
        since = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        until = (datetime.fromisoformat(end) + timedelta(days=1)).replace(tzinfo=timezone.utc)
        return since, until, "custom"

    if range == "7d":
        return now - timedelta(days=7), now, "7d"
    if range == "90d":
        return now - timedelta(days=90), now, "90d"
    if range == "12m":
        return now - timedelta(days=365), now, "12m"
    if range == "all":
        return datetime(2000, 1, 1, tzinfo=timezone.utc), now, "all"

    # Default: YTD
    since = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    return since, now, "ytd"
