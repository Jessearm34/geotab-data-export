from datetime import datetime, timezone

from app.geotab.transform import _parse_idling_duration, trip_from_geotab
from app.models import Driver, FaultCode, GPSLog, SyncMetadata, Trip, Vehicle


def test_models_create_normalized_records(db):
    vehicle = Vehicle(geotab_id="v1", serial_number="s1", vin="VIN", license_plate="ABC123", make="Ford", model="Transit", year=2024)
    driver = Driver(geotab_id="d1", name="Jane Driver", employee_id="E1")
    db.add_all([vehicle, driver])
    db.flush()
    stamp = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    trip = Trip(geotab_trip_id="t1", vehicle_id=vehicle.id, driver_id=driver.id, start_time=stamp, end_time=stamp.replace(hour=11), distance_miles=20, fuel_used=2, idle_time=60)
    log = GPSLog(geotab_log_id="l1", vehicle_id=vehicle.id, timestamp=stamp, latitude=41.0, longitude=-87.0, speed=35)
    fault = FaultCode(geotab_fault_id="f1", vehicle_id=vehicle.id, timestamp=stamp, fault_code="P001", description="Fault")
    metadata = SyncMetadata(entity_name="trips")
    db.add_all([trip, log, fault, metadata])
    db.commit()

    assert db.query(Vehicle).count() == 1
    assert db.query(Trip).count() == 1
    assert db.query(GPSLog).count() == 1
    assert db.query(FaultCode).count() == 1
    assert db.query(SyncMetadata).count() == 1


def test_parse_idling_duration_numeric():
    assert _parse_idling_duration(30) == 30.0
    assert _parse_idling_duration(0) == 0.0
    assert _parse_idling_duration(120.5) == 120.5


def test_parse_idling_duration_none_and_empty():
    assert _parse_idling_duration(None) == 0.0
    assert _parse_idling_duration("") == 0.0


def test_parse_idling_duration_hhmmss():
    assert _parse_idling_duration("00:00:00") == 0.0
    assert _parse_idling_duration("01:30:00") == 5400.0
    assert _parse_idling_duration("00:05:30") == 330.0
    assert _parse_idling_duration("02:15:45") == 8145.0


def test_parse_idling_duration_invalid_string():
    assert _parse_idling_duration("bad") == 0.0
    assert _parse_idling_duration("abc:def") == 0.0


def test_trip_from_geotab_numeric_idling():
    result = trip_from_geotab({
        "id": "t1",
        "device": {"id": "v1"},
        "start": "2026-01-01T10:00:00+00:00",
        "stop": "2026-01-01T11:00:00+00:00",
        "distance": 16.0934,
        "fuelUsed": 3.78541,
        "idlingDuration": 30,
    })
    assert result is not None
    assert result.idle_time == 30.0


def test_trip_from_geotab_hhmmss_idling():
    result = trip_from_geotab({
        "id": "t2",
        "device": {"id": "v1"},
        "start": "2026-01-01T10:00:00+00:00",
        "stop": "2026-01-01T11:00:00+00:00",
        "distance": 16.0934,
        "fuelUsed": 3.78541,
        "idlingDuration": "00:00:00",
    })
    assert result is not None
    assert result.idle_time == 0.0

    result2 = trip_from_geotab({
        "id": "t3",
        "device": {"id": "v1"},
        "start": "2026-01-01T10:00:00+00:00",
        "stop": "2026-01-01T11:00:00+00:00",
        "distance": 16.0934,
        "fuelUsed": 3.78541,
        "idlingDuration": "01:30:00",
    })
    assert result2 is not None
    assert result2.idle_time == 5400.0


def test_trip_from_geotab_missing_idling():
    result = trip_from_geotab({
        "id": "t4",
        "device": {"id": "v1"},
        "start": "2026-01-01T10:00:00+00:00",
        "stop": "2026-01-01T11:00:00+00:00",
        "distance": 16.0934,
        "fuelUsed": 3.78541,
    })
    assert result is not None
    assert result.idle_time == 0.0
