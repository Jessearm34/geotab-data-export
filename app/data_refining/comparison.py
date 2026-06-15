"""Period-over-period KPI comparison logic.

Computes deltas between a primary time window and a prior equivalent window
for revenue and other time-range-aware KPIs.

Rules:
  - YTD: compare this year's completed months vs last year's same months
  - Trailing windows (30d, 90d): compare vs immediately preceding equivalent window
  - Custom range: compare vs an immediately preceding equivalent-length window
  - Current in-progress month is EXCLUDED from YTD comparison (only compare
    completed months)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def prior_period(since: datetime, until: datetime) -> tuple[datetime, datetime]:
    """Return the prior window of the same length as (since, until).

    For YTD (since is Jan 1 of this year), returns prior-year YTD for
    the same completed months only (excludes current in-progress month if
    the YTD window includes it). Uses `until` as the reference point for
    determining the current month (testable without mocking datetime).

    For trailing windows and custom ranges, simply subtracts the window
    length and returns (since - length, since).
    """
    length = until - since

    if since.month == 1 and since.day == 1:
        return _ytd_prior_period(since, until)

    prior_since = since - length
    prior_until = since
    return (prior_since, prior_until)


def _ytd_prior_period(since: datetime, until: datetime) -> tuple[datetime, datetime]:
    """YTD comparison: prior year same completed months, excluding current in-progress month.

    Uses `until` as the reference point. If `until` falls inside the current
    month (i.e. the month containing `until`), that month is treated as
    in-progress and excluded from the comparison.
    """
    ref_year = until.year
    ref_month = until.month

    # The last completed month is the month before the reference month
    # (the reference month is the current in-progress month)
    last_completed = ref_month - 1

    if last_completed < since.month:
        return (since, since)  # zero-length window = no comparison

    prior_year = since.year - 1
    prior_since = datetime(prior_year, since.month, 1, tzinfo=timezone.utc)
    prior_until = datetime(prior_year, last_completed + 1, 1, tzinfo=timezone.utc)
    return (prior_since, prior_until)


def compute_delta(current: float | int | None, prior: float | int | None) -> float | None:
    """Compute percentage change from prior to current.

    Returns None if either value is None or prior is 0 (can't compute).
    """
    if current is None or prior is None or prior == 0:
        return None
    return round((float(current) - float(prior)) / float(prior) * 100, 1)
