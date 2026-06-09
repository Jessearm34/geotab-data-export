from datetime import datetime, timezone

from app.models import SyncMetadata, Trip, Vehicle
from app.services.sync_service import SyncService


class FakeGeotabClient:
    def __init__(self):
        self.searches = []

    def get(self, type_name, search=None, results_limit=5000):
        self.searches.append((type_name, search))
        if type_name == "Device":
            return [{"id": "v1", "serialNumber": "S1", "licensePlate": "A1"}]
        if type_name == "Trip":
            return [
                {
                    "id": "t1",
                    "device": {"id": "v1"},
                    "driver": None,
                    "start": "2026-01-01T10:00:00+00:00",
                    "stop": "2026-01-01T11:00:00+00:00",
                    "distance": 16.0934,
                    "fuelUsed": 3.78541,
                    "idlingDuration": 30,
                }
            ]
        return []


def test_incremental_sync_and_upsert(db):
    client = FakeGeotabClient()
    service = SyncService(db, client=client)
    service.sync_vehicles()
    service.sync_trips()
    service.sync_trips()

    assert db.query(Vehicle).count() == 1
    assert db.query(Trip).count() == 1
    assert db.query(SyncMetadata).filter_by(entity_name="trips").one().last_sync_timestamp is not None
    trip_searches = [search for type_name, search in client.searches if type_name == "Trip"]
    assert all("fromDate" in search for search in trip_searches)


def test_last_sync_uses_metadata(db):
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
    db.add(SyncMetadata(entity_name="trips", last_sync_timestamp=stamp))
    db.commit()
    assert SyncService(db, client=FakeGeotabClient()).last_sync("trips") == stamp
