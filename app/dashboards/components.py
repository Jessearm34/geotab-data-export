"""Structured HTML component builders for the fleet analytics dashboard.

Mirrors the component patterns from eww-dashboard-public (visualize_fasthtml/app.py):
KPI cards, date controls, panels, tables, badges, and empty states.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.dashboards.kpi import Kpi, format_delta, format_kpi_value


def kpi_card(kpi: Kpi, active: bool = False) -> str:
    hint_html = f'<div class="k-hint">{kpi.hint}</div>' if kpi.hint else ""
    delta_html = format_delta(kpi.delta, kpi.delta_good_when_up)
    active_cls = " active" if active else ""
    value_html = format_kpi_value(kpi.value, kpi.unit)
    return f'<div class="kpi{active_cls}"><div class="k-label">{kpi.label}</div><div class="k-value">{value_html}</div>{delta_html}{hint_html}</div>'


def kpi_row(kpis: list[Kpi], active_key: str | None = None) -> str:
    cards = "".join(kpi_card(k, active=(k.key == active_key)) for k in kpis)
    return f'<div class="kpis">{cards}</div>'


def panel(inner: str | None, title: str | None = None, span_2: bool = False, dot: str | None = None) -> str | None:
    if not inner:
        return None
    span_cls = " span-2" if span_2 else ""
    if title:
        dot_html = f'<span class="dot" style="background:{dot}"></span>' if dot else ""
        title_html = f"<h3>{dot_html}{title}</h3>"
        return f'<section class="panel{span_cls}">{title_html}{inner}</section>'
    return f'<section class="panel{span_cls}">{inner}</section>'


def empty_state(message: str = "No data available for this period.") -> str:
    return f'<div class="chart-empty">{message}</div>'


def chart_container(chart_html: str | None, title: str | None = None, span_2: bool = False, dot: str | None = None) -> str | None:
    if chart_html is None:
        return None
    return panel(chart_html, title=title, span_2=span_2, dot=dot)


def data_table(headers: list[str], rows: list[list[str]], num_cols: set[int] | None = None) -> str | None:
    if not rows:
        return None
    num_cols = num_cols or set()
    ths = "".join(f'<th class="num"{" " if i in num_cols else ""}>{h}</th>' if i in num_cols else f"<th>{h}</th>" for i, h in enumerate(headers))
    trs = "".join(
        "<tr>" + "".join(
            f'<td class="num">{" " if ci in num_cols else ""}{c}</td>' if ci in num_cols else f"<td>{c}</td>"
            for ci, c in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    return f'<div class="tbl-wrap"><table class="data"><thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div>'


def badge(text: str, variant: str = "") -> str:
    variant_cls = f" {variant}" if variant else ""
    return f'<span class="badge{variant_cls}">{text}</span>'


def date_controls(
    current_range: str = "30d",
    start_date: str | None = None,
    end_date: str | None = None,
    hx_target: str = "#main-content",
) -> str:
    presets = [
        ("7d", "7d"),
        ("30d", "30d"),
        ("90d", "90d"),
        ("YTD", "ytd"),
        ("All", "all"),
    ]
    preset_html = "".join(
        f'<button class="preset{" active" if current_range == key else ""}" '
        f'hx-get="?range={key}" hx-target="{hx_target}" hx-push-url="true">{label}</button>'
        for label, key in presets
    )
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_val = start_date or (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    end_val = end_date or today_str
    return f"""
<div class="controls">
  <span class="lbl">Period</span>
  {preset_html}
  <span class="spacer"></span>
  <span class="lbl">From</span>
  <input type="date" name="start" value="{start_val}" hx-get="?start={{val}}&end={end_val}&range=custom"
         hx-target="{hx_target}" hx-trigger="change" hx-push-url="true"
         onchange="this.closest('.controls').querySelector('.apply').classList.add('active')">
  <span class="lbl">To</span>
  <input type="date" name="end" value="{end_val}" hx-get="?range=custom&start={start_val}&end={{val}}"
         hx-target="{hx_target}" hx-trigger="change" hx-push-url="true"
         onchange="this.closest('.controls').querySelector('.apply').classList.add('active')">
  <button class="apply" hx-get="?range=custom&start={start_val}&end={end_val}"
          hx-target="{hx_target}" hx-push-url="true">Apply</button>
</div>"""


def resolve_date_range(range_str: str | None, start_str: str | None, end_str: str | None) -> tuple[datetime, datetime, str]:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if start_str and end_str:
        try:
            s = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
            e = datetime.fromisoformat(end_str).replace(tzinfo=timezone.utc) + timedelta(days=1)
            return s, e, "custom"
        except (ValueError, TypeError):
            pass
    match (range_str or "30d").lower():
        case "7d":
            return now - timedelta(days=7), now, "7d"
        case "90d":
            return now - timedelta(days=90), now, "90d"
        case "ytd":
            return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), now, "ytd"
        case "all":
            return now - timedelta(days=3650), now, "all"
        case _:
            return now - timedelta(days=30), now, "30d"


def page_header(title: str, subtitle: str | None = None, refreshed: datetime | None = None) -> str:
    sub = f'<div class="crumbs">{subtitle}</div>' if subtitle else ""
    ref = f'<span class="pill">Updated {refreshed.strftime("%H:%M")}</span>' if refreshed else ""
    return f'<div class="header"><div><h1>{title}</h1>{sub}</div><div class="refreshed">{ref}</div></div>'
