"""Tests for reconciliation/audit helper functions."""

from datetime import datetime, timezone

from app.data_refining.reconciliation import (
    check_duplicates,
    check_timestamps,
    compare_counts,
    detect_null_drift,
    field_map_table,
    fk_resolution_rate,
    unit_conversion_verification,
)


class TestCompareCounts:
    def test_match(self):
        r = compare_counts(100, 100, "trips")
        assert r["mismatch"] is False
        assert r["raw"] == 100
        assert r["stored"] == 100

    def test_mismatch(self):
        r = compare_counts(100, 95, "trips")
        assert r["mismatch"] is True
        assert r["difference"] == -5
        assert r["mismatch_pct"] == 5.0

    def test_zero_raw(self):
        r = compare_counts(0, 10, "logs")
        assert r["mismatch"] is True
        assert r["mismatch_pct"] == 0.0


class TestCheckTimestamps:
    def test_basic_range(self):
        records = [
            {"ts": "2026-01-01T00:00:00Z"},
            {"ts": "2026-01-15T12:00:00Z"},
            {"ts": "2026-01-31T23:59:59Z"},
        ]
        r = check_timestamps(records, "ts")
        assert r["total"] == 3
        assert r["missing"] == 0
        assert r["min"].year == 2026
        assert r["max"].day == 31

    def test_missing_timestamp(self):
        records = [{"ts": "2026-01-01T00:00:00Z"}, {"ts": None}, {"no_ts": "x"}]
        r = check_timestamps(records, "ts")
        assert r["missing"] == 2

    def test_empty_list(self):
        r = check_timestamps([], "ts")
        assert r["total"] == 0
        assert r["missing"] == 0
        assert "min" not in r


class TestDetectNullDrift:
    def test_no_drift(self):
        raw = [{"name": "A", "value": 1}, {"name": "B", "value": 2}]
        stored = [{"name": "A", "value": 1}, {"name": "B", "value": 2}]
        r = detect_null_drift(raw, stored, ["name", "value"])
        assert r["total_drift_fields"] == 0

    def test_extra_nulls_in_stored(self):
        raw = [{"name": "A", "value": 1}, {"name": "B", "value": 2}]
        stored = [{"name": "A", "value": None}, {"name": "B", "value": 2}]
        r = detect_null_drift(raw, stored, ["value"])
        assert r["total_drift_fields"] == 1
        assert r["drift_fields"]["value"]["extra_nulls"] == 1

    def test_all_missing(self):
        raw = [{"x": 1}]
        stored = [{"x": None}]
        r = detect_null_drift(raw, stored, ["x"])
        assert r["drift_fields"]["x"]["extra_nulls"] == 1


class TestCheckDuplicates:
    def test_no_duplicates(self):
        rows = [{"geotab_id": "a"}, {"geotab_id": "b"}, {"geotab_id": "c"}]
        r = check_duplicates(rows)
        assert r["total_duplicates"] == 0

    def test_with_duplicates(self):
        rows = [{"geotab_id": "a"}, {"geotab_id": "b"}, {"geotab_id": "a"}, {"geotab_id": "c"}, {"geotab_id": "a"}]
        r = check_duplicates(rows)
        assert r["total_duplicates"] == 1
        assert r["duplicates"][0]["id"] == "a"
        assert r["duplicates"][0]["count"] == 3

    def test_none_id_skipped(self):
        rows = [{"geotab_id": None}, {"geotab_id": None}]
        r = check_duplicates(rows)
        assert r["total_duplicates"] == 0


class TestFkResolutionRate:
    def test_all_resolved(self):
        items = [{"device": "dev1"}, {"device": "dev2"}]
        parents = {"dev1", "dev2"}
        r = fk_resolution_rate(items, "device", parents)
        assert r["rate_pct"] == 100.0
        assert r["unresolved"] == 0

    def test_partial_resolution(self):
        items = [{"device": "dev1"}, {"device": "dev2"}, {"device": "dev_unknown"}]
        parents = {"dev1", "dev2"}
        r = fk_resolution_rate(items, "device", parents)
        assert r["resolved"] == 2
        assert r["unresolved"] == 1

    def test_empty_items(self):
        r = fk_resolution_rate([], "device", {"dev1"})
        assert r["rate_pct"] == 100.0
        assert r["resolved"] == 0


class TestFieldMapTable:
    def test_basic_mapping(self):
        raw_keys = ["id", "name", "device"]
        raw_keys_out = ["id"]
        r = field_map_table(raw_keys, raw_keys_out, ["id", "name"])
        assert len(r) == 3
        id_entry = [e for e in r if e["raw_key"] == "id"][0]
        assert id_entry["in_transform"] is True
        assert id_entry["in_db"] is True
        device_entry = [e for e in r if e["raw_key"] == "device"][0]
        assert device_entry["in_transform"] is False


class TestUnitConversionVerification:
    def test_consistent(self):
        raw = [{"distance": 100.0}, {"distance": 50.0}]
        stored = [{"distance_miles": 62.1371}, {"distance_miles": 31.06855}]
        r = unit_conversion_verification(raw, stored)
        assert r["consistent"] is True
        assert r["samples_checked"] == 2

    def test_inconsistent(self):
        raw = [{"distance": 100.0}]
        stored = [{"distance_miles": 999.0}]  # way off
        r = unit_conversion_verification(raw, stored)
        assert r["consistent"] is False
        assert r["mismatches"] == 1

    def test_empty(self):
        r = unit_conversion_verification([], [])
        assert r["consistent"] is True
        assert r["samples_checked"] == 0
