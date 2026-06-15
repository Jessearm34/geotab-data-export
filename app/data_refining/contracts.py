"""Typed contracts for refined data at the data_refining → rendering boundary.

Rendering code MUST consume these contracts, not raw ORM models or Geotab
payload shapes. This ensures:
  - Independent inspection of refined outputs vs raw gathered data
  - Schema validation at the boundary (catch type errors before rendering)
  - Renderer doesn't need to know about Geotab, SQLAlchemy, or raw entity shapes
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class KpiValue(BaseModel):
    """A single KPI with period-over-period comparison."""

    key: str
    label: str
    value: float | int | None = None
    delta: float | None = None
    unit: str = ""
    delta_good_when_up: bool = True
    hint: str = ""


class TrendPoint(BaseModel):
    """A single point in a daily/montly trend series."""

    day: str
    mileage: float = 0.0
    fuel: float = 0.0
    trips: int = 0


class VehicleUtilization(BaseModel):
    """Vehicle utilization within a time window."""

    vehicle_id: int
    label: str
    total_miles: float = 0.0
    hours_driven: float = 0.0
    utilization_percentage: float = 0.0


class DriverMetric(BaseModel):
    """Driver performance within a time window."""

    driver_id: int
    name: str
    trip_count: int = 0
    distance_driven: float = 0.0
    average_trip_length: float = 0.0


class DriverSafetyRanking(BaseModel):
    """Driver safety ranking with score."""

    driver_id: int
    name: str
    trip_count: int = 0
    distance_driven: float = 0.0
    idle_pct: float = 0.0
    score: float = 100.0


class FuelEfficiencyRow(BaseModel):
    """Per-vehicle fuel efficiency."""

    vehicle_id: int
    label: str
    total_miles: float = 0.0
    fuel_used: float = 0.0
    mpg: float = 0.0


class IdleVehicle(BaseModel):
    """Per-vehicle idling breakdown."""

    vehicle_id: int
    label: str
    idle_seconds: float = 0.0
    idle_pct: float = 0.0


class IdlingSummary(BaseModel):
    """Aggregate idling analysis."""

    vehicles: list[IdleVehicle] = Field(default_factory=list)
    total_idle_hours: float = 0.0
    idle_pct: float = 0.0


class SpeedAnalysis(BaseModel):
    """Speeding and speed distribution."""

    total_gps_points: int = 0
    speeding_count: int = 0
    speeding_pct: float = 0.0
    speed_distribution: list[float] = Field(default_factory=list)
    avg_speed: float = 0.0
    max_speed: float = 0.0


class EmissionsEstimate(BaseModel):
    """CO₂ emissions from fuel consumption."""

    total_fuel_gal: float = 0.0
    co2_lbs: float = 0.0
    co2_tons: float = 0.0


class MaintenanceFault(BaseModel):
    """A single active fault on a vehicle."""

    vehicle: str
    timestamp: str
    fault_code: str
    description: str | None = None


class FaultFrequency(BaseModel):
    """Fault code occurrence count."""

    fault_code: str
    count: int = 0


class MaintenanceSummary(BaseModel):
    """Maintenance and fault analysis."""

    open_fault_counts: int = 0
    fault_frequency: list[FaultFrequency] = Field(default_factory=list)
    current_faults: list[MaintenanceFault] = Field(default_factory=list)


class VehicleLocation(BaseModel):
    """Latest known location of a vehicle."""

    vehicle: str
    latitude: float = 0.0
    longitude: float = 0.0
    speed: float = 0.0
    timestamp: str
    status: str = "stopped"


class VehicleDetail(BaseModel):
    """Detailed view of a single vehicle."""

    daily_mileage: list[dict] = Field(default_factory=list)
    speed_distribution: list[float] = Field(default_factory=list)
    trip_history: list[dict] = Field(default_factory=list)
    gps_points: list[dict] = Field(default_factory=list)


class TripPoint(BaseModel):
    """A single trip for charting purposes."""

    start_time: str
    end_time: str
    distance_miles: float = 0.0
    fuel_used: float = 0.0


class GpsPoint(BaseModel):
    """A single GPS log point for map/chart display."""

    lat: float = 0.0
    lon: float = 0.0
    speed: float = 0.0
    timestamp: str


class DailyMileage(BaseModel):
    """Daily mileage aggregate for a single vehicle."""

    day: str
    miles: float = 0.0
