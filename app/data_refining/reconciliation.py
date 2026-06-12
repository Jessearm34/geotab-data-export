"""Reusable helpers for reconciliation/audit comparisons.

Provides utilities for comparing raw Geotab API data → transformed → stored DB
→ analytics output, detecting timezone mismatches, count mismatches, null drift,
duplicate records, FK resolution drops, and sync watermark issues.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


# ── Comparison helpers ────────────────────────────── #


def compare_counts(
    raw: int, stored: int, label: str = "records"
) -> dict[str, Any]:
    """Compare raw API count vs stored DB count.

    Returns a dict with counts, difference, flag if mismatch, and the mismatch
    percentage.
    """
    diff = stored - raw
    pct = round(abs(diff) / raw * 100, 1) if raw else 0.0
    return {
        "label": label,
        "raw": raw,
        "stored": stored,
        "difference": diff,
        "mismatch": diff != 0,
        "mismatch_pct": pct,
    }


def check_timestamps(
    records: list[dict[str, Any]],
    ts_field: str,
) -> dict[str, Any]:
    """Analyze timestamp ranges from a list of dicts.

    Returns min, max, count with issues (missing/parse failure), and any
    apparent timezone clues.
    """
    parsed: list[datetime] = []
    missing = 0
    for r in records:
        val = r.get(ts_field)
        if not val:
            missing += 1
            continue
        try:
            dt = _coerce_dt(val)
            parsed.append(dt)
        except (ValueError, TypeError):
            missing += 1

    result: dict[str, Any] = {
        "field": ts_field,
        "total": len(records),
        "missing": missing,
    }
    if parsed:
        result["min"] = min(parsed)
        result["max"] = max(parsed)
    return result


def detect_null_drift(
    raw_fields: list[dict[str, Any]],
    stored_fields: list[dict[str, Any]],
    field_names: list[str],
) -> dict[str, Any]:
    """Compare raw vs stored null rates for specified field names.

    Detects fields that are populated in the raw API response but null in the
    stored DB (e.g. fields lost during transform, FK gaps).
    """
    raw_count = len(raw_fields)
    stored_count = len(stored_fields)
    drift: dict[str, dict[str, Any]] = {}
    for field in field_names:
        raw_null = sum(1 for r in raw_fields if r.get(field) is None)
        stored_null = sum(1 for r in stored_fields if r.get(field) is None)
        diff = stored_null - raw_null
        if diff != 0:
            drift[field] = {
                "field": field,
                "raw_null": raw_null,
                "stored_null": stored_null,
                "raw_total": raw_count,
                "stored_total": stored_count,
                "extra_nulls": diff,
            }
    return {"drift_fields": drift, "total_drift_fields": len(drift)}


def check_duplicates(
    rows: list[dict[str, Any]], id_field: str = "geotab_id"
) -> dict[str, Any]:
    """Detect duplicate IDs in a list of dicts."""
    seen: dict[str, int] = {}
    dups: list[dict[str, Any]] = []
    for r in rows:
        val = r.get(id_field)
        if val is None:
            continue
        key = str(val)
        if key in seen:
            seen[key] += 1
        else:
            seen[key] = 1
    for key, count in seen.items():
        if count > 1:
            dups.append({"id": key, "count": count})
    return {"duplicates": dups, "total_duplicates": len(dups)}


def fk_resolution_rate(
    items: list[dict[str, Any]],
    fk_field: str,
    parent_ids: set[str],
) -> dict[str, Any]:
    """Check what fraction of FK references resolve to a known parent.

    Useful for detecting trips/logs/faults dropped because device.id doesn't
    match any stored Vehicle.geotab_id.
    """
    total = len(items)
    resolved = 0
    unresolved_ids: set[str] = set()
    for item in items:
        fk_val = item.get(fk_field)
        if fk_val and str(fk_val) in parent_ids:
            resolved += 1
        else:
            if fk_val:
                unresolved_ids.add(str(fk_val))
    rate = round(resolved / total * 100, 1) if total else 100.0
    return {
        "total": total,
        "resolved": resolved,
        "unresolved": total - resolved,
        "rate_pct": rate,
        "unresolved_ids": sorted(unresolved_ids)[:50],  # cap output
    }


def check_upsert_windowing(
    sync_meta: dict[str, Any], entity: str, since: datetime
) -> dict[str, Any]:
    """Check if sync metadata is up-to-date for the given entity and window.

    Returns the age of the last sync and a warning if sync is stale vs the
    'since' cutoff.
    """
    last_sync = sync_meta.get(entity)
    result: dict[str, Any] = {
        "entity": entity,
        "last_sync": last_sync,
        "query_window_since": since,
        "stale": False,
        "stale_by_seconds": None,
    }
    if last_sync:
        age = (datetime.now(timezone.utc) - last_sync).total_seconds()
        result["age_seconds"] = round(age, 1)
        if last_sync < since:
            stash = (since - last_sync).total_seconds()
            result["stale"] = True
            result["stale_by_seconds"] = round(stash, 1)
    else:
        result["stale"] = True
        result["age_seconds"] = None
    return result


def field_map_table(
    raw_keys: list[str],
    transform_keys: list[str] | None = None,
    db_columns: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build a field mapping table: raw Geotab → transform field → DB column.

    Each entry shows the raw key, whether it has a transform mapping, and
    whether it maps to a DB column.
    """
    mapping: list[dict[str, Any]] = []
    for key in sorted(raw_keys):
        entry: dict[str, Any] = {"raw_key": key}
        if transform_keys:
            entry["in_transform"] = key in transform_keys
        if db_columns:
            entry["in_db"] = key in db_columns
        mapping.append(entry)
    return mapping


def unit_conversion_verification(
    raw_items: list[dict[str, Any]],
    stored_items: list[dict[str, Any]],
    raw_km_field: str = "distance",
    stored_miles_field: str = "distance_miles",
    factor: float = 0.621371,
) -> dict[str, Any]:
    """Verify that km→mi conversion was applied consistently.

    Samples up to 50 records and compares expected vs actual stored values.
    """
    samples: list[dict[str, Any]] = []
    mismatches = 0
    for i, (raw, stored) in enumerate(zip(raw_items, stored_items)):
        if i >= 50:
            break
        raw_km = float(raw.get(raw_km_field, 0) or 0)
        stored_mi = stored.get(stored_miles_field)
        expected = round(raw_km * factor, 2)
        actual = round(stored_mi, 2) if stored_mi else 0.0
        match = abs(expected - actual) < 0.01
        if not match:
            mismatches += 1
        samples.append(
            {
                "row": i,
                "raw_km": round(raw_km, 2),
                "expected_miles": expected,
                "actual_miles": actual,
                "match": match,
            }
        )
    return {
        "factor": factor,
        "samples_checked": len(samples),
        "mismatches": mismatches,
        "samples": samples,
        "consistent": mismatches == 0,
    }


# ── Internal helpers ──────────────────────────────── #


def _coerce_dt(val: Any) -> datetime:
    """Parse a datetime from various formats."""
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    text = str(val).replace("Z", "+00:00").replace(" ", "T")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
