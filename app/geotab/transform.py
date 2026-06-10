from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.schemas.domain import DriverIn, FaultCodeIn, FuelEventIn, GPSLogIn, TripIn, VehicleIn

logger = logging.getLogger(__name__)
KM_TO_MILES = 0.621371
LITERS_TO_GALLONS = 0.264172


def _parse_idling_duration(value: Any) -> float:
    """Parse idlingDuration which can be numeric seconds or 'HH:MM:SS' string."""
    if value is None or value == "" or value == 0:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    parts = text.split(":")
    if len(parts) == 3:
        try:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except (ValueError, TypeError):
            logger.warning("idlingDuration_parse_failed value=%s", value)
            return 0.0
    try:
        return float(text)
    except (ValueError, TypeError):
        logger.warning("idlingDuration_parse_failed value=%s", value)
        return 0.0


def parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return datetime.now(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _id(value: Any) -> str | None:
    if isinstance(value, dict):
        raw = value.get("id")
        return str(raw) if raw else None
    return str(value) if value else None


def vehicle_from_geotab(item: dict[str, Any]) -> VehicleIn:
    return VehicleIn(
        geotab_id=str(item["id"]),
        serial_number=item.get("serialNumber"),
        vin=item.get("vehicleIdentificationNumber") or item.get("vin"),
        license_plate=item.get("licensePlate"),
        make=item.get("make"),
        model=item.get("model"),
        year=item.get("year"),
    )


def driver_from_geotab(item: dict[str, Any]) -> DriverIn:
    first = item.get("firstName") or ""
    last = item.get("lastName") or ""
    name = item.get("name") or " ".join(part for part in [first, last] if part).strip() or str(item["id"])
    return DriverIn(geotab_id=str(item["id"]), name=name, employee_id=item.get("employeeNo") or item.get("employeeId"))


def trip_from_geotab(item: dict[str, Any]) -> TripIn | None:
    device_id = _id(item.get("device"))
    if not device_id:
        return None
    return TripIn(
        geotab_trip_id=str(item["id"]),
        vehicle_geotab_id=device_id,
        driver_geotab_id=_id(item.get("driver")),
        start_time=parse_dt(item.get("start")),
        end_time=parse_dt(item.get("stop") or item.get("end")),
        distance_miles=float(item.get("distance", 0) or 0) * KM_TO_MILES,
        fuel_used=float(item.get("fuelUsed", 0) or 0) * LITERS_TO_GALLONS,
        idle_time=_parse_idling_duration(item.get("idlingDuration")),
    )


def gps_log_from_geotab(item: dict[str, Any]) -> GPSLogIn | None:
    device_id = _id(item.get("device"))
    if not device_id:
        return None
    return GPSLogIn(
        geotab_log_id=str(item["id"]),
        vehicle_geotab_id=device_id,
        timestamp=parse_dt(item.get("dateTime")),
        latitude=float(item.get("latitude", 0) or 0),
        longitude=float(item.get("longitude", 0) or 0),
        speed=float(item.get("speed", 0) or 0),
    )


def fault_from_geotab(item: dict[str, Any]) -> FaultCodeIn | None:
    device_id = _id(item.get("device"))
    if not device_id:
        return None
    diagnostic = item.get("diagnostic") or {}
    code = diagnostic.get("code") or diagnostic.get("name") or item.get("failureMode") or "UNKNOWN"
    return FaultCodeIn(
        geotab_fault_id=str(item["id"]),
        vehicle_geotab_id=device_id,
        timestamp=parse_dt(item.get("dateTime")),
        fault_code=str(code),
        description=diagnostic.get("name") or item.get("description"),
    )


def fuel_event_from_geotab(item: dict[str, Any]) -> FuelEventIn | None:
    """Create a FuelEventIn from a Geotab trip item (daily aggregated fuel).

    Fuel events are derived from trip data since Geotab does not expose a
    dedicated fuel-consumption entity. The item must contain device and fuel
    data with a start timestamp.
    """
    device_id = _id(item.get("device"))
    if not device_id:
        return None
    fuel_liters = float(item.get("fuelUsed", 0) or 0)
    if fuel_liters <= 0:
        return None
    return FuelEventIn(
        vehicle_geotab_id=device_id,
        timestamp=parse_dt(item.get("start") or item.get("dateTime")),
        fuel_used=fuel_liters * LITERS_TO_GALLONS,
    )
