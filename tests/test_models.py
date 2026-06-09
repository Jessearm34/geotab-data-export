from datetime import datetime, timezone

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
