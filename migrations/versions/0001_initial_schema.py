"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-06-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("geotab_id", sa.String(length=128), nullable=False),
        sa.Column("serial_number", sa.String(length=128)),
        sa.Column("vin", sa.String(length=64)),
        sa.Column("license_plate", sa.String(length=64)),
        sa.Column("make", sa.String(length=128)),
        sa.Column("model", sa.String(length=128)),
        sa.Column("year", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("geotab_id"),
    )
    op.create_index("ix_vehicles_geotab_id", "vehicles", ["geotab_id"])
    op.create_index("ix_vehicles_vin", "vehicles", ["vin"])

    op.create_table(
        "drivers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("geotab_id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("employee_id", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("geotab_id"),
    )
    op.create_index("ix_drivers_geotab_id", "drivers", ["geotab_id"])
    op.create_index("ix_drivers_employee_id", "drivers", ["employee_id"])

    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("geotab_trip_id", sa.String(length=128), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("driver_id", sa.Integer(), sa.ForeignKey("drivers.id", ondelete="SET NULL")),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("distance_miles", sa.Float(), nullable=False),
        sa.Column("fuel_used", sa.Float(), nullable=False),
        sa.Column("idle_time", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("geotab_trip_id", name="uq_trips_geotab_trip_id"),
    )
    op.create_index("ix_trips_vehicle_id", "trips", ["vehicle_id"])
    op.create_index("ix_trips_driver_id", "trips", ["driver_id"])
    op.create_index("ix_trips_start_time", "trips", ["start_time"])
    op.create_index("ix_trips_end_time", "trips", ["end_time"])

    op.create_table(
        "gps_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("geotab_log_id", sa.String(length=128), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("geotab_log_id", name="uq_gps_logs_geotab_log_id"),
    )
    op.create_index("ix_gps_logs_vehicle_id", "gps_logs", ["vehicle_id"])
    op.create_index("ix_gps_logs_timestamp", "gps_logs", ["timestamp"])

    op.create_table(
        "fault_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("geotab_fault_id", sa.String(length=128), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fault_code", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("geotab_fault_id", name="uq_fault_codes_geotab_fault_id"),
    )
    op.create_index("ix_fault_codes_vehicle_id", "fault_codes", ["vehicle_id"])
    op.create_index("ix_fault_codes_timestamp", "fault_codes", ["timestamp"])

    op.create_table(
        "fuel_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vehicle_id", sa.Integer(), sa.ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fuel_used", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("vehicle_id", "timestamp", name="uq_fuel_events_vehicle_timestamp"),
    )
    op.create_index("ix_fuel_events_vehicle_id", "fuel_events", ["vehicle_id"])
    op.create_index("ix_fuel_events_timestamp", "fuel_events", ["timestamp"])

    op.create_table(
        "sync_metadata",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_name", sa.String(length=64), nullable=False),
        sa.Column("last_sync_timestamp", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("entity_name"),
    )
    op.create_table(
        "sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entity_name", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("records_processed", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text()),
    )
    op.create_index("ix_sync_logs_entity_name", "sync_logs", ["entity_name"])
    op.create_index("ix_sync_logs_started_at", "sync_logs", ["started_at"])


def downgrade() -> None:
    op.drop_table("sync_logs")
    op.drop_table("sync_metadata")
    op.drop_table("fuel_events")
    op.drop_table("fault_codes")
    op.drop_table("gps_logs")
    op.drop_table("trips")
    op.drop_table("drivers")
    op.drop_table("vehicles")
