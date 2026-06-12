"""Tests for metric classification module."""

from app.data_refining.reference_metrics import (
    MetricTier,
    baseline_metrics,
    classify_metric,
    drift_sources_for,
    extended_metrics,
    get_metric,
)


class TestMetricTier:
    def test_baseline_value(self):
        assert MetricTier.BASELINE.value == "baseline"

    def test_extended_value(self):
        assert MetricTier.EXTENDED.value == "extended"


class TestGetMetric:
    def test_known_key(self):
        m = get_metric("fleet_miles")
        assert m is not None
        assert m.label == "Fleet Miles"
        assert m.tier == MetricTier.BASELINE

    def test_extended_key(self):
        m = get_metric("co2")
        assert m is not None
        assert m.tier == MetricTier.EXTENDED

    def test_unknown_key(self):
        assert get_metric("nonexistent") is None


class TestBaselineMetrics:
    def test_returns_only_baseline(self):
        metrics = baseline_metrics()
        assert all(m.tier == MetricTier.BASELINE for m in metrics)

    def test_includes_fleet_miles(self):
        keys = {m.key for m in baseline_metrics()}
        assert "fleet_miles" in keys
        assert "total_vehicles" in keys

    def test_excludes_co2(self):
        keys = {m.key for m in baseline_metrics()}
        assert "co2" not in keys


class TestExtendedMetrics:
    def test_returns_only_extended(self):
        metrics = extended_metrics()
        assert all(m.tier == MetricTier.EXTENDED for m in metrics)

    def test_includes_co2(self):
        keys = {m.key for m in extended_metrics()}
        assert "co2" in keys

    def test_excludes_fleet_miles(self):
        keys = {m.key for m in extended_metrics()}
        assert "fleet_miles" not in keys


class TestClassifyMetric:
    def test_baseline(self):
        assert classify_metric("fleet_miles") == MetricTier.BASELINE

    def test_extended(self):
        assert classify_metric("co2") == MetricTier.EXTENDED

    def test_unknown(self):
        assert classify_metric("nonexistent") is None


class TestDriftSourcesFor:
    def test_known_metric(self):
        sources = drift_sources_for("fleet_miles")
        assert len(sources) > 0
        assert any("conversion" in s.lower() for s in sources)

    def test_unknown_metric(self):
        assert drift_sources_for("nonexistent") == []

    def test_baseline_not_empty(self):
        """All baseline metrics should have at least one documented drift source."""
        for m in baseline_metrics():
            assert len(m.known_drift_sources) > 0, f"Metric '{m.key}' has no drift sources"
