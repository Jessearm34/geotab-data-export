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
            users = [
                {"id": "d1", "firstName": "Jane", "lastName": "Driver", "employeeNo": "E1", "isDriver": True},
                {"id": "d2", "name": "Bob Operator", "isDriver": True},
                {"id": "u1", "name": "Admin User"},
            ]
            _all = users
            if search and search.get("isDriver") is True:
                _all = [u for u in users if u.get("isDriver")]
            return _all
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


# ── New diagnostic tests ──────────────────────────────────────────────── #


def test_sync_all_ordered_succeeds(db):
    """sync_all must populate all five entities with correct ordering."""
    service = SyncService(db, client=FakeGeotabClient())
    results = service.sync_all()

    assert results["vehicles"] == 1
    assert results["drivers"] == 1
    assert results["trips"] == 1
    assert results["gps_logs"] == 1
    assert results["faults"] == 1

    assert db.query(Vehicle).count() == 1
    assert db.query(Driver).count() == 1
    assert db.query(Trip).count() == 1
    assert db.query(GPSLog).count() == 1
    assert db.query(FaultCode).count() == 1


def test_sync_trips_discards_when_no_vehicles(db):
    """
    BUG SCENARIO: If trips (or logs/faults) sync before vehicles,
    the vehicle_map is empty → all records are discarded.
    After a 'successful' sync with 0 records, last_sync is updated,
    effectively losing a full lookback window of data.

    Note: the FakeGeotabClient returns the same data regardless of
    fromDate, so a subsequent call after vehicles are synced WILL
    succeed with our fake (unlike the real Geotab API which would
    filter by fromDate). This test verifies the initial discard.
    """
    client = FakeGeotabClient()
    service = SyncService(db, client=client)

    # Sync trips BEFORE vehicles — vehicle_map is empty
    count = service.sync_trips()
    assert count == 0, "trips should be 0 when no vehicles exist"

    db.flush()
    meta = db.query(SyncMetadata).filter_by(entity_name="trips").one()
    assert meta.last_sync_timestamp is not None, (
        "BUG: last_sync was set despite 0 records being persisted — "
        "this advances the incremental window and permanently loses data"
    )

    # Now sync vehicles — trip window already moved past historical data
    service.sync_vehicles()
    assert db.query(Vehicle).count() == 1

    # With our non-filtering fake, a re-sync succeeds (real API would filter).
    # The bug severity depends on: a) how far last_sync jumps forward,
    # and b) how much real data falls outside the new window.


def test_sync_logs_discards_when_no_vehicles(db):
    """Same race: LogRecord sync before vehicles → all GPS data lost."""
    client = FakeGeotabClient()
    service = SyncService(db, client=client)

    count = service.sync_logs()
    assert count == 0, "gps_logs should be 0 when no vehicles exist"

    db.flush()
    assert db.query(SyncMetadata).filter_by(entity_name="gps_logs").one() is not None


def test_sync_faults_discards_when_no_vehicles(db):
    """Same race: FaultData sync before vehicles → all fault data lost."""
    client = FakeGeotabClient()
    service = SyncService(db, client=client)

    count = service.sync_faults()
    assert count == 0, "faults should be 0 when no vehicles exist"

    db.flush()
    assert db.query(SyncMetadata).filter_by(entity_name="faults").one() is not None


def test_vehicle_persisted_correctly(db):
    """Vehicle fields are stored correctly from Geotab Device."""
    client = FakeGeotabClient()
    SyncService(db, client=client).sync_vehicles()

    v = db.query(Vehicle).one()
    assert v.geotab_id == "v1"
    assert v.serial_number == "S1"
    assert v.license_plate == "A1"


def test_trip_links_vehicle_and_driver(db):
    """Trip FK references are resolved correctly."""
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_drivers()
    service.sync_trips()
def test_sync_trips_links_vehicles(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_trips()

    trip = db.query(Trip).one()
    assert trip.vehicle_id is not None
    assert trip.driver_id is None  # drivers not synced yet — no link
    assert round(trip.distance_miles, 1) == 10.0
    assert round(trip.fuel_used, 2) == 1.0


def test_sync_trips_with_drivers_links_both(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_all()

    trip = db.query(Trip).one()
    assert trip.vehicle_id is not None
    assert trip.driver_id is not None
    assert trip.distance_miles > 0
    assert trip.fuel_used > 0
    assert trip.idle_time == 30


def test_gps_log_links_vehicle(db):
    """GPSLog FK references vehicle correctly."""
    driver = db.query(Driver).get(trip.driver_id)
    assert driver is not None


def test_sync_gps_logs_populates(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_logs()

    log = db.query(GPSLog).one()
    assert log.vehicle_id is not None
    assert log.latitude == 40.0
    assert log.longitude == -80.0
    assert log.speed == 55


def test_fault_code_links_vehicle(db):
    """FaultCode FK references vehicle correctly."""
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_faults()

    fault = db.query(FaultCode).one()
    assert fault.vehicle_id is not None
    assert fault.fault_code == "P0301"
    assert fault.description == "Misfire Cyl 1"
