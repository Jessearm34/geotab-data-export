"""KPI dataclass and formatting utilities for dashboard display.

Mirrors the pattern from eww-dashboard-public (visualize_fasthtml/data.py):
structured KPI values with labels, deltas, units, and display hints.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Kpi:
    key: str
    label: str
    value: float | int | None = 0
    delta: float | None = None
    unit: str = ""
    delta_good_when_up: bool = True
    chartable: bool = False
    hint: str = ""


def _abbrev_money(v: float) -> str:
    n = abs(v)
    sign = "-" if v < 0 else ""
    if n >= 1_000_000:
        return f"{sign}${n / 1_000_000:,.2f}M"
    if n >= 1_000:
        return f"{sign}${n / 1_000:,.1f}K"
    return f"{sign}${n:,.0f}"


def format_kpi_value(value: float | int | None, unit: str = "") -> str:
    if value is None:
        return "—"
    if unit == "$":
        return _abbrev_money(float(value))
    if unit == "days":
        return f"{value:,.0f} days"
    if unit == "x":
        return f"{value:,.2f}×"
    if unit == "%":
        return f"{value:,.1f}%"
    if unit == "mph":
        return f"{value:,.1f} mph"
    if unit == "mpg":
        return f"{value:,.1f} mpg"
    if isinstance(value, float) and value.is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}" if isinstance(value, float) else str(value)


def format_delta(delta: float | None, delta_good_when_up: bool = True) -> str:
    if delta is None:
        return ""
    up = delta >= 0
    good = up if delta_good_when_up else not up
    arrow = "▲" if up else "▼"
    css = "up" if good else "down"
    return f'<span class="k-delta {css}">{arrow} {abs(delta):.1f}%</span>'
