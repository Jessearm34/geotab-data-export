"""Geotab → Postgres sync service.

Pulls vehicles, drivers, trips, GPS logs, and fault codes from the Geotab
JSON-RPC API and upserts them into the local database.

Usage
-----
Run as a standalone script::

    python sync.py                  # sync all entities
    python sync.py vehicles drivers # sync specific entities only

Or import and call programmatically::

    from sync import GeotabSync
    syncer = GeotabSync()
    syncer.run_all()

Environment variables required
-------------------------------
GEOTAB_SERVER    – e.g. "my.geotab.com"
GEOTAB_USERNAME  – Geotab account email
GEOTAB_PASSWORD  – Geotab account password
GEOTAB_DATABASE  – Geotab database name (company name)
DATABASE_URL     – Postgres connection string (inherited from database.py)
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from database import SessionLocal
from models import Driver, FaultCode, GPSLog, SyncLog, SyncMetadata, Trip, Vehicle

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("geotab_sync")

# ── Constants ──────────────────────────────────────────────────────────────

# Geotab returns distances in kilometres; convert to miles.
KM_TO_MILES = 0.621371

# Maximum records to request per Geotab API call (their hard cap is ~50 000).
PAGE_SIZE = 5_000

# Entity names used as keys in SyncMetadata / SyncLog.
ENTITY_VEHICLES = "vehicles"
ENTITY_DRIVERS = "drivers"
ENTITY_TRIPS = "trips"
ENTITY_GPS_LOGS = "gps_logs"
ENTITY_FAULT_CODES = "fault_codes"

ALL_ENTITIES = [
    ENTITY_VEHICLES,
    ENTITY_DRIVERS,
    ENTITY_TRIPS,
    ENTITY_GPS_LOGS,
    ENTITY_FAULT_CODES,
]


# ── Geotab API client ──────────────────────────────────────────────────────


class GeotabAPIError(RuntimeError):
    """Raised when the Geotab JSON-RPC API returns an error."""


class GeotabClient:
    """Thin wrapper around the Geotab JSON-RPC API.

    Authenticates once on construction and refreshes credentials
    automatically when a session-expired error is returned.
    """

    def __init__(
        self,
        server: str,
        username: str,
        password: str,
        database: str,
        timeout: int = 60,
    ) -> None:
        self.server = server.rstrip("/")
        self.username = username
        self.password = password
        self.database = database
        self.timeout = timeout
        self._session_id: str | None = None
        self._session: requests.Session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._authenticate()

    # ── Auth ───────────────────────────────────────────────────────────────

    def _authenticate(self) -> None:
        """Authenticate and store the session credentials."""
        logger.info("Authenticating with Geotab API (server=%s, db=%s)", self.server, self.database)
        result = self._rpc("Authenticate", {
            "userName": self.username,
            "password": self.password,
            "database": self.database,
        }, authenticated=False)
        creds = result.get("credentials", {})
        self._session_id = creds.get("sessionId")
        # The server may redirect us to a different host after auth.
        redirected = result.get("path")
        if redirected and redirected not in ("ThisServer", "", None):
            self.server = f"https://{redirected}"
            logger.info("Redirected to server: %s", self.server)
        logger.info("Authenticated successfully (session_id=...%s)", str(self._session_id)[-6:] if self._session_id else "None")

    # ── RPC ────────────────────────────────────────────────────────────────

    @property
    def _url(self) -> str:
        base = self.server if self.server.startswith("http") else f"https://{self.server}"
        return f"{base}/apiv1"

    def _rpc(self, method: str, params: dict[str, Any], authenticated: bool = True) -> Any:
        """Execute a single JSON-RPC call and return the result."""
        if authenticated:
            params["credentials"] = {
                "userName": self.username,
                "database": self.database,
                "sessionId": self._session_id,
            }
        payload = {"method": method, "params": params}
        try:
            resp = self._session.post(self._url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise GeotabAPIError(f"HTTP error calling {method}: {exc}") from exc

        data = resp.json()
        if "error" in data:
            err = data["error"]
            # Session expired — re-authenticate once and retry.
            if isinstance(err, dict) and err.get("name") in ("InvalidUserException", "DbUnavailableException"):
                if authenticated:
                    logger.warning("Session expired, re-authenticating…")
                    self._authenticate()
                    return self._rpc(method, params, authenticated=True)
            raise GeotabAPIError(f"Geotab API error in {method}: {err}")

        return data.get("result")

    # ── Get (with pagination) ──────────────────────────────────────────────

    def get(
        self,
        type_name: str,
        search: dict[str, Any] | None = None,
        results_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all records of *type_name*, handling pagination automatically.

        Geotab's ``Get`` call returns at most ``resultsLimit`` records per
        request.  We fetch one page at a time and stop when fewer records than
        the page size are returned, indicating we have reached the end.
        Callers should use ``fromDate``/``toDate`` in *search* to keep result
        sets manageable for high-volume entity types (LogRecord, Trip).
        """
        return self._get_paged(type_name, search, results_limit)

    def _get_paged(
        self,
        type_name: str,
        search: dict[str, Any] | None,
        results_limit: int | None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages for *type_name* and return the combined list."""
        all_records: list[dict[str, Any]] = []
        limit = PAGE_SIZE

        while True:
            params: dict[str, Any] = {"typeName": type_name, "resultsLimit": limit}
            if search:
                params["search"] = search

            batch = self._rpc("Get", params)
            if not isinstance(batch, list):
                logger.warning("Unexpected response type for %s: %s", type_name, type(batch))
                break

            all_records.extend(batch)
            logger.debug("  fetched %d %s records (total so far: %d)", len(batch), type_name, len(all_records))

            # If we got fewer records than the page size, we're done.
            if len(batch) < limit:
                break

            # If a hard cap was requested and we've hit it, stop.
            if results_limit and len(all_records) >= results_limit:
                all_records = all_records[:results_limit]
                break

            # Geotab doesn't have a universal offset parameter — if we got a
            # full page, warn and stop to avoid an infinite loop.  Callers
            # should use date-range searches to limit result sets.
            logger.warning(
                "Received a full page (%d) of %s — there may be more records. "
                "Consider narrowing the search with fromDate/toDate.",
                limit,
                type_name,
            )
            break

        return all_records


# ── Sync helpers ───────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_or_create_sync_metadata(db: Session, entity_name: str) -> SyncMetadata:
    meta = db.scalar(select(SyncMetadata).where(SyncMetadata.entity_name == entity_name))
    if meta is None:
        meta = SyncMetadata(entity_name=entity_name, last_sync_timestamp=None)
        db.add(meta)
        db.flush()
    return meta


def _start_sync_log(db: Session, entity_name: str) -> SyncLog:
    log = SyncLog(
        entity_name=entity_name,
        started_at=_now(),
        finished_at=None,
        status="running",
        records_processed=0,
        message=None,
    )
    db.add(log)
    db.flush()
    return log


def _finish_sync_log(
    db: Session,
    log: SyncLog,
    status: str,
    records: int,
    message: str | None = None,
) -> None:
    log.finished_at = _now()
    log.status = status
    log.records_processed = records
    log.message = message
    db.flush()


def _safe_str(value: Any, max_len: int = 128) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s[:max_len] if s else None


def _parse_geotab_datetime(value: Any) -> datetime | None:
    """Parse a Geotab ISO-8601 datetime string into a timezone-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        # Geotab returns strings like "2024-01-15T08:30:00.000Z"
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _geotab_id(obj: Any) -> str | None:
    """Extract the Geotab entity ID string from a nested ``{"id": "..."}`` dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return _safe_str(obj.get("id"), 128)
    return _safe_str(obj, 128)


# ── GeotabSync ─────────────────────────────────────────────────────────────


class GeotabSync:
    """Orchestrates syncing Geotab data into the local Postgres database.

    Each ``sync_*`` method is idempotent: it upserts records keyed on the
    Geotab entity ID, so running it multiple times is safe.
    """

    def __init__(self) -> None:
        server = os.environ["GEOTAB_SERVER"]
        username = os.environ["GEOTAB_USERNAME"]
        password = os.environ["GEOTAB_PASSWORD"]
        database = os.environ["GEOTAB_DATABASE"]
        self.client = GeotabClient(server, username, password, database)

    # ── Vehicles ───────────────────────────────────────────────────────────

    def sync_vehicles(self) -> int:
        """Fetch all vehicles from Geotab and upsert into the Vehicle table.

        Matches on ``geotab_id``; updates vin, make, model, year,
        serial_number, and license_plate.

        Returns the number of records upserted.
        """
        logger.info("Starting vehicle sync…")
        db = SessionLocal()
        try:
            sync_log = _start_sync_log(db, ENTITY_VEHICLES)
            db.commit()
            try:
                raw = self.client.get("Device")
                logger.info("Fetched %d vehicles from Geotab", len(raw))

                rows = []
                for device in raw:
                    gid = _geotab_id(device.get("id"))
                    if not gid:
                        continue
                    rows.append({
                        "geotab_id": gid,
                        "serial_number": _safe_str(device.get("serialNumber"), 128),
                        "vin": _safe_str(device.get("vehicleIdentificationNumber"), 64),
                        "license_plate": _safe_str(device.get("licensePlate"), 64),
                        "make": _safe_str(device.get("engineVehicleIdentification") or device.get("make"), 128),
                        "model": _safe_str(device.get("model"), 128),
                        "year": _parse_year(device.get("year")),
                    })

                if rows:
                    stmt = pg_insert(Vehicle).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["geotab_id"],
                        set_={
                            "serial_number": stmt.excluded.serial_number,
                            "vin": stmt.excluded.vin,
                            "license_plate": stmt.excluded.license_plate,
                            "make": stmt.excluded.make,
                            "model": stmt.excluded.model,
                            "year": stmt.excluded.year,
                            "updated_at": _now(),
                        },
                    )
                    db.execute(stmt)

                meta = _get_or_create_sync_metadata(db, ENTITY_VEHICLES)
                meta.last_sync_timestamp = _now()
                _finish_sync_log(db, sync_log, "success", len(rows))
                db.commit()
                logger.info("Vehicle sync complete: %d records upserted", len(rows))
                return len(rows)

            except Exception as exc:
                db.rollback()
                _finish_sync_log(db, sync_log, "error", 0, str(exc))
                db.commit()
                logger.error("Vehicle sync failed: %s", exc, exc_info=True)
                raise

        finally:
            db.close()

    # ── Drivers ────────────────────────────────────────────────────────────

    def sync_drivers(self) -> int:
        """Fetch all drivers from Geotab and upsert into the Driver table.

        Matches on ``geotab_id``; updates name and employee_id.

        Returns the number of records upserted.
        """
        logger.info("Starting driver sync…")
        db = SessionLocal()
        try:
            sync_log = _start_sync_log(db, ENTITY_DRIVERS)
            db.commit()
            try:
                raw = self.client.get("User")
                logger.info("Fetched %d users from Geotab", len(raw))

                rows = []
                for user in raw:
                    gid = _geotab_id(user.get("id"))
                    if not gid:
                        continue
                    first = _safe_str(user.get("firstName"), 128) or ""
                    last = _safe_str(user.get("lastName"), 128) or ""
                    name = f"{first} {last}".strip() or _safe_str(user.get("name"), 255) or gid
                    rows.append({
                        "geotab_id": gid,
                        "name": name[:255],
                        "employee_id": _safe_str(user.get("employeeNo") or user.get("employeeId"), 128),
                    })

                if rows:
                    stmt = pg_insert(Driver).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["geotab_id"],
                        set_={
                            "name": stmt.excluded.name,
                            "employee_id": stmt.excluded.employee_id,
                            "updated_at": _now(),
                        },
                    )
                    db.execute(stmt)

                meta = _get_or_create_sync_metadata(db, ENTITY_DRIVERS)
                meta.last_sync_timestamp = _now()
                _finish_sync_log(db, sync_log, "success", len(rows))
                db.commit()
                logger.info("Driver sync complete: %d records upserted", len(rows))
                return len(rows)

            except Exception as exc:
                db.rollback()
                _finish_sync_log(db, sync_log, "error", 0, str(exc))
                db.commit()
                logger.error("Driver sync failed: %s", exc, exc_info=True)
                raise

        finally:
            db.close()

    # ── Trips ──────────────────────────────────────────────────────────────

    def sync_trips(self, from_date: datetime | None = None, to_date: datetime | None = None) -> int:
        """Fetch trips from Geotab and upsert into the Trip table.

        Matches on ``geotab_trip_id``; links to Vehicle and Driver via their
        Geotab IDs.  Only trips whose vehicle and driver already exist in the
        database are inserted (orphan trips are skipped with a warning).

        Args:
            from_date: Fetch trips starting on or after this UTC datetime.
                       Defaults to the last successful sync timestamp, or
                       30 days ago if no prior sync exists.
            to_date:   Fetch trips up to this UTC datetime.  Defaults to now.

        Returns the number of records upserted.
        """
        logger.info("Starting trip sync…")
        db = SessionLocal()
        try:
            sync_log = _start_sync_log(db, ENTITY_TRIPS)
            db.commit()
            try:
                meta = _get_or_create_sync_metadata(db, ENTITY_TRIPS)
                db.commit()

                if from_date is None:
                    from_date = meta.last_sync_timestamp
                if from_date is None:
                    from_date = _now().replace(hour=0, minute=0, second=0, microsecond=0)
                    from_date = from_date.replace(day=max(1, from_date.day - 30))
                if to_date is None:
                    to_date = _now()

                search = {
                    "fromDate": from_date.isoformat(),
                    "toDate": to_date.isoformat(),
                }
                raw = self.client.get("Trip", search=search)
                logger.info("Fetched %d trips from Geotab (from=%s to=%s)", len(raw), from_date.date(), to_date.date())

                # Build lookup maps: geotab_id → local PK
                vehicle_map: dict[str, int] = {
                    gid: pk
                    for gid, pk in db.execute(select(Vehicle.geotab_id, Vehicle.id)).all()
                }
                driver_map: dict[str, int] = {
                    gid: pk
                    for gid, pk in db.execute(select(Driver.geotab_id, Driver.id)).all()
                }

                rows = []
                skipped = 0
                for trip in raw:
                    gid = _geotab_id(trip.get("id"))
                    if not gid:
                        continue

                    vehicle_gid = _geotab_id(trip.get("device"))
                    driver_gid = _geotab_id(trip.get("driver"))

                    vehicle_pk = vehicle_map.get(vehicle_gid) if vehicle_gid else None
                    if vehicle_pk is None:
                        skipped += 1
                        continue  # can't link without a vehicle

                    driver_pk = driver_map.get(driver_gid) if driver_gid else None

                    start_time = _parse_geotab_datetime(trip.get("start"))
                    stop_time = _parse_geotab_datetime(trip.get("stop"))
                    if not start_time or not stop_time:
                        skipped += 1
                        continue

                    # Geotab distance is in km; convert to miles.
                    distance_km = float(trip.get("distance") or 0)
                    distance_miles = round(distance_km * KM_TO_MILES, 4)

                    # Idle duration is in seconds.
                    idle_seconds = float(trip.get("idlingDuration") or trip.get("idleDuration") or 0)

                    rows.append({
                        "geotab_trip_id": gid,
                        "vehicle_id": vehicle_pk,
                        "driver_id": driver_pk,
                        "start_time": start_time,
                        "end_time": stop_time,
                        "distance_miles": distance_miles,
                        "fuel_used": 0.0,  # Geotab Trip entity has no fuelUsed
                        "idle_time": idle_seconds,
                    })

                if skipped:
                    logger.warning("Skipped %d trips (missing vehicle or timestamps)", skipped)

                if rows:
                    stmt = pg_insert(Trip).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_trips_geotab_trip_id",
                        set_={
                            "vehicle_id": stmt.excluded.vehicle_id,
                            "driver_id": stmt.excluded.driver_id,
                            "start_time": stmt.excluded.start_time,
                            "end_time": stmt.excluded.end_time,
                            "distance_miles": stmt.excluded.distance_miles,
                            "fuel_used": stmt.excluded.fuel_used,
                            "idle_time": stmt.excluded.idle_time,
                            "updated_at": _now(),
                        },
                    )
                    db.execute(stmt)

                meta.last_sync_timestamp = to_date
                _finish_sync_log(db, sync_log, "success", len(rows))
                db.commit()
                logger.info("Trip sync complete: %d records upserted, %d skipped", len(rows), skipped)
                return len(rows)

            except Exception as exc:
                db.rollback()
                _finish_sync_log(db, sync_log, "error", 0, str(exc))
                db.commit()
                logger.error("Trip sync failed: %s", exc, exc_info=True)
                raise

        finally:
            db.close()

    # ── GPS Logs ───────────────────────────────────────────────────────────

    def sync_gps_logs(self, from_date: datetime | None = None, to_date: datetime | None = None) -> int:
        """Fetch GPS log records from Geotab and upsert into the GPSLog table.

        Matches on ``geotab_log_id``; links to Vehicle via Geotab device ID.
        GPS logs without a matching vehicle are skipped.

        Args:
            from_date: Fetch logs on or after this UTC datetime.
                       Defaults to the last successful sync timestamp, or
                       24 hours ago if no prior sync exists.
            to_date:   Fetch logs up to this UTC datetime.  Defaults to now.

        Returns the number of records upserted.
        """
        logger.info("Starting GPS log sync…")
        db = SessionLocal()
        try:
            sync_log = _start_sync_log(db, ENTITY_GPS_LOGS)
            db.commit()
            try:
                meta = _get_or_create_sync_metadata(db, ENTITY_GPS_LOGS)
                db.commit()

                if from_date is None:
                    from_date = meta.last_sync_timestamp
                if from_date is None:
                    from_date = _now().replace(hour=0, minute=0, second=0, microsecond=0)
                if to_date is None:
                    to_date = _now()

                search = {
                    "fromDate": from_date.isoformat(),
                    "toDate": to_date.isoformat(),
                }
                raw = self.client.get("LogRecord", search=search)
                logger.info("Fetched %d GPS log records from Geotab", len(raw))

                vehicle_map: dict[str, int] = {
                    gid: pk
                    for gid, pk in db.execute(select(Vehicle.geotab_id, Vehicle.id)).all()
                }

                rows = []
                skipped = 0
                for log in raw:
                    gid = _geotab_id(log.get("id"))
                    if not gid:
                        continue

                    vehicle_gid = _geotab_id(log.get("device"))
                    vehicle_pk = vehicle_map.get(vehicle_gid) if vehicle_gid else None
                    if vehicle_pk is None:
                        skipped += 1
                        continue

                    ts = _parse_geotab_datetime(log.get("dateTime"))
                    if not ts:
                        skipped += 1
                        continue

                    lat = log.get("latitude")
                    lon = log.get("longitude")
                    if lat is None or lon is None:
                        skipped += 1
                        continue

                    # Speed is in km/h in Geotab; convert to mph.
                    speed_kmh = float(log.get("speed") or 0)
                    speed_mph = round(speed_kmh * KM_TO_MILES, 2)

                    rows.append({
                        "geotab_log_id": gid,
                        "vehicle_id": vehicle_pk,
                        "timestamp": ts,
                        "latitude": float(lat),
                        "longitude": float(lon),
                        "speed": speed_mph,
                    })

                if skipped:
                    logger.warning("Skipped %d GPS log records (missing vehicle, timestamp, or coordinates)", skipped)

                if rows:
                    stmt = pg_insert(GPSLog).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_gps_logs_geotab_log_id",
                        set_={
                            "vehicle_id": stmt.excluded.vehicle_id,
                            "timestamp": stmt.excluded.timestamp,
                            "latitude": stmt.excluded.latitude,
                            "longitude": stmt.excluded.longitude,
                            "speed": stmt.excluded.speed,
                            "updated_at": _now(),
                        },
                    )
                    db.execute(stmt)

                meta.last_sync_timestamp = to_date
                _finish_sync_log(db, sync_log, "success", len(rows))
                db.commit()
                logger.info("GPS log sync complete: %d records upserted, %d skipped", len(rows), skipped)
                return len(rows)

            except Exception as exc:
                db.rollback()
                _finish_sync_log(db, sync_log, "error", 0, str(exc))
                db.commit()
                logger.error("GPS log sync failed: %s", exc, exc_info=True)
                raise

        finally:
            db.close()

    # ── Fault Codes ────────────────────────────────────────────────────────

    def sync_fault_codes(self, from_date: datetime | None = None, to_date: datetime | None = None) -> int:
        """Fetch fault code records from Geotab and upsert into the FaultCode table.

        Matches on ``geotab_fault_id``; links to Vehicle via Geotab device ID.
        Fault codes without a matching vehicle are skipped.

        Args:
            from_date: Fetch faults on or after this UTC datetime.
                       Defaults to the last successful sync timestamp, or
                       30 days ago if no prior sync exists.
            to_date:   Fetch faults up to this UTC datetime.  Defaults to now.

        Returns the number of records upserted.
        """
        logger.info("Starting fault code sync…")
        db = SessionLocal()
        try:
            sync_log = _start_sync_log(db, ENTITY_FAULT_CODES)
            db.commit()
            try:
                meta = _get_or_create_sync_metadata(db, ENTITY_FAULT_CODES)
                db.commit()

                if from_date is None:
                    from_date = meta.last_sync_timestamp
                if from_date is None:
                    from_date = _now().replace(hour=0, minute=0, second=0, microsecond=0)
                    from_date = from_date.replace(day=max(1, from_date.day - 30))
                if to_date is None:
                    to_date = _now()

                search = {
                    "fromDate": from_date.isoformat(),
                    "toDate": to_date.isoformat(),
                }
                raw = self.client.get("ExceptionEvent", search=search)
                logger.info("Fetched %d fault/exception records from Geotab", len(raw))

                vehicle_map: dict[str, int] = {
                    gid: pk
                    for gid, pk in db.execute(select(Vehicle.geotab_id, Vehicle.id)).all()
                }

                rows = []
                skipped = 0
                for event in raw:
                    gid = _geotab_id(event.get("id"))
                    if not gid:
                        continue

                    vehicle_gid = _geotab_id(event.get("device"))
                    vehicle_pk = vehicle_map.get(vehicle_gid) if vehicle_gid else None
                    if vehicle_pk is None:
                        skipped += 1
                        continue

                    ts = _parse_geotab_datetime(event.get("activeFrom") or event.get("dateTime"))
                    if not ts:
                        skipped += 1
                        continue

                    # The rule/diagnostic name is the closest thing to a fault code.
                    rule = event.get("rule") or {}
                    fault_code = _safe_str(rule.get("name") or rule.get("id") or event.get("id"), 128) or gid
                    description = _safe_str(event.get("comment") or rule.get("comment"), None)

                    rows.append({
                        "geotab_fault_id": gid,
                        "vehicle_id": vehicle_pk,
                        "timestamp": ts,
                        "fault_code": fault_code,
                        "description": description,
                    })

                if skipped:
                    logger.warning("Skipped %d fault records (missing vehicle or timestamp)", skipped)

                if rows:
                    stmt = pg_insert(FaultCode).values(rows)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_fault_codes_geotab_fault_id",
                        set_={
                            "vehicle_id": stmt.excluded.vehicle_id,
                            "timestamp": stmt.excluded.timestamp,
                            "fault_code": stmt.excluded.fault_code,
                            "description": stmt.excluded.description,
                            "updated_at": _now(),
                        },
                    )
                    db.execute(stmt)

                meta.last_sync_timestamp = to_date
                _finish_sync_log(db, sync_log, "success", len(rows))
                db.commit()
                logger.info("Fault code sync complete: %d records upserted, %d skipped", len(rows), skipped)
                return len(rows)

            except Exception as exc:
                db.rollback()
                _finish_sync_log(db, sync_log, "error", 0, str(exc))
                db.commit()
                logger.error("Fault code sync failed: %s", exc, exc_info=True)
                raise

        finally:
            db.close()

    # ── Run all ────────────────────────────────────────────────────────────

    def run_all(
        self,
        entities: list[str] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> dict[str, int]:
        """Run sync for all (or a subset of) entities.

        Args:
            entities:  List of entity names to sync.  Defaults to all.
            from_date: Override the start date for time-windowed entities
                       (trips, gps_logs, fault_codes).
            to_date:   Override the end date for time-windowed entities.

        Returns a dict mapping entity name → records upserted.
        """
        targets = entities or ALL_ENTITIES
        results: dict[str, int] = {}
        errors: list[str] = []

        for entity in targets:
            try:
                if entity == ENTITY_VEHICLES:
                    results[entity] = self.sync_vehicles()
                elif entity == ENTITY_DRIVERS:
                    results[entity] = self.sync_drivers()
                elif entity == ENTITY_TRIPS:
                    results[entity] = self.sync_trips(from_date=from_date, to_date=to_date)
                elif entity == ENTITY_GPS_LOGS:
                    results[entity] = self.sync_gps_logs(from_date=from_date, to_date=to_date)
                elif entity == ENTITY_FAULT_CODES:
                    results[entity] = self.sync_fault_codes(from_date=from_date, to_date=to_date)
                else:
                    logger.warning("Unknown entity: %s — skipping", entity)
            except Exception as exc:
                logger.error("Entity '%s' sync failed: %s", entity, exc)
                errors.append(f"{entity}: {exc}")
                results[entity] = -1  # sentinel for failure

        logger.info("Sync summary: %s", results)
        if errors:
            logger.error("Sync completed with %d error(s):\n  %s", len(errors), "\n  ".join(errors))
        return results


# ── Helpers ────────────────────────────────────────────────────────────────


def _parse_year(value: Any) -> int | None:
    """Parse a year value from Geotab (may be int, string, or None)."""
    if value is None:
        return None
    try:
        y = int(value)
        return y if 1900 <= y <= 2100 else None
    except (ValueError, TypeError):
        return None


# ── CLI entry point ────────────────────────────────────────────────────────


def main() -> None:
    """Run the sync from the command line.

    Usage::

        python sync.py                          # sync all entities
        python sync.py vehicles drivers         # sync specific entities
        python sync.py trips --from 2024-01-01  # sync trips from a date
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Sync Geotab fleet data into the local Postgres database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "entities",
        nargs="*",
        choices=[*ALL_ENTITIES, []],
        metavar="ENTITY",
        help=f"Entities to sync: {', '.join(ALL_ENTITIES)}. Defaults to all.",
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        metavar="YYYY-MM-DD",
        help="Start date for time-windowed entities (trips, gps_logs, fault_codes).",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        metavar="YYYY-MM-DD",
        help="End date for time-windowed entities. Defaults to now.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    from_date: datetime | None = None
    to_date: datetime | None = None

    if args.from_date:
        try:
            from_date = datetime.fromisoformat(args.from_date).replace(tzinfo=timezone.utc)
        except ValueError:
            parser.error(f"Invalid --from date: {args.from_date!r}. Use YYYY-MM-DD.")

    if args.to_date:
        try:
            to_date = datetime.fromisoformat(args.to_date).replace(tzinfo=timezone.utc)
        except ValueError:
            parser.error(f"Invalid --to date: {args.to_date!r}. Use YYYY-MM-DD.")

    # Validate entity names manually (argparse choices don't work well with nargs=*)
    valid = set(ALL_ENTITIES)
    for e in args.entities:
        if e not in valid:
            parser.error(f"Unknown entity {e!r}. Choose from: {', '.join(ALL_ENTITIES)}")

    try:
        syncer = GeotabSync()
    except KeyError as exc:
        logger.error("Missing required environment variable: %s", exc)
        sys.exit(1)

    results = syncer.run_all(
        entities=args.entities or None,
        from_date=from_date,
        to_date=to_date,
    )

    # Exit with a non-zero code if any entity failed.
    if any(v < 0 for v in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
