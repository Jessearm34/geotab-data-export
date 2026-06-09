from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Date, Float, cast, desc, func, select
from sqlalchemy.orm import Session

from app.models import Driver, FaultCode, GPSLog, Trip, Vehicle
from app.schemas.domain import FleetSummary


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def fleet_summary(self, since: datetime | None = None) -> FleetSummary:
        since = since or datetime.now(timezone.utc) - timedelta(days=30)
        total_vehicles = self.db.scalar(select(func.count(Vehicle.id))) or 0
        active_vehicles = (
            self.db.scalar(select(func.count(func.distinct(Trip.vehicle_id))).where(Trip.start_time >= since)) or 0
        )
        total_miles = self.db.scalar(select(func.coalesce(func.sum(Trip.distance_miles), 0.0)).where(Trip.start_time >= since)) or 0
        fuel = self.db.scalar(select(func.coalesce(func.sum(Trip.fuel_used), 0.0)).where(Trip.start_time >= since)) or 0
        return FleetSummary(
            total_vehicles=int(total_vehicles),
            active_vehicles=int(active_vehicles),
            total_fleet_miles=round(float(total_miles), 2),
            total_fuel_consumed=round(float(fuel), 2),
            average_mpg=round(float(total_miles) / float(fuel), 2) if fuel else 0.0,
        )

    def vehicle_utilization(self, since: datetime | None = None) -> list[dict[str, Any]]:
        since = since or datetime.now(timezone.utc) - timedelta(days=30)
        rows = self.db.execute(
            select(
                Vehicle.id,
                Vehicle.license_plate,
                Vehicle.vin,
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("miles"),
                func.coalesce(func.sum((func.extract("epoch", Trip.end_time) - func.extract("epoch", Trip.start_time)) / 3600), 0.0).label("hours"),
            )
            .join(Trip, Trip.vehicle_id == Vehicle.id, isouter=True)
            .where((Trip.start_time >= since) | (Trip.id.is_(None)))
            .group_by(Vehicle.id)
            .order_by(desc("miles"))
        ).mappings()
        period_hours = max((datetime.now(timezone.utc) - since).total_seconds() / 3600, 1)
        return [
            {
                "vehicle_id": row["id"],
                "label": row["license_plate"] or row["vin"] or f"Vehicle {row['id']}",
                "total_miles": round(float(row["miles"]), 2),
                "hours_driven": round(float(row["hours"]), 2),
                "utilization_percentage": round(min((float(row["hours"]) / period_hours) * 100, 100), 2),
            }
            for row in rows
        ]

    def driver_metrics(self, since: datetime | None = None) -> list[dict[str, Any]]:
        since = since or datetime.now(timezone.utc) - timedelta(days=30)
        rows = self.db.execute(
            select(
                Driver.id,
                Driver.name,
                func.count(Trip.id).label("trip_count"),
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("distance"),
                func.coalesce(func.avg(Trip.distance_miles), 0.0).label("avg_trip"),
            )
            .join(Trip, Trip.driver_id == Driver.id, isouter=True)
            .where((Trip.start_time >= since) | (Trip.id.is_(None)))
            .group_by(Driver.id)
            .order_by(desc("distance"))
        ).mappings()
        return [
            {
                "driver_id": row["id"],
                "name": row["name"],
                "trip_count": int(row["trip_count"]),
                "distance_driven": round(float(row["distance"]), 2),
                "average_trip_length": round(float(row["avg_trip"]), 2),
            }
            for row in rows
        ]

    def maintenance_metrics(self, since: datetime | None = None) -> dict[str, Any]:
        since = since or datetime.now(timezone.utc) - timedelta(days=30)
        fault_rows = self.db.execute(
            select(FaultCode.fault_code, func.count(FaultCode.id).label("count"))
            .where(FaultCode.timestamp >= since)
            .group_by(FaultCode.fault_code)
            .order_by(desc("count"))
        ).mappings()
        current = self.db.execute(
            select(FaultCode, Vehicle)
            .join(Vehicle, Vehicle.id == FaultCode.vehicle_id)
            .where(FaultCode.timestamp >= datetime.now(timezone.utc) - timedelta(days=7))
            .order_by(FaultCode.timestamp.desc())
            .limit(100)
        ).all()
        return {
            "open_fault_counts": sum(int(row["count"]) for row in fault_rows),
            "fault_frequency": [{"fault_code": row["fault_code"], "count": int(row["count"])} for row in fault_rows],
            "current_faults": [
                {
                    "vehicle": vehicle.license_plate or vehicle.vin or vehicle.geotab_id,
                    "timestamp": fault.timestamp.isoformat(),
                    "fault_code": fault.fault_code,
                    "description": fault.description,
                }
                for fault, vehicle in current
            ],
        }

    def idle_analysis(self, since: datetime | None = None) -> dict[str, float]:
        since = since or datetime.now(timezone.utc) - timedelta(days=30)
        idle = self.db.scalar(select(func.coalesce(func.sum(Trip.idle_time), 0.0)).where(Trip.start_time >= since)) or 0
        driven = (
            self.db.scalar(
                select(func.coalesce(func.sum(func.extract("epoch", Trip.end_time) - func.extract("epoch", Trip.start_time)), 0.0)).where(
                    Trip.start_time >= since
                )
            )
            or 0
        )
        total = float(idle) + float(driven)
        return {"idle_duration": round(float(idle), 2), "idle_percentage": round((float(idle) / total) * 100, 2) if total else 0}

    def daily_trends(self, since: datetime | None = None) -> list[dict[str, Any]]:
        since = since or datetime.now(timezone.utc) - timedelta(days=30)
        rows = self.db.execute(
            select(
                cast(Trip.start_time, Date).label("day"),
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("miles"),
                func.coalesce(func.sum(Trip.fuel_used), 0.0).label("fuel"),
                func.count(Trip.id).label("trips"),
            )
            .where(Trip.start_time >= since)
            .group_by("day")
            .order_by("day")
        ).mappings()
        return [
            {
                "day": row["day"].isoformat() if isinstance(row["day"], date) else str(row["day"]),
                "mileage": round(float(row["miles"]), 2),
                "fuel": round(float(row["fuel"]), 2),
                "trips": int(row["trips"]),
            }
            for row in rows
        ]

    def vehicle_detail(self, vehicle_id: int, since: datetime, until: datetime) -> dict[str, Any]:
        trips = self.db.execute(
            select(Trip).where(Trip.vehicle_id == vehicle_id, Trip.start_time >= since, Trip.start_time <= until).order_by(Trip.start_time)
        ).scalars()
        logs = self.db.execute(
            select(GPSLog).where(GPSLog.vehicle_id == vehicle_id, GPSLog.timestamp >= since, GPSLog.timestamp <= until).order_by(GPSLog.timestamp.desc()).limit(500)
        ).scalars()
        trip_list = list(trips)
        log_list = list(logs)
        return {
            "daily_mileage": self._daily_vehicle_mileage(vehicle_id, since, until),
            "speed_distribution": [log.speed for log in log_list],
            "trip_history": [
                {
                    "start_time": trip.start_time.isoformat(),
                    "end_time": trip.end_time.isoformat(),
                    "distance_miles": round(trip.distance_miles, 2),
                    "fuel_used": round(trip.fuel_used, 2),
                }
                for trip in trip_list
            ],
            "gps_points": [{"lat": log.latitude, "lon": log.longitude, "speed": log.speed, "timestamp": log.timestamp.isoformat()} for log in log_list],
        }

    def _daily_vehicle_mileage(self, vehicle_id: int, since: datetime, until: datetime) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(cast(Trip.start_time, Date).label("day"), func.coalesce(func.sum(Trip.distance_miles), 0.0).label("miles"))
            .where(Trip.vehicle_id == vehicle_id, Trip.start_time >= since, Trip.start_time <= until)
            .group_by("day")
            .order_by("day")
        ).mappings()
        return [{"day": str(row["day"]), "miles": round(float(row["miles"]), 2)} for row in rows]

    def latest_locations(self) -> list[dict[str, Any]]:
        subq = select(GPSLog.vehicle_id, func.max(GPSLog.timestamp).label("max_timestamp")).group_by(GPSLog.vehicle_id).subquery()
        rows = self.db.execute(
            select(GPSLog, Vehicle)
            .join(subq, (GPSLog.vehicle_id == subq.c.vehicle_id) & (GPSLog.timestamp == subq.c.max_timestamp))
            .join(Vehicle, Vehicle.id == GPSLog.vehicle_id)
        ).all()
        return [
            {
                "vehicle": vehicle.license_plate or vehicle.vin or vehicle.geotab_id,
                "latitude": log.latitude,
                "longitude": log.longitude,
                "speed": log.speed,
                "timestamp": log.timestamp.isoformat(),
                "status": "moving" if log.speed > 1 else "stopped",
            }
            for log, vehicle in rows
        ]
