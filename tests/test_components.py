"""Tests for dashboard component builders.

Tests cover KPI cards, panels, empty states, data tables, badges, and
date controls — verifying HTML output structure and CSS class conventions.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.dashboards.components import (
    badge,
    chart_container,
    data_table,
    date_controls,
    empty_state,
    kpi_card,
    kpi_row,
    page_header,
    panel,
    resolve_date_range,
)
from app.dashboards.kpi import Kpi


class TestKpiCard:
    def test_basic(self):
        k = Kpi(key="test", label="Test KPI", value=42)
        html = kpi_card(k)
        assert "kpi" in html
        assert "k-label" in html
        assert "Test KPI" in html
        assert "k-value" in html
        assert "42" in html

    def test_with_unit(self):
        k = Kpi(key="mpg", label="Avg MPG", value=15.5, unit="mpg")
        html = kpi_card(k)
        assert "15.1 mpg" in html or "15.5 mpg" in html

    def test_none_value(self):
        k = Kpi(key="none", label="Empty", value=None)
        html = kpi_card(k)
        assert "—" in html

    def test_active_class(self):
        k = Kpi(key="active", label="Active", value=1)
        html = kpi_card(k, active=True)
        assert 'class="kpi active"' in html

    def test_with_hint(self):
        k = Kpi(key="hinted", label="With Hint", value=10, hint="Units in gallons")
        html = kpi_card(k)
        assert "k-hint" in html
        assert "gallons" in html

    def test_with_delta_up_good(self):
        k = Kpi(key="d", label="Delta", value=100, delta=12.5, delta_good_when_up=True)
        html = kpi_card(k)
        assert "k-delta" in html
        assert "up" in html
        assert "12.5%" in html

    def test_with_delta_down_bad(self):
        k = Kpi(key="d", label="Delta", value=100, delta=-5.0, delta_good_when_up=True)
        html = kpi_card(k)
        assert "k-delta" in html
        assert "down" in html


class TestKpiRow:
    def test_multiple_kpis(self):
        kpis = [
            Kpi(key="a", label="A", value=1),
            Kpi(key="b", label="B", value=2),
        ]
        html = kpi_row(kpis)
        assert 'class="kpis"' in html
        assert "A" in html
        assert "B" in html

    def test_active_key(self):
        kpis = [
            Kpi(key="a", label="A", value=1),
            Kpi(key="b", label="B", value=2),
        ]
        html = kpi_row(kpis, active_key="a")
        assert 'class="kpi active"' in html


class TestPanel:
    def test_basic(self):
        html = panel("hello")
        assert html.startswith('<section class="panel">')
        assert "hello" in html
        assert html.endswith("</section>")

    def test_with_title(self):
        html = panel("content", title="My Title")
        assert "<h3>" in html
        assert "My Title" in html

    def test_span_2(self):
        html = panel("content", span_2=True)
        assert 'class="panel span-2"' in html

    def test_with_dot(self):
        html = panel("content", title="Titled", dot="#38bdf8")
        assert "dot" in html
        assert "#38bdf8" in html


class TestEmptyState:
    def test_default_message(self):
        html = empty_state()
        assert "chart-empty" in html
        assert "No data available" in html

    def test_custom_message(self):
        html = empty_state("Custom message")
        assert "Custom message" in html


class TestChartContainer:
    def test_with_chart(self):
        html = chart_container("<div>chart</div>", title="My Chart")
        assert "chart" in html
        assert "My Chart" in html

    def test_empty_fallback(self):
        html = chart_container(None, title="Empty Chart")
        assert "chart-empty" in html


class TestDataTable:
    def test_basic(self):
        html = data_table(["Name", "Value"], [["A", "1"], ["B", "2"]])
        assert "tbl-wrap" in html
        assert "data" in html
        assert "Name" in html
        assert "A" in html
        assert "1" in html

    def test_empty_rows(self):
        html = data_table(["Name"], [])
        assert "<tr>" not in html or "</tr>" in html

    def test_num_cols(self):
        html = data_table(["Name", "Count"], [["A", "42"]], num_cols={1})
        assert 'class="num"' in html


class TestBadge:
    def test_default(self):
        html = badge("Info")
        assert 'class="badge"' in html
        assert "Info" in html

    def test_variant(self):
        html = badge("Danger", variant="red")
        assert 'class="badge red"' in html


class TestPageHeader:
    def test_basic(self):
        html = page_header("Dashboard")
        assert "<h1>Dashboard</h1>" in html
        assert "header" in html

    def test_with_subtitle(self):
        html = page_header("Dashboard", subtitle="Overview")
        assert "crumbs" in html
        assert "Overview" in html


class TestResolveDateRange:
    def test_default_to_30d(self):
        since, until, rng = resolve_date_range(None, None, None)
        assert rng == "30d"
        assert since < until

    def test_7d(self):
        since, until, rng = resolve_date_range("7d", None, None)
        assert rng == "7d"
        assert (until - since).days == 7

    def test_custom_dates(self):
        since, until, rng = resolve_date_range("custom", "2026-01-01", "2026-01-10")
        assert rng == "custom"
        assert (until - since).days > 9

    def test_ytd(self):
        now = datetime.now(timezone.utc)
        since, until, rng = resolve_date_range("ytd", None, None)
        assert rng == "ytd"
        assert since.month == 1 and since.day == 1
        assert abs((until - now).total_seconds()) < 60

    def test_all(self):
        since, until, rng = resolve_date_range("all", None, None)
        assert rng == "all"
        assert (until - since).days > 1000


class TestDateControls:
    def test_renders_presets(self):
        html = date_controls("30d")
        assert "controls" in html
        assert 'class="preset active"' in html
        assert "7d" in html
        assert "30d" in html
        assert "Apply" in html

    def test_hx_attributes(self):
        html = date_controls("7d", hx_target="#other")
        assert 'hx-target="#other"' in html
