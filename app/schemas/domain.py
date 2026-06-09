from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VehicleIn(BaseModel):
    geotab_id: str
    serial_number: str | None = None
    vin: str | None = None
    license_plate: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None


class DriverIn(BaseModel):
    geotab_id: str
    name: str
    employee_id: str | None = None


class TripIn(BaseModel):
    geotab_trip_id: str
    vehicle_geotab_id: str
    driver_geotab_id: str | None = None
    start_time: datetime
    end_time: datetime
    distance_miles: float = Field(default=0, ge=0)
    fuel_used: float = Field(default=0, ge=0)
    idle_time: float = Field(default=0, ge=0)


class GPSLogIn(BaseModel):
    geotab_log_id: str
    vehicle_geotab_id: str
    timestamp: datetime
    latitude: float
    longitude: float
    speed: float = Field(default=0, ge=0)


class FaultCodeIn(BaseModel):
    geotab_fault_id: str
    vehicle_geotab_id: str
    timestamp: datetime
    fault_code: str
    description: str | None = None


class FuelEventIn(BaseModel):
    vehicle_geotab_id: str
    timestamp: datetime
    fuel_used: float = Field(default=0, ge=0)


class FleetSummary(BaseModel):
    total_vehicles: int
    active_vehicles: int
    total_fleet_miles: float
    total_fuel_consumed: float
    average_mpg: float


class ORMBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)
