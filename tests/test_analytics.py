from datetime import datetime, timedelta, timezone

from app.analytics.services import AnalyticsService
from app.models import Driver, Trip, Vehicle


def test_fleet_summary_and_driver_metrics(db):
    vehicle = Vehicle(geotab_id="v1")
    driver = Driver(geotab_id="d1", name="Jane Driver")
    db.add_all([vehicle, driver])
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            Trip(geotab_trip_id="t1", vehicle_id=vehicle.id, driver_id=driver.id, start_time=now - timedelta(hours=2), end_time=now - timedelta(hours=1), distance_miles=100, fuel_used=10, idle_time=120),
            Trip(geotab_trip_id="t2", vehicle_id=vehicle.id, driver_id=driver.id, start_time=now - timedelta(hours=5), end_time=now - timedelta(hours=4), distance_miles=50, fuel_used=5, idle_time=60),
        ]
    )
    db.commit()

    analytics = AnalyticsService(db)
    summary = analytics.fleet_summary(now - timedelta(days=1))
    drivers = analytics.driver_metrics(now - timedelta(days=1))

    assert summary.total_vehicles == 1
    assert summary.active_vehicles == 1
    assert summary.total_fleet_miles == 150
    assert summary.average_mpg == 10
    assert drivers[0]["trip_count"] == 2
    assert drivers[0]["average_trip_length"] == 75
