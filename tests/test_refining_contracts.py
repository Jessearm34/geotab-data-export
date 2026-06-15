"""Tests for refined-data contracts — schema validation at the boundary."""

from datetime import datetime, timezone

from app.data_refining.contracts import (
    DailyMileage,
    DriverMetric,
    DriverSafetyRanking,
    EmissionsEstimate,
    FaultFrequency,
    FuelEfficiencyRow,
    GpsPoint,
    IdleVehicle,
    IdlingSummary,
    KpiValue,
    MaintenanceFault,
    MaintenanceSummary,
    SpeedAnalysis,
    TrendPoint,
    TripPoint,
    VehicleDetail,
    VehicleLocation,
    VehicleUtilization,
)


class TestKpiValue:
    def test_basic(self):
        k = KpiValue(key="test", label="Test", value=42)
        assert k.key == "test"
        assert k.value == 42

    def test_none_value(self):
        k = KpiValue(key="x", label="X", value=None)
        assert k.value is None

    def test_negative_delta(self):
        k = KpiValue(key="x", label="X", value=10, delta=-5.0)
        assert k.delta == -5.0

    def test_defaults(self):
        k = KpiValue(key="k", label="K")
        assert k.value is None
        assert k.delta is None
        assert k.unit == ""
        assert k.delta_good_when_up is True
        assert k.hint == ""


class TestTrendPoint:
    def test_basic(self):
        t = TrendPoint(day="2026-01-15", mileage=100.5, fuel=10.0, trips=3)
        assert t.day == "2026-01-15"
        assert t.mileage == 100.5
        assert t.trips == 3

    def test_defaults(self):
        t = TrendPoint(day="2026-01-01")
        assert t.mileage == 0.0
        assert t.fuel == 0.0
        assert t.trips == 0


class TestVehicleUtilization:
    def test_basic(self):
        v = VehicleUtilization(vehicle_id=1, label="A1", total_miles=500)
        assert v.vehicle_id == 1
        assert v.total_miles == 500.0
        assert v.utilization_percentage == 0.0

class TestDriverMetric:
    def test_basic(self):
        d = DriverMetric(driver_id=1, name="Alice", trip_count=10, distance_driven=500.0)
        assert d.name == "Alice"
        assert d.average_trip_length == 0.0

    def test_zero_defaults(self):
        d = DriverMetric(driver_id=1, name="Bob")
        assert d.trip_count == 0
        assert d.distance_driven == 0.0


class TestDriverSafetyRanking:
    def test_high_score(self):
        d = DriverSafetyRanking(driver_id=1, name="Safe", score=95.0)
        assert d.score == 95.0
        assert d.idle_pct == 0.0

    def test_low_score(self):
        d = DriverSafetyRanking(driver_id=2, name="Idler", idle_pct=30.0, score=70.0)
        assert d.score == 70.0


class TestFuelEfficiencyRow:
    def test_mpg_calculation(self):
        f = FuelEfficiencyRow(vehicle_id=1, label="A1", total_miles=300, fuel_used=30, mpg=10.0)
        assert f.mpg == 10.0


class TestIdleVehicle:
    def test_basic(self):
        v = IdleVehicle(vehicle_id=1, label="A1", idle_seconds=900.0, idle_pct=25.0)
        assert v.idle_seconds == 900.0
        assert v.idle_pct == 25.0


class TestIdlingSummary:
    def test_aggregate(self):
        s = IdlingSummary(total_idle_hours=5.5, idle_pct=12.3)
        assert s.total_idle_hours == 5.5
        assert s.vehicles == []

    def test_with_vehicles(self):
        s = IdlingSummary(vehicles=[IdleVehicle(vehicle_id=1, label="A1", idle_seconds=100, idle_pct=10)], total_idle_hours=0.5, idle_pct=10)
        assert len(s.vehicles) == 1


class TestSpeedAnalysis:
    def test_basic(self):
        s = SpeedAnalysis(total_gps_points=100, speeding_count=5, speeding_pct=5.0, avg_speed=45.0, max_speed=85.0)
        assert s.total_gps_points == 100
        assert s.speeding_count == 5

    def test_no_data(self):
        s = SpeedAnalysis()
        assert s.total_gps_points == 0
        assert s.speed_distribution == []


class TestEmissionsEstimate:
    def test_basic(self):
        e = EmissionsEstimate(total_fuel_gal=100, co2_lbs=2000, co2_tons=1.0)
        assert e.co2_tons == 1.0

    def test_zeros(self):
        e = EmissionsEstimate()
        assert e.total_fuel_gal == 0.0
        assert e.co2_tons == 0.0


class TestMaintenanceFault:
    def test_basic(self):
        f = MaintenanceFault(vehicle="A1", timestamp="2026-01-01T12:00:00", fault_code="P0301", description="Misfire")
        assert f.fault_code == "P0301"
        assert f.description == "Misfire"

    def test_no_description(self):
        f = MaintenanceFault(vehicle="A1", timestamp="2026-01-01T12:00:00", fault_code="P0000")
        assert f.description is None


class TestFaultFrequency:
    def test_basic(self):
        f = FaultFrequency(fault_code="P0301", count=5)
        assert f.count == 5

    def test_zero_default(self):
        f = FaultFrequency(fault_code="P0000")
        assert f.count == 0


class TestMaintenanceSummary:
    def test_basic(self):
        m = MaintenanceSummary(open_fault_counts=10)
        assert m.fault_frequency == []
        assert m.current_faults == []


class TestVehicleLocation:
    def test_basic(self):
        v = VehicleLocation(vehicle="A1", latitude=40.0, longitude=-80.0, speed=55, timestamp="2026-01-01T12:00:00")
        assert v.latitude == 40.0
        assert v.status == "stopped"

    def test_moving(self):
        v = VehicleLocation(vehicle="A1", latitude=40.0, longitude=-80.0, speed=55, timestamp="2026-01-01T12:00:00", status="moving")
        assert v.status == "moving"


class TestVehicleDetail:
    def test_empty(self):
        v = VehicleDetail()
        assert v.daily_mileage == []
        assert v.trip_history == []
        assert v.gps_points == []


class TestTripPoint:
    def test_basic(self):
        t = TripPoint(start_time="2026-01-01T10:00:00", end_time="2026-01-01T11:00:00", distance_miles=100)
        assert t.distance_miles == 100.0


class TestGpsPoint:
    def test_basic(self):
        g = GpsPoint(lat=40.0, lon=-80.0, speed=55, timestamp="2026-01-01T10:30:00")
        assert g.speed == 55.0


class TestDailyMileage:
    def test_basic(self):
        d = DailyMileage(day="2026-01-15", miles=150.5)
        assert d.miles == 150.5
