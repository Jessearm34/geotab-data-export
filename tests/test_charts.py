"""Tests for chart helpers — defensive guards against None/empty inputs."""

from app.dashboards.charts import bar_chart, has_meaningful_series, histogram, line_chart, map_chart


# ── has_meaningful_series ──────────────────────────── #


class TestHasMeaningfulSeries:
    def test_none_rows(self):
        assert has_meaningful_series(None) is False

    def test_empty_rows(self):
        assert has_meaningful_series([]) is False

    def test_single_zero_row(self):
        assert has_meaningful_series([{"x": 0, "y": 0}]) is False

    def test_single_row_with_value(self):
        assert has_meaningful_series([{"x": 1, "y": 5}]) is True

    def test_multiple_all_zero_rows(self):
        assert has_meaningful_series([{"x": 0}, {"x": 0}]) is False

    def test_mixed_zero_and_value(self):
        assert has_meaningful_series([{"miles": 0}, {"miles": 10}]) is True

    def test_uses_specified_keys(self):
        rows = [{"label": "A", "value": 0, "other": 42}]
        assert has_meaningful_series(rows, ["value"]) is False
        assert has_meaningful_series(rows, ["other"]) is True

    def test_none_values_skip(self):
        rows = [{"a": None, "b": None}]
        assert has_meaningful_series(rows) is False

    def test_mixed_none_and_zero(self):
        rows = [{"a": None, "b": 0}]
        assert has_meaningful_series(rows) is False

    def test_empty_dict_keys_fallback(self):
        rows = [{}]
        assert has_meaningful_series(rows) is False


# ── line_chart ────────────────────────────────────── #
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
