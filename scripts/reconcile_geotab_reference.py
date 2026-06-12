#!/usr/bin/env python3
"""Reconciliation / audit tool for Geotab data pipeline.

Compares, for a given entity and time window:
  Raw Geotab API → Transformed → Stored DB → Analytics output

Flags count mismatches, timezone / unit / FK / null drift, and sync watermark
issues. Read-only — never modifies Geotab or the local database.

Usage:
  python scripts/reconcile_geotab_reference.py --entity trips --limit 200
  python scripts/reconcile_geotab_reference.py --entity all --db-only
  python scripts/reconcile_geotab_reference.py --entity trips --compare-metrics
  python scripts/reconcile_geotab_reference.py --entity all --output-dir ./output/recon
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from app.data_refining.metrics import AnalyticsService
from app.data_refining.reconciliation import (
    check_duplicates,
    check_timestamps,
    check_upsert_windowing,
    compare_counts,
    detect_null_drift,
    field_map_table,
    fk_resolution_rate,
    unit_conversion_verification,
)
from app.database.session import SessionLocal
from app.geotab.client import GeotabClient, iso_geotab
from app.geotab.transform import (
    driver_from_geotab,
    fault_from_geotab,
    gps_log_from_geotab,
    trip_from_geotab,
    vehicle_from_geotab,
)
from app.models import Driver, FaultCode, FuelEvent, GPSLog, SyncMetadata, Trip, Vehicle

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_ENTITY_CONFIG: dict[str, Any] = {
    "vehicles": {
        "geotab_type": "Device",
        "model": Vehicle,
        "transform": vehicle_from_geotab,
        "db_id_col": "geotab_id",
        "geotab_id_field": "id",
    },
    "drivers": {
        "geotab_type": "User",
        "model": Driver,
        "transform": driver_from_geotab,
        "db_id_col": "geotab_id",
        "geotab_id_field": "id",
    },
    "trips": {
        "geotab_type": "Trip",
        "model": Trip,
        "transform": trip_from_geotab,
        "db_id_col": "geotab_trip_id",
        "geotab_id_field": "id",
    },
    "logs": {
        "geotab_type": "LogRecord",
        "model": GPSLog,
        "transform": gps_log_from_geotab,
        "db_id_col": "geotab_log_id",
        "geotab_id_field": "id",
    },
    "faults": {
        "geotab_type": "FaultData",
        "model": FaultCode,
        "transform": fault_from_geotab,
        "db_id_col": "geotab_fault_id",
        "geotab_id_field": "id",
    },
}

_METRIC_METHODS: dict[str, str] = {
    "fleet_summary": "fleet_summary(since, until)",
    "daily_trends": "daily_trends(since, until)",
    "vehicle_utilization": "vehicle_utilization(since, until)",
    "idling_summary": "idling_summary(since, until)",
    "speed_analysis": "speed_analysis(since, until)",
    "emissions_estimate": "emissions_estimate(since, until)",
    "fuel_efficiency": "fuel_efficiency(since, until)",
    "driver_safety_rankings": "driver_safety_rankings(since, until)",
    "driver_metrics": "driver_metrics(since, until)",
    "maintenance_metrics": "maintenance_metrics(since, until)",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconciliation / audit tool for Geotab data pipeline"
    )
    parser.add_argument(
        "--entity",
        choices=list(_ENTITY_CONFIG.keys()) + ["all"],
        default="trips",
        help="Entity to reconcile (default: trips)",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max raw records to fetch")
    parser.add_argument("--from-date", help="Start date (ISO format)")
    parser.add_argument("--to-date", help="End date (ISO format)")
    parser.add_argument(
        "--output-dir",
        default="output/reconciliation",
        help="Output directory for JSON reports",
    )
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="Skip Geotab API calls, only inspect stored DB state",
    )
    parser.add_argument(
        "--compare-metrics",
        action="store_true",
        help="Also call analytics methods and show metric values",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Enable debug logging"
    )
    return parser.parse_args()


def ensure_output_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def save_report(output_dir: str, name: str, data: Any) -> str:
    path = os.path.join(output_dir, f"{name}.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Report saved: %s", path)
    return path


def resolve_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    if args.from_date:
        since = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
    else:
        since = now - timedelta(days=30)
    if args.to_date:
        until = (datetime.fromisoformat(args.to_date) + timedelta(days=1)).replace(tzinfo=timezone.utc)
    else:
        until = now
    return since, until


def reconcile_entity(
    cfg: dict[str, Any],
    entity: str,
    since: datetime,
    until: datetime,
    limit: int,
    db_only: bool,
    compare_metrics: bool,
    output_dir: str,
    db: SessionLocal,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "entity": entity,
        "window_since": since.isoformat(),
        "window_until": until.isoformat(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ── Stage 1: Raw Geotab API ────────────────────── #
    stage_raw: dict[str, Any] = {"status": "skipped"}
    raw_items: list[dict[str, Any]] = []

    if not db_only:
        try:
            client = GeotabClient()
            search: dict[str, Any] = {
                "fromDate": iso_geotab(since),
                "toDate": iso_geotab(until),
            }
            raw_items = client.get(cfg["geotab_type"], search, results_limit=limit)
            stage_raw = {
                "status": "ok",
                "geotab_type": cfg["geotab_type"],
                "count_requested": limit,
                "count_received": len(raw_items),
                "sample_raw_keys": list(raw_items[0].keys()) if raw_items else [],
            }
        except Exception as exc:
            stage_raw = {"status": "error", "message": str(exc)}
            logger.error("Raw Geotab fetch failed for %s: %s", entity, exc)
    report["stage_1_raw_api"] = stage_raw

    # ── Stage 1.5: Sync Metadata Check ─────────────── #
    sync_meta_rows: dict[str, Any] = {}
    try:
        sync_meta_rows = {
            m.entity_name: m.last_sync_timestamp
            for m in db.query(SyncMetadata).all()
        }
    except Exception as exc:
        logger.warning("Sync metadata query failed (table may be empty): %s", exc)
    sync_meta_str = {
        k: v.isoformat() if v else None for k, v in sync_meta_rows.items()
    }
    windowing = check_upsert_windowing(sync_meta_rows, entity, since)
    report["stage_1.5_sync_watermark"] = {
        "sync_metadata": sync_meta_str,
        "windowing": {
            "entity": windowing["entity"],
            "last_sync": windowing["last_sync"].isoformat() if windowing["last_sync"] else None,
            "age_seconds": windowing.get("age_seconds"),
            "stale": windowing["stale"],
            "stale_by_seconds": windowing.get("stale_by_seconds"),
        },
    }

    # ── Stage 2: Transformed ───────────────────────── #
    stage_transform: dict[str, Any] = {"status": "skipped"}
    stripped_items: list[dict[str, Any]] = []
    if raw_items:
        try:
            transform_fn = cfg["transform"]
            for item in raw_items:
                parsed = transform_fn(item)
                if parsed is not None:
                    stripped_items.append(parsed.model_dump())
            stage_transform = {
                "status": "ok",
                "raw_in": len(raw_items),
                "transformed_out": len(stripped_items),
                "dropped": len(raw_items) - len(stripped_items),
                "transform_sample": stripped_items[:3] if stripped_items else [],
            }
        except Exception as exc:
            stage_transform = {"status": "error", "message": str(exc)}
            logger.error("Transform failed for %s: %s", entity, exc)
    report["stage_2_transformed"] = stage_transform

    # ── Stage 3: Stored DB ─────────────────────────── #
    stage_db: dict[str, Any] = {"status": "ok"}
    stored_items: list[dict[str, Any]] = []
    try:
        model = cfg["model"]
        rows = db.query(model).all()
        stored_items = [
            {c.name: getattr(r, c.name) for c in model.__table__.columns}
            for r in rows
        ]
        stage_db["total_stored"] = len(stored_items)

        if raw_items:
            count_comp = compare_counts(len(raw_items), len(stored_items), label=entity)
            stage_db["count_comparison"] = count_comp

        ts_check = check_timestamps(stored_items, "start_time" if entity in ("trips",) else "timestamp")
        stage_db["timestamp_range"] = ts_check

        dup_check = check_duplicates(stored_items, cfg["db_id_col"])
        stage_db["duplicates"] = dup_check

        if entity in ("trips", "logs", "faults") and raw_items:
            vehicle_ids = {
                str(v.geotab_id) for v in db.query(Vehicle).all()
            }
            fk_field = "vehicle_geotab_id"
            fk_check = fk_resolution_rate(stripped_items, fk_field, vehicle_ids)
            stage_db["fk_resolution"] = fk_check

            null_drift = detect_null_drift(
                raw_items,
                stripped_items,
                ["device", "driver", "distance", "fuelUsed", "idlingDuration"],
            )
            if null_drift["total_drift_fields"] > 0:
                stage_db["null_drift"] = null_drift

        if entity == "trips" and raw_items and stored_items:
            unit_check = unit_conversion_verification(raw_items, stored_items)
            stage_db["unit_conversion_verification"] = unit_check

        field_map = field_map_table(
            list(raw_items[0].keys()) if raw_items else [],
            list(stripped_items[0].keys()) if stripped_items else [],
            [c.name for c in model.__table__.columns],
        )
        stage_db["field_mapping"] = field_map
    except Exception as exc:
        stage_db = {"status": "error", "message": str(exc)}
        logger.error("DB query failed for %s: %s", entity, exc)
    report["stage_3_stored_db"] = stage_db

    # ── Stage 4: Analytics (optional) ──────────────── #
    stage_metrics: dict[str, Any] = {"status": "skipped"}
    if compare_metrics:
        try:
            analytics = AnalyticsService(db)
            metric_values: dict[str, Any] = {}
            for method_name, method_sig in _METRIC_METHODS.items():
                try:
                    fn = getattr(analytics, method_name)
                    result = fn(since, until)
                    if hasattr(result, "model_dump"):
                        result = result.model_dump()
                    metric_values[method_name] = result
                except Exception as exc:
                    metric_values[method_name] = f"ERROR: {exc}"
            stage_metrics = {"status": "ok", "metrics": metric_values}
        except Exception as exc:
            stage_metrics = {"status": "error", "message": str(exc)}
            logger.error("Analytics method call failed: %s", exc)
    report["stage_4_analytics"] = stage_metrics

    return report


def main() -> None:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    output_dir = ensure_output_dir(args.output_dir)
    since, until = resolve_window(args)
    logger.info("Window: %s → %s", since.isoformat(), until.isoformat())

    entities = list(_ENTITY_CONFIG.keys()) if args.entity == "all" else [args.entity]

    db = SessionLocal()
    all_reports: dict[str, Any] = {
        "window_since": since.isoformat(),
        "window_until": until.isoformat(),
        "db_only": args.db_only,
        "entities": {},
    }

    try:
        for entity in entities:
            cfg = _ENTITY_CONFIG[entity]
            logger.info("Reconciling entity=%s", entity)
            report = reconcile_entity(
                cfg, entity, since, until, args.limit, args.db_only,
                args.compare_metrics, output_dir, db,
            )
            all_reports["entities"][entity] = report
            save_report(output_dir, f"recon_{entity}", report)

        save_report(output_dir, "recon_all", all_reports)
        logger.info("All reports written to %s", output_dir)

        # Print summary to stdout
        print("\n=== Reconciliation Summary ===\n")
        for entity, report in all_reports["entities"].items():
            raw = report.get("stage_1_raw_api", {})
            transform = report.get("stage_2_transformed", {})
            db_stage = report.get("stage_3_stored_db", {})
            water = report.get("stage_1.5_sync_watermark", {}).get("windowing", {})

            raw_count = raw.get("count_received", "N/A")
            stored_count = db_stage.get("total_stored", "N/A")
            comp = db_stage.get("count_comparison", {})
            dup = db_stage.get("duplicates", {})
            ws = water if isinstance(water, dict) else {}

            print(f"  Entity: {entity}")
            print(f"    Raw API:    {raw_count}")
            print(f"    Stored DB:  {stored_count}")
            if comp:
                flag = "⚠️ MISMATCH" if comp.get("mismatch") else "OK"
                print(f"    Count:      {flag} (diff={comp.get('difference', '?')})")
            if dup and dup.get("total_duplicates", 0) > 0:
                print(f"    Duplicates: {dup['total_duplicates']}")
            if ws.get("stale"):
                print(f"    Sync stale: yes ({ws.get('stale_by_seconds', '?')}s behind window)")
            if transform:
                drops = transform.get("dropped", 0)
                if drops:
                    print(f"    Transform drops: {drops}")
            print()

    finally:
        db.close()


if __name__ == "__main__":
    main()
