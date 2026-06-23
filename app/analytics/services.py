from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Date, Float, case, cast, desc, func, select
from sqlalchemy.orm import Session

from app.models import Driver, FaultCode, GPSLog, Trip, Vehicle
from app.observability import timed
from app.schemas.domain import FleetSummary

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _since(since: datetime | None) -> datetime:
        return since or datetime.now(timezone.utc) - timedelta(days=30)

    @staticmethod
    def _until(until: datetime | None) -> datetime:
        return until or datetime.now(timezone.utc)

    # Geotab Trip entity has no fuelUsed property — estimate fuel from distance.
    # Industry average: ~8 MPG for diesel fleet vehicles.
    ESTIMATED_MPG = 8.0

    @timed()
    def fleet_summary(self, since: datetime | None = None, until: datetime | None = None) -> FleetSummary:
        since, until = self._since(since), self._until(until)
        total_vehicles = self.db.scalar(select(func.count(Vehicle.id))) or 0
        trip_count = self.db.scalar(select(func.count(Trip.id)).where(Trip.start_time.between(since, until))) or 0
        logger.info("dashboard_query fleet_summary total_vehicles=%s trip_count=%s since=%s until=%s", total_vehicles, trip_count, since.isoformat(), until.isoformat())
        active_vehicles = (
            self.db.scalar(select(func.count(func.distinct(Trip.vehicle_id))).where(Trip.start_time.between(since, until))) or 0
        )
        total_miles = self.db.scalar(select(func.coalesce(func.sum(Trip.distance_miles), 0.0)).where(Trip.start_time.between(since, until))) or 0
        total_miles_f = float(total_miles)
        # Estimate fuel from distance (Geotab API doesn't return fuel_used)
        estimated_fuel = round(total_miles_f / self.ESTIMATED_MPG, 2) if total_miles_f else 0.0
        return FleetSummary(
            total_vehicles=int(total_vehicles),
            active_vehicles=int(active_vehicles),
            total_fleet_miles=round(total_miles_f, 2),
            total_fuel_consumed=estimated_fuel,
            average_mpg=round(self.ESTIMATED_MPG, 2) if total_miles_f else None,
        )

    @timed()
    def vehicle_utilization(self, since: datetime | None = None, until: datetime | None = None) -> list[dict[str, Any]]:
        since, until = self._since(since), self._until(until)
        rows = self.db.execute(
            select(
                Vehicle.id,
                Vehicle.license_plate,
                Vehicle.vin,
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("miles"),
                func.coalesce(func.sum((func.extract("epoch", Trip.end_time) - func.extract("epoch", Trip.start_time)) / 3600), 0.0).label("hours"),
            )
            .join(Trip, (Trip.vehicle_id == Vehicle.id) & (Trip.start_time.between(since, until)), isouter=True)
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

    @timed()
    def driver_metrics(self, since: datetime | None = None, until: datetime | None = None) -> list[dict[str, Any]]:
        since, until = self._since(since), self._until(until)
        rows = self.db.execute(
            select(
                Driver.id,
                Driver.name,
                func.count(Trip.id).label("trip_count"),
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("distance"),
                func.coalesce(func.avg(Trip.distance_miles), 0.0).label("avg_trip"),
            )
            .join(Trip, (Trip.driver_id == Driver.id) & (Trip.start_time.between(since, until)), isouter=True)
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

    @timed()
    def maintenance_metrics(self, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        since, until = self._since(since), self._until(until)
        fault_rows = self.db.execute(
            select(FaultCode.fault_code, func.count(FaultCode.id).label("count"))
            .where(FaultCode.timestamp.between(since, until))
            .group_by(FaultCode.fault_code)
            .order_by(desc("count"))
        ).mappings()
        current = self.db.execute(
            select(FaultCode, Vehicle)
            .join(Vehicle, Vehicle.id == FaultCode.vehicle_id)
            .where(FaultCode.timestamp.between(since, until))
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

    @timed()
    def idle_analysis(self, since: datetime | None = None, until: datetime | None = None) -> dict[str, float]:
        since, until = self._since(since), self._until(until)
        idle = self.db.scalar(select(func.coalesce(func.sum(Trip.idle_time), 0.0)).where(Trip.start_time.between(since, until))) or 0
        driven = (
            self.db.scalar(
                select(func.coalesce(func.sum(func.extract("epoch", Trip.end_time) - func.extract("epoch", Trip.start_time)), 0.0)).where(
                    Trip.start_time.between(since, until)
                )
            )
            or 0
        )
        total = float(idle) + float(driven)
        return {"idle_duration": round(float(idle), 2), "idle_percentage": round((float(idle) / total) * 100, 2) if total else 0}

    @timed()
    def daily_trends(self, since: datetime | None = None, until: datetime | None = None) -> list[dict[str, Any]]:
        since, until = self._since(since), self._until(until)
        rows = self.db.execute(
            select(
                func.date(Trip.start_time).label("day"),
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("miles"),
                func.count(Trip.id).label("trips"),
            )
            .where(Trip.start_time.between(since, until))
            .group_by(func.date(Trip.start_time))
            .order_by(func.date(Trip.start_time))
        ).mappings()
        result = [
            {
                "day": row["day"].isoformat() if isinstance(row["day"], date) else str(row["day"]),
                "mileage": round(float(row["miles"]), 2),
                "fuel": round(float(row["miles"]) / self.ESTIMATED_MPG, 2),
                "trips": int(row["trips"]),
            }
            for row in rows
        ]
        logger.info("dashboard_query daily_trends rows=%s", len(result))
        return result

    @timed()
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
                    "fuel_used": round(trip.distance_miles / self.ESTIMATED_MPG, 2),
                }
                for trip in trip_list
            ],
            "gps_points": [{"lat": log.latitude, "lon": log.longitude, "speed": log.speed, "timestamp": log.timestamp.isoformat()} for log in log_list],
        }

    def _daily_vehicle_mileage(self, vehicle_id: int, since: datetime, until: datetime) -> list[dict[str, Any]]:
        rows = self.db.execute(
            select(func.date(Trip.start_time).label("day"), func.coalesce(func.sum(Trip.distance_miles), 0.0).label("miles"))
            .where(Trip.vehicle_id == vehicle_id, Trip.start_time >= since, Trip.start_time <= until)
            .group_by(func.date(Trip.start_time))
            .order_by(func.date(Trip.start_time))
        ).mappings()
        return [{"day": str(row["day"]), "miles": round(float(row["miles"]), 2)} for row in rows]

    @timed()
    def latest_locations(self, max_age_days: int = 365) -> list[dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        subq = (
            select(GPSLog.vehicle_id, func.max(GPSLog.timestamp).label("max_timestamp"))
            .where(GPSLog.timestamp >= cutoff)
            .group_by(GPSLog.vehicle_id)
            .subquery()
        )
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

    # ── Executive Safety & Sustainability Metrics ─────────────────────── #

    @timed()
    def speed_analysis(self, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        """Speeding analysis: GPS points above threshold, speed distribution."""
        since, until = self._since(since), self._until(until)
        SPEED_THRESHOLD = 70
        stats = self.db.execute(
            select(
                func.count(GPSLog.id).label("count"),
                func.coalesce(func.avg(GPSLog.speed), 0.0).label("avg"),
                func.coalesce(func.max(GPSLog.speed), 0.0).label("max"),
                func.coalesce(func.sum(case((GPSLog.speed > SPEED_THRESHOLD, 1), else_=0)), 0).label("speeding"),
            ).where(GPSLog.timestamp.between(since, until))
        ).one()
        sample = [
            float(r[0]) for r in
            self.db.execute(
                select(GPSLog.speed)
                .where(GPSLog.timestamp.between(since, until))
                .order_by(func.random())
                .limit(1000)
            ).all()
            if r[0] is not None
        ]
        logger.info("dashboard_query speed_analysis gps_points=%s", stats.count)
        return {
            "total_gps_points": int(stats.count),
            "speeding_count": int(stats.speeding),
            "speeding_pct": round((float(stats.speeding) / float(stats.count)) * 100, 2) if stats.count else 0.0,
            "speed_distribution": sample,
            "avg_speed": round(float(stats.avg), 1) if stats.count else 0.0,
            "max_speed": round(float(stats.max), 1) if stats.count else 0.0,
        }

    @timed()
    def fuel_efficiency(self, since: datetime | None = None, until: datetime | None = None) -> list[dict[str, Any]]:
        """Per-vehicle MPG ranking based on distance (fuel estimated at fleet average).
        
        Geotab API does not provide fuel_used, so per-vehicle MPG is derived from
        distance_miles against the fleet-average MPG. Vehicles with more miles rank higher.
        """
        since, until = self._since(since), self._until(until)
        rows = self.db.execute(
            select(
                Vehicle.id,
                Vehicle.license_plate,
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("miles"),
            )
            .join(Trip, Trip.vehicle_id == Vehicle.id)
            .where(Trip.start_time.between(since, until))
            .group_by(Vehicle.id)
            .having(func.coalesce(func.sum(Trip.distance_miles), 0.0) > 0)
            .order_by(desc("miles"))
        ).mappings()
        result = []
        for r in rows:
            miles = float(r["miles"])
            mpg = self.ESTIMATED_MPG  # per-vehicle MPG equals fleet average estimate
            result.append({
                "vehicle_id": r["id"],
                "label": r["license_plate"] or f"Vehicle {r['id']}",
                "total_miles": round(miles, 2),
                "fuel_used": round(miles / self.ESTIMATED_MPG, 2),
                "mpg": mpg,
            })
        return sorted(result, key=lambda x: x["total_miles"], reverse=True)

    @timed()
    def idling_summary(self, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        """Per-vehicle idling breakdown."""
        since, until = self._since(since), self._until(until)
        rows = self.db.execute(
            select(
                Vehicle.id,
                Vehicle.license_plate,
                func.coalesce(func.sum(Trip.idle_time), 0.0).label("idle"),
                func.coalesce(func.sum(
                    func.extract("epoch", Trip.end_time) - func.extract("epoch", Trip.start_time)
                ), 0.0).label("total_time"),
            )
            .join(Trip, Trip.vehicle_id == Vehicle.id)
            .where(Trip.start_time.between(since, until))
            .group_by(Vehicle.id)
            .order_by(desc("idle"))
        ).mappings()
        vehicles = []
        total_idle = 0.0
        total_time = 0.0
        for r in rows:
            idle = float(r["idle"])
            tot = float(r["total_time"])
            total_idle += idle
            total_time += tot
            vehicles.append({
                "vehicle_id": r["id"],
                "label": r["license_plate"] or f"Vehicle {r['id']}",
                "idle_seconds": round(idle, 1),
                "idle_pct": round((idle / tot) * 100, 2) if tot else 0.0,
            })
        return {
            "vehicles": vehicles,
            "total_idle_hours": round(total_idle / 3600, 2),
            "idle_pct": round((total_idle / total_time) * 100, 2) if total_time else 0.0,
        }

    @timed()
    def driver_safety_rankings(self, since: datetime | None = None, until: datetime | None = None) -> list[dict[str, Any]]:
        """Rank drivers by safety score (lower idle % = better score)."""
        since, until = self._since(since), self._until(until)
        rows = self.db.execute(
            select(
                Driver.id,
                Driver.name,
                func.count(Trip.id).label("trip_count"),
                func.coalesce(func.sum(Trip.distance_miles), 0.0).label("distance"),
                func.coalesce(func.sum(Trip.idle_time), 0.0).label("idle"),
                func.coalesce(func.sum(
                    func.extract("epoch", Trip.end_time) - func.extract("epoch", Trip.start_time)
                ), 0.0).label("total_time"),
            )
            .join(Trip, Trip.driver_id == Driver.id)
            .where(Trip.start_time.between(since, until))
            .group_by(Driver.id)
            .order_by(desc("distance"))
        ).mappings()
        rankings = []
        for r in rows:
            total = float(r["total_time"])
            idle = float(r["idle"])
            idle_pct = round((idle / total) * 100, 2) if total else 0.0
            rankings.append({
                "driver_id": r["id"],
                "name": r["name"],
                "trip_count": int(r["trip_count"]),
                "distance_driven": round(float(r["distance"]), 2),
                "idle_pct": idle_pct,
                "score": round(100 - idle_pct, 1),
            })
        return sorted(rankings, key=lambda x: x["score"], reverse=True)

    @timed()
    def emissions_estimate(self, since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
        """Estimate CO₂ emissions from fuel consumption.
        
        EPA factor: ~20.0 lbs CO₂ per gallon of diesel.
        Geotab API does not provide fuel_used, so fuel is estimated from distance
        at the fleet-average MPG rate.
        """
        since, until = self._since(since), self._until(until)
        total_miles = self.db.scalar(
            select(func.coalesce(func.sum(Trip.distance_miles), 0.0)).where(Trip.start_time.between(since, until))
        ) or 0.0
        estimated_fuel = float(total_miles) / self.ESTIMATED_MPG if total_miles else 0.0
        CO2_LBS_PER_GAL = 20.0
        co2_lbs = estimated_fuel * CO2_LBS_PER_GAL
        return {
            "total_fuel_gal": round(estimated_fuel, 2),
            "co2_lbs": round(co2_lbs, 1),
            "co2_tons": round(co2_lbs / 2000, 2),
        }
