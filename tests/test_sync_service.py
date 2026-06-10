from datetime import datetime, timezone

from app.models import Driver, FaultCode, GPSLog, SyncMetadata, Trip, Vehicle
from app.services.sync_service import SyncService


class FakeGeotabClient:
    def __init__(self):
        self.searches = []

    def get(self, type_name, search=None, results_limit=5000):
        self.searches.append((type_name, search))
        if type_name == "Device":
            return [
                {"id": "v1", "serialNumber": "S1", "licensePlate": "A1"},
                {"id": "v2", "serialNumber": "S2", "licensePlate": "A2"},
            ]
        if type_name == "User":
            return [
                {"id": "d1", "firstName": "Jane", "lastName": "Driver", "employeeNo": "E1", "isDriver": True},
                {"id": "d2", "name": "Bob Operator", "isDriver": True},
                {"id": "u1", "name": "Admin User"},
            ]
        if type_name == "Trip":
            return [
                {
                    "id": "t1",
                    "device": {"id": "v1"},
                    "driver": {"id": "d1"},
                    "start": "2026-01-01T10:00:00+00:00",
                    "stop": "2026-01-01T11:00:00+00:00",
                    "distance": 16.0934,
                    "fuelUsed": 3.78541,
                    "idlingDuration": 30,
                }
            ]
        if type_name == "LogRecord":
            return [
                {
                    "id": "g1",
                    "device": {"id": "v1"},
                    "dateTime": "2026-01-01T10:30:00+00:00",
                    "latitude": 40.0,
                    "longitude": -80.0,
                    "speed": 55,
                }
            ]
        if type_name == "FaultData":
            return [
                {
                    "id": "f1",
                    "device": {"id": "v1"},
                    "dateTime": "2026-01-01T11:00:00+00:00",
                    "diagnostic": {"code": "P0301", "name": "Misfire Cyl 1"},
                }
            ]
        return []


def test_sync_all_populates_every_table(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    counts = service.sync_all()

    assert counts["vehicles"] == 2
    assert counts["drivers"] > 0
    assert counts["trips"] == 1
    assert counts["gps_logs"] == 1
    assert counts["faults"] == 1

    assert db.query(Vehicle).count() == 2
    assert db.query(Trip).count() == 1
    assert db.query(GPSLog).count() == 1
    assert db.query(FaultCode).count() == 1
    assert db.query(SyncMetadata).count() == 5


def test_sync_drivers_filters_drivers(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    count = service.sync_drivers()

    assert count == 2, "Should only sync users where isDriver=True"
    names = {r.name for r in db.query(Driver).all()}
    assert "Jane Driver" in names
    assert "Bob Operator" in names
    assert "Admin User" not in names


def test_last_sync_initial_lookback_is_365_days(db):
    service = SyncService(db, client=FakeGeotabClient())
    result = service.last_sync("trips")
    delta = datetime.now(timezone.utc) - result
    assert delta.days >= 364, f"Expected ~365 day lookback, got {delta.days} days"


def test_last_sync_uses_stored_metadata(db):
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.add(SyncMetadata(entity_name="trips", last_sync_timestamp=stamp))
    db.commit()
    assert SyncService(db, client=FakeGeotabClient()).last_sync("trips") == stamp


def test_sync_trips_links_vehicles(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_trips()

    trip = db.query(Trip).one()
    assert trip.vehicle_id is not None
    assert trip.driver_id is None  # drivers not synced yet
    assert round(trip.distance_miles, 1) == 10.0
    assert round(trip.fuel_used, 2) == 1.0


def test_sync_trips_with_drivers_links_both(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_all()

    trip = db.query(Trip).one()
    assert trip.vehicle_id is not None
    assert trip.driver_id is not None
    driver = db.get(Driver, trip.driver_id)
    assert driver is not None


def test_sync_gps_logs_populates(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_logs()

    log = db.query(GPSLog).one()
    assert log.vehicle_id is not None
    assert round(log.latitude, 1) == 40.0
    assert round(log.speed, 1) == 55.0


def test_sync_faults_populates(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_faults()

    fault = db.query(FaultCode).one()
    assert fault.vehicle_id is not None
    assert fault.fault_code == "P0301"
    assert fault.description == "Misfire Cyl 1"


def test_incremental_sync_deduplicates(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_all()
    service.sync_all()

    assert db.query(Vehicle).count() == 2
    assert db.query(Trip).count() == 1
    assert db.query(GPSLog).count() == 1
    assert db.query(FaultCode).count() == 1

    last_sync = {m.entity_name: m.last_sync_timestamp for m in db.query(SyncMetadata).all()}
    for name in ("vehicles", "drivers", "trips", "gps_logs", "faults"):
        assert last_sync.get(name) is not None, f"{name} should have a sync timestamp"


def test_upsert_batch_chunks_large_input(db):
    """_upsert_batch handles row sets larger than _LOGBATCH_SIZE correctly."""
    from app.services.sync_service import _LOGBATCH_SIZE

    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()

    big_rows = []
    for i in range(_LOGBATCH_SIZE + 500):
        big_rows.append({
            "geotab_trip_id": f"big_{i}",
            "vehicle_id": 1,
            "start_time": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "end_time": datetime(2026, 1, 1, tzinfo=timezone.utc) + __import__("datetime").timedelta(hours=1),
            "distance_miles": 10.0,
            "fuel_used": 1.0,
            "idle_time": 0,
        })
    written = service._upsert_batch(Trip, big_rows, ["geotab_trip_id"])
    assert written == _LOGBATCH_SIZE + 500
    assert db.query(Trip).count() == _LOGBATCH_SIZE + 500
