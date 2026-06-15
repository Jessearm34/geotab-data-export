"""Tests for period-over-period KPI comparison logic."""

from datetime import datetime, timezone

from app.data_refining.comparison import compute_delta, prior_period


class TestPriorPeriod:
    def test_trailing_30d(self):
        """A 30-day trailing window should compare vs the prior 30 days."""
        now = datetime.now(timezone.utc)
        since = now - __import__("datetime").timedelta(days=30)
        until = now
        prior_since, prior_until = prior_period(since, until)
        assert (prior_until - prior_since).days >= 28  # approximate
        assert prior_until <= since

    def test_custom_range(self):
        """A custom 10-day range compares vs the prior 10 days."""
        since = datetime(2026, 3, 15, tzinfo=timezone.utc)
        until = datetime(2026, 3, 25, tzinfo=timezone.utc)
        prior_since, prior_until = prior_period(since, until)
        assert prior_until == since
        assert (prior_until - prior_since).days == 10

    def test_ytd_excludes_current_month(self):
        """YTD comparison should only include completed months, excluding current in-progress month."""
        # Simulating June 2026: since=Jan 1, until=June 10 (in-progress June)
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        until = datetime(2026, 6, 10, tzinfo=timezone.utc)
        prior_since, prior_until = prior_period(since, until)
        assert prior_since.year == 2025
        assert prior_since.month == 1
        assert prior_since.day == 1
        # Should cover Jan-May (5 completed months), so prior_until = June 1
        assert prior_until == datetime(2025, 6, 1, tzinfo=timezone.utc)

    def test_ytd_january_no_comparison(self):
        """In January, no completed months yet — should return zero-length window."""
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        until = datetime(2026, 1, 15, tzinfo=timezone.utc)
        prior_since, prior_until = prior_period(since, until)
        assert prior_since == prior_until  # zero-length = no comparison

    def test_ytd_february_covers_january(self):
        """In February, only January is completed — compare Jan vs Jan of prior year."""
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        until = datetime(2026, 2, 10, tzinfo=timezone.utc)
        prior_since, prior_until = prior_period(since, until)
        assert prior_since == datetime(2025, 1, 1, tzinfo=timezone.utc)
        # prior_until should be Feb 1, 2025 (covering all of January 2025)
        assert prior_until == datetime(2025, 2, 1, tzinfo=timezone.utc)

    def test_ytd_april(self):
        """In April (until=Apr 5), completed months are Jan-Mar."""
        since = datetime(2026, 1, 1, tzinfo=timezone.utc)
        until = datetime(2026, 4, 5, tzinfo=timezone.utc)
        prior_since, prior_until = prior_period(since, until)
        assert prior_since == datetime(2025, 1, 1, tzinfo=timezone.utc)
        # Completed: Jan, Feb, Mar → prior_until = Apr 1
        assert prior_until == datetime(2025, 4, 1, tzinfo=timezone.utc)

    def test_equal_windows(self):
        """Trailing window length equality: prior window should match current window length."""
        since = datetime(2026, 6, 1, tzinfo=timezone.utc)
        until = datetime(2026, 6, 15, tzinfo=timezone.utc)
        current_len = (until - since).total_seconds()
        prior_since, prior_until = prior_period(since, until)
        prior_len = (prior_until - prior_since).total_seconds()
        assert prior_len == current_len


class TestComputeDelta:
    def test_increase(self):
        assert compute_delta(110, 100) == 10.0

    def test_decrease(self):
        assert compute_delta(90, 100) == -10.0

    def test_no_change(self):
        assert compute_delta(100, 100) == 0.0

    def test_none_current(self):
        assert compute_delta(None, 100) is None

    def test_none_prior(self):
        assert compute_delta(100, None) is None

    def test_zero_prior(self):
        assert compute_delta(100, 0) is None

    def test_both_none(self):
        assert compute_delta(None, None) is None

    def test_floats(self):
        assert compute_delta(150.0, 100.0) == 50.0

    def test_integers(self):
        assert compute_delta(200, 100) == 100.0
