from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.geotab.client import GeotabClient, iso_geotab
from app.geotab.transform import driver_from_geotab, fault_from_geotab, gps_log_from_geotab, trip_from_geotab, vehicle_from_geotab
from app.models import Driver, FaultCode, GPSLog, SyncLog, SyncMetadata, Trip, Vehicle
from app.schemas.domain import DriverIn, FaultCodeIn, GPSLogIn, TripIn, VehicleIn

logger = logging.getLogger(__name__)
T = TypeVar("T")

_LOGBATCH_SIZE = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SyncService:
    def __init__(self, db: Session, client: GeotabClient | None = None) -> None:
        self.db = db
        self.client = client or GeotabClient()
        self.settings = get_settings()

    def last_sync(self, entity_name: str) -> datetime:
        metadata = self.db.scalar(select(SyncMetadata).where(SyncMetadata.entity_name == entity_name))
        if metadata and metadata.last_sync_timestamp:
            stamp = metadata.last_sync_timestamp
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        return _now() - timedelta(days=365)

    def set_last_sync(self, entity_name: str, timestamp: datetime) -> None:
        metadata = self.db.scalar(select(SyncMetadata).where(SyncMetadata.entity_name == entity_name))
        if metadata is None:
            metadata = SyncMetadata(entity_name=entity_name)
            self.db.add(metadata)
        metadata.last_sync_timestamp = timestamp

    def _run_logged(self, entity_name: str, func: Callable[[], int]) -> int:
        started_at = _now()
        log = SyncLog(entity_name=entity_name, started_at=started_at, status="running", records_processed=0)
        self.db.add(log)
        self.db.flush()
        try:
            processed = func()
            log.status = "success"
            log.records_processed = processed
            log.finished_at = _now()
            self.set_last_sync(entity_name, log.finished_at)
            self.db.commit()
            logger.info("sync_success entity=%s records=%s", entity_name, processed)
            return processed
        except Exception as exc:
            self.db.rollback()
            fail_log = SyncLog(
                entity_name=entity_name,
                started_at=started_at,
                finished_at=_now(),
                status="failed",
                records_processed=0,
                message=str(exc),
            )
            self.db.add(fail_log)
            self.db.commit()
            logger.exception("sync_failed entity=%s", entity_name)
            raise

    def _upsert_batch(self, model: type[Any], rows: list[dict[str, Any]], conflict_cols: list[str]) -> int:
        if not rows:
            return 0
        if self.db.bind and self.db.bind.dialect.name == "postgresql":
            total = 0
            for i in range(0, len(rows), _LOGBATCH_SIZE):
                batch = rows[i : i + _LOGBATCH_SIZE]
                statement = pg_insert(model).values(batch)
                update_cols = {
                    column.name: getattr(statement.excluded, column.name)
                    for column in model.__table__.columns
                    if column.name not in {"id", "created_at"} and column.name not in conflict_cols
                }
                self.db.execute(statement.on_conflict_do_update(index_elements=conflict_cols, set_=update_cols))
                total += len(batch)
                logger.info(
                    "sync_batch entity=%s batch=%d/%d size=%d total=%d",
                    model.__tablename__,
                    i // _LOGBATCH_SIZE + 1,
                    (len(rows) + _LOGBATCH_SIZE - 1) // _LOGBATCH_SIZE,
                    len(batch),
                    total,
                )
            return total

        for row in rows:
            filters = [getattr(model, col) == row[col] for col in conflict_cols]
            existing = self.db.scalar(select(model).where(*filters))
            if existing:
                for key, value in row.items():
                    if key != "id":
                        setattr(existing, key, value)
            else:
                self.db.add(model(**row))
        return len(rows)

    def _vehicle_map(self) -> dict[str, int]:
        return dict(self.db.execute(select(Vehicle.geotab_id, Vehicle.id)).all())

    def _driver_map(self) -> dict[str, int]:
        return dict(self.db.execute(select(Driver.geotab_id, Driver.id)).all())

    def sync_vehicles(self) -> int:
        return self._run_logged("vehicles", self._sync_vehicles)

    def _sync_vehicles(self) -> int:
        items = self.client.get("Device", results_limit=50000)
        logger.info("sync_fetch entity=vehicles raw_count=%s", len(items))
        rows = [vehicle_from_geotab(item).model_dump() for item in items]
        written = self._upsert_batch(Vehicle, rows, ["geotab_id"])
        logger.info("sync_write entity=vehicles written=%s", written)
        return written

    def sync_drivers(self) -> int:
        return self._run_logged("drivers", self._sync_drivers)

    def _sync_drivers(self) -> int:
        items = self.client.get("User", results_limit=50000)
        logger.info("sync_fetch entity=drivers raw_count=%s", len(items))
        rows = [
            driver_from_geotab(item).model_dump()
            for item in items
            if item.get("isDriver")
        ]
        written = self._upsert_batch(Driver, rows, ["geotab_id"])
        logger.info("sync_write entity=drivers written=%s", written)
        return written

    def sync_trips(self) -> int:
        return self._run_logged("trips", self._sync_trips)

    def _sync_trips(self) -> int:
        since = self.last_sync("trips")
        items = self.client.get("Trip", {"fromDate": iso_geotab(since)}, results_limit=50000)
        logger.info("sync_fetch entity=trips raw_count=%s since=%s", len(items), since.isoformat())
        vehicle_ids = self._vehicle_map()
        driver_ids = self._driver_map()
        logger.info("sync_maps vehicle_map_size=%s driver_map_size=%s", len(vehicle_ids), len(driver_ids))
        rows: list[dict[str, Any]] = []
        skipped_no_vehicle = 0
        for parsed in self._parse_many(items, trip_from_geotab):
            vehicle_id = vehicle_ids.get(parsed.vehicle_geotab_id)
            if not vehicle_id:
                skipped_no_vehicle += 1
                continue
            rows.append(
                {
                    "geotab_trip_id": parsed.geotab_trip_id,
                    "vehicle_id": vehicle_id,
                    "driver_id": driver_ids.get(parsed.driver_geotab_id or ""),
                    "start_time": parsed.start_time,
                    "end_time": parsed.end_time,
                    "distance_miles": parsed.distance_miles,
                    "fuel_used": parsed.fuel_used,
                    "idle_time": parsed.idle_time,
                }
            )
        if skipped_no_vehicle:
            logger.warning("sync_skip entity=trips skipped_no_vehicle=%s", skipped_no_vehicle)
        written = self._upsert_batch(Trip, rows, ["geotab_trip_id"])
        logger.info("sync_write entity=trips written=%s", written)
        return written

    def sync_logs(self) -> int:
        return self._run_logged("gps_logs", self._sync_logs)

    def _sync_logs(self) -> int:
        since = self.last_sync("gps_logs")
        items = self.client.get("LogRecord", {"fromDate": iso_geotab(since)}, results_limit=50000)
        logger.info("sync_fetch entity=gps_logs raw_count=%s since=%s", len(items), since.isoformat())
        vehicle_ids = self._vehicle_map()
        logger.info("sync_maps vehicle_map_size=%s", len(vehicle_ids))
        parsed_items = self._parse_many(items, gps_log_from_geotab)
        skipped_no_vehicle = sum(1 for p in parsed_items if p.vehicle_geotab_id not in vehicle_ids)
        rows = [
            {
                "geotab_log_id": parsed.geotab_log_id,
                "vehicle_id": vehicle_ids[parsed.vehicle_geotab_id],
                "timestamp": parsed.timestamp,
                "latitude": parsed.latitude,
                "longitude": parsed.longitude,
                "speed": parsed.speed,
            }
            for parsed in parsed_items
            if parsed.vehicle_geotab_id in vehicle_ids
        ]
        if skipped_no_vehicle:
            logger.warning("sync_skip entity=gps_logs skipped_no_vehicle=%s", skipped_no_vehicle)
        written = self._upsert_batch(GPSLog, rows, ["geotab_log_id"])
        logger.info("sync_write entity=gps_logs written=%s", written)
        return written

    def sync_faults(self) -> int:
        return self._run_logged("faults", self._sync_faults)

    def _sync_faults(self) -> int:
        since = self.last_sync("faults")
        items = self.client.get("FaultData", {"fromDate": iso_geotab(since)}, results_limit=50000)
        logger.info("sync_fetch entity=faults raw_count=%s since=%s", len(items), since.isoformat())
        vehicle_ids = self._vehicle_map()
        logger.info("sync_maps vehicle_map_size=%s", len(vehicle_ids))
        parsed_items = self._parse_many(items, fault_from_geotab)
        skipped_no_vehicle = sum(1 for p in parsed_items if p.vehicle_geotab_id not in vehicle_ids)
        rows = [
            {
                "geotab_fault_id": parsed.geotab_fault_id,
                "vehicle_id": vehicle_ids[parsed.vehicle_geotab_id],
                "timestamp": parsed.timestamp,
                "fault_code": parsed.fault_code,
                "description": parsed.description,
            }
            for parsed in parsed_items
            if parsed.vehicle_geotab_id in vehicle_ids
        ]
        if skipped_no_vehicle:
            logger.warning("sync_skip entity=faults skipped_no_vehicle=%s", skipped_no_vehicle)
        written = self._upsert_batch(FaultCode, rows, ["geotab_fault_id"])
        logger.info("sync_write entity=faults written=%s", written)
        return written

    @staticmethod
    def _parse_many(items: Iterable[dict[str, Any]], parser: Callable[[dict[str, Any]], T | None]) -> list[T]:
        parsed: list[T] = []
        for item in items:
            try:
                value = parser(item)
                if value is not None:
                    parsed.append(value)
            except Exception:
                logger.exception("geotab_transform_failed item_id=%s", item.get("id"))
        return parsed

    def sync_all(self) -> dict[str, int]:
        return {
            "vehicles": self.sync_vehicles(),
            "drivers": self.sync_drivers(),
            "trips": self.sync_trips(),
            "gps_logs": self.sync_logs(),
            "faults": self.sync_faults(),
        }
