from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Vehicle(Base, TimestampMixin):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geotab_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    serial_number: Mapped[Optional[str]] = mapped_column(String(128))
    vin: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    license_plate: Mapped[Optional[str]] = mapped_column(String(64))
    make: Mapped[Optional[str]] = mapped_column(String(128))
    model: Mapped[Optional[str]] = mapped_column(String(128))
    year: Mapped[Optional[int]] = mapped_column(Integer)

    trips: Mapped[list["Trip"]] = relationship(back_populates="vehicle")
    gps_logs: Mapped[list["GPSLog"]] = relationship(back_populates="vehicle")
    fault_codes: Mapped[list["FaultCode"]] = relationship(back_populates="vehicle")
    fuel_events: Mapped[list["FuelEvent"]] = relationship(back_populates="vehicle")


class Driver(Base, TimestampMixin):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geotab_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    employee_id: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    trips: Mapped[list["Trip"]] = relationship(back_populates="driver")


class Trip(Base, TimestampMixin):
    __tablename__ = "trips"
    __table_args__ = (
        UniqueConstraint("geotab_trip_id", name="uq_trips_geotab_trip_id"),
        Index("ix_trips_vehicle_id", "vehicle_id"),
        Index("ix_trips_driver_id", "driver_id"),
        Index("ix_trips_start_time", "start_time"),
        Index("ix_trips_end_time", "end_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geotab_trip_id: Mapped[str] = mapped_column(String(128), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    driver_id: Mapped[Optional[int]] = mapped_column(ForeignKey("drivers.id", ondelete="SET NULL"))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    distance_miles: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    fuel_used: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    idle_time: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    vehicle: Mapped[Vehicle] = relationship(back_populates="trips")
    driver: Mapped[Optional[Driver]] = relationship(back_populates="trips")


class GPSLog(Base, TimestampMixin):
    __tablename__ = "gps_logs"
    __table_args__ = (
        UniqueConstraint("geotab_log_id", name="uq_gps_logs_geotab_log_id"),
        Index("ix_gps_logs_vehicle_id", "vehicle_id"),
        Index("ix_gps_logs_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geotab_log_id: Mapped[str] = mapped_column(String(128), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    speed: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    vehicle: Mapped[Vehicle] = relationship(back_populates="gps_logs")


class FaultCode(Base, TimestampMixin):
    __tablename__ = "fault_codes"
    __table_args__ = (
        UniqueConstraint("geotab_fault_id", name="uq_fault_codes_geotab_fault_id"),
        Index("ix_fault_codes_vehicle_id", "vehicle_id"),
        Index("ix_fault_codes_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    geotab_fault_id: Mapped[str] = mapped_column(String(128), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fault_code: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    vehicle: Mapped[Vehicle] = relationship(back_populates="fault_codes")


class FuelEvent(Base, TimestampMixin):
    __tablename__ = "fuel_events"
    __table_args__ = (
        Index("ix_fuel_events_vehicle_id", "vehicle_id"),
        Index("ix_fuel_events_timestamp", "timestamp"),
        UniqueConstraint("vehicle_id", "timestamp", name="uq_fuel_events_vehicle_timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fuel_used: Mapped[float] = mapped_column(Float, nullable=False, default=0)

    vehicle: Mapped[Vehicle] = relationship(back_populates="fuel_events")


class SyncMetadata(Base):
    __tablename__ = "sync_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    last_sync_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncLog(Base):
    __tablename__ = "sync_logs"
    __table_args__ = (Index("ix_sync_logs_started_at", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    records_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text)
