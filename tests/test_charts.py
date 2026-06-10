"""Tests for chart helpers — defensive guards against None/empty inputs."""

from app.dashboards.charts import bar_chart, histogram, line_chart, map_chart


def test_line_chart_with_none_rows():
    html = line_chart(None, "x", "y", "Test")
    assert "<div>" in html or "plotly" in html


def test_line_chart_with_empty_rows():
    html = line_chart([], "x", "y", "Test")
    assert "<div>" in html or "plotly" in html


def test_line_chart_with_valid_rows():
    html = line_chart([{"x": 1, "y": 2}], "x", "y", "Test")
    assert "<div>" in html or "plotly" in html


def test_bar_chart_with_none_rows():
    html = bar_chart(None, "x", "y", "Test")
    assert "<div>" in html or "plotly" in html


def test_bar_chart_with_empty_rows():
    html = bar_chart([], "x", "y", "Test")
    assert "<div>" in html or "plotly" in html


def test_histogram_with_none_values():
    html = histogram(None, "Test")
    assert "<div>" in html or "plotly" in html


def test_histogram_with_empty_values():
    html = histogram([], "Test")
    assert "<div>" in html or "plotly" in html


def test_map_chart_with_none_points():
    html = map_chart(None, "Test")
    assert "<div>" in html or "plotly" in html


def test_map_chart_with_empty_points():
    html = map_chart([], "Test")
    assert "<div>" in html or "plotly" in html
