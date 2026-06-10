from datetime import datetime, timedelta, timezone

from app.analytics.services import AnalyticsService
from app.models import Driver, FaultCode, GPSLog, Trip, Vehicle


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


def test_speed_analysis_counts_speeding_gps_points(db):
    vehicle = Vehicle(geotab_id="v1", license_plate="A1")
    db.add(vehicle)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([
        GPSLog(geotab_log_id="g1", vehicle_id=vehicle.id, timestamp=now, latitude=0, longitude=0, speed=45),
        GPSLog(geotab_log_id="g2", vehicle_id=vehicle.id, timestamp=now, latitude=0, longitude=0, speed=55),
        GPSLog(geotab_log_id="g3", vehicle_id=vehicle.id, timestamp=now, latitude=0, longitude=0, speed=72),
        GPSLog(geotab_log_id="g4", vehicle_id=vehicle.id, timestamp=now, latitude=0, longitude=0, speed=85),
    ])
    db.commit()

    result = AnalyticsService(db).speed_analysis(now - timedelta(days=1))
    assert result["total_gps_points"] == 4
    assert result["speeding_count"] == 2, "72 and 85 are above 70 mph threshold"
    assert result["avg_speed"] == 64.2
    assert result["max_speed"] == 85.0


def test_fuel_efficiency_ranks_by_mpg(db):
    v1 = Vehicle(geotab_id="v1", license_plate="A1")
    v2 = Vehicle(geotab_id="v2", license_plate="A2")
    db.add_all([v1, v2])
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([
        Trip(geotab_trip_id="t1", vehicle_id=v1.id, start_time=now - timedelta(hours=2), end_time=now - timedelta(hours=1), distance_miles=100, fuel_used=10, idle_time=0),
        Trip(geotab_trip_id="t2", vehicle_id=v2.id, start_time=now - timedelta(hours=4), end_time=now - timedelta(hours=3), distance_miles=60, fuel_used=10, idle_time=0),
    ])
    db.commit()

    result = AnalyticsService(db).fuel_efficiency(now - timedelta(days=1))
    assert len(result) == 2
    assert result[0]["label"] == "A1"  # 10 MPG
    assert result[0]["mpg"] == 10.0
    assert result[1]["mpg"] == 6.0  # 60/10


def test_idling_summary_calculates_totals(db):
    v1 = Vehicle(geotab_id="v1", license_plate="A1")
    db.add(v1)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([
        Trip(geotab_trip_id="t1", vehicle_id=v1.id, start_time=now - timedelta(hours=2), end_time=now - timedelta(hours=1), distance_miles=50, fuel_used=5, idle_time=600),
        Trip(geotab_trip_id="t2", vehicle_id=v1.id, start_time=now - timedelta(hours=4), end_time=now - timedelta(hours=3), distance_miles=30, fuel_used=3, idle_time=300),
    ])
    db.commit()

    result = AnalyticsService(db).idling_summary(now - timedelta(days=1))
    assert len(result["vehicles"]) == 1
    assert result["vehicles"][0]["idle_seconds"] == 900.0
    assert result["total_idle_hours"] == 0.25  # 900 / 3600
    assert result["idle_pct"] > 0


def test_driver_safety_rankings_sorts_by_score(db):
    d1 = Driver(geotab_id="d1", name="Alice")
    d2 = Driver(geotab_id="d2", name="Bob")
    v1 = Vehicle(geotab_id="v1")
    db.add_all([d1, d2, v1])
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([
        Trip(geotab_trip_id="t1", vehicle_id=v1.id, driver_id=d1.id, start_time=now - timedelta(hours=2), end_time=now, distance_miles=50, fuel_used=5, idle_time=100),
        Trip(geotab_trip_id="t2", vehicle_id=v1.id, driver_id=d2.id, start_time=now - timedelta(hours=2), end_time=now, distance_miles=50, fuel_used=5, idle_time=1800),
    ])
    db.commit()

    result = AnalyticsService(db).driver_safety_rankings(now - timedelta(days=1))
    assert len(result) == 2
    assert result[0]["name"] == "Alice"
    assert result[0]["score"] > result[1]["score"]


def test_emissions_estimate_from_fuel(db):
    v1 = Vehicle(geotab_id="v1")
    db.add(v1)
    db.flush()
    now = datetime.now(timezone.utc)
    db.add_all([
        Trip(geotab_trip_id="t1", vehicle_id=v1.id, start_time=now - timedelta(hours=2), end_time=now - timedelta(hours=1), distance_miles=100, fuel_used=20, idle_time=0),
    ])
    db.commit()

    result = AnalyticsService(db).emissions_estimate(now - timedelta(days=1))
    assert result["total_fuel_gal"] == 20.0
    assert result["co2_lbs"] == 400.0      # 20 × 20
    assert result["co2_tons"] == 0.2       # 400 / 2000


def test_daily_trends_returns_data(db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    v = Vehicle(geotab_id="v1")
    db.add(v)
    db.flush()
    db.add(Trip(geotab_trip_id="t_trend", vehicle_id=v.id, start_time=now, end_time=now, distance_miles=50, fuel_used=5, idle_time=0))
    db.commit()

    result = AnalyticsService(db).daily_trends()
    assert len(result) > 0
    assert result[0]["mileage"] == 50.0
    assert result[0]["fuel"] == 5.0
    assert result[0]["trips"] == 1


def test_daily_trends_empty_db(db):
    assert AnalyticsService(db).daily_trends() == []


def test_daily_trends_outside_range(db):
    from datetime import datetime, timezone, timedelta

    v = Vehicle(geotab_id="v1")
    db.add(v)
    db.flush()
    db.add(Trip(geotab_trip_id="t_old", vehicle_id=v.id, start_time=datetime(2020, 1, 1, tzinfo=timezone.utc), end_time=datetime(2020, 1, 1, tzinfo=timezone.utc), distance_miles=10, fuel_used=1, idle_time=0))
    db.commit()

    assert AnalyticsService(db).daily_trends(since=datetime.now(timezone.utc) - timedelta(days=7)) == []


def test_fleet_summary_no_fuel_returns_none(db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    v = Vehicle(geotab_id="v1")
    db.add(v)
    db.flush()
    db.add(Trip(geotab_trip_id="t_no_fuel", vehicle_id=v.id, start_time=now, end_time=now, distance_miles=50, fuel_used=0, idle_time=0))
    db.commit()

    summary = AnalyticsService(db).fleet_summary()
    assert summary.average_mpg is None
    assert summary.total_fleet_miles == 50.0


def test_fleet_summary_empty_db(db):
    summary = AnalyticsService(db).fleet_summary()
    assert summary.total_vehicles == 0
    assert summary.active_vehicles == 0
    assert summary.total_fleet_miles == 0.0
    assert summary.average_mpg is None


def test_vehicle_utilization_no_trips_shows_zero(db):
    from datetime import datetime, timezone

    v = Vehicle(geotab_id="v1", license_plate="Z1")
    db.add(v)
    db.commit()

    result = AnalyticsService(db).vehicle_utilization()
    assert len(result) == 1
    assert result[0]["total_miles"] == 0.0


def test_driver_metrics_no_trips_shows_zero(db):
    db.add(Driver(geotab_id="d1", name="No Trip Driver"))
    db.commit()

    result = AnalyticsService(db).driver_metrics()
    assert len(result) == 1
    assert result[0]["trip_count"] == 0


def test_vehicle_detail_empty(db):
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    result = AnalyticsService(db).vehicle_detail(999, now - timedelta(days=30), now)
    assert result["trip_history"] == []
    assert result["gps_points"] == []


def test_latest_locations_empty(db):
    assert AnalyticsService(db).latest_locations() == []


def test_speed_analysis_empty(db):
    result = AnalyticsService(db).speed_analysis()
    assert result["total_gps_points"] == 0
    assert result["speed_distribution"] == []


def test_fuel_efficiency_empty(db):
    assert AnalyticsService(db).fuel_efficiency() == []


def test_emissions_estimate_empty(db):
    result = AnalyticsService(db).emissions_estimate()
    assert result["total_fuel_gal"] == 0.0
    assert result["co2_tons"] == 0.0


def test_driver_safety_rankings_empty(db):
    assert AnalyticsService(db).driver_safety_rankings() == []


def test_idling_summary_empty(db):
    result = AnalyticsService(db).idling_summary()
    assert result["total_idle_hours"] == 0.0


def test_idle_analysis_empty(db):
    result = AnalyticsService(db).idle_analysis()
    assert result["idle_duration"] == 0.0
    assert result["idle_percentage"] == 0.0


def test_maintenance_metrics_empty(db):
    result = AnalyticsService(db).maintenance_metrics()
    assert result["open_fault_counts"] == 0
