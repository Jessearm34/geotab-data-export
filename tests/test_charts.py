"""Tests for chart helpers — defensive guards against None/empty inputs."""

from app.dashboards.charts import bar_chart, histogram, line_chart, map_chart


def test_line_chart_with_none_rows():
    assert line_chart(None, "x", "y", "Test") is None


def test_line_chart_with_empty_rows():
    assert line_chart([], "x", "y", "Test") is None


def test_line_chart_with_valid_rows():
    html = line_chart([{"x": 1, "y": 2}], "x", "y", "Test")
    assert isinstance(html, str)
    assert "<div>" in html or "plotly" in html


def test_bar_chart_with_none_rows():
    assert bar_chart(None, "x", "y", "Test") is None


def test_bar_chart_with_empty_rows():
    assert bar_chart([], "x", "y", "Test") is None


def test_histogram_with_none_values():
    assert histogram(None, "Test") is None


def test_histogram_with_empty_values():
    assert histogram([], "Test") is None


def test_map_chart_with_none_points():
    assert map_chart(None, "Test") is None


def test_map_chart_with_empty_points():
    assert map_chart([], "Test") is None
