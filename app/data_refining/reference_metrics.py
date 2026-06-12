"""Metric classification: baseline (replicated MyGeotab) vs extended (custom).

Every dashboard metric is registered here with its tier, source method,
and known drift sources. This makes the "replicate first, extend second"
strategy explicit in code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class MetricTier(Enum):
    BASELINE = "baseline"  # Intended to match MyGeotab
    EXTENDED = "extended"  # Custom calculation / additional insight


@dataclass
class MetricDef:
    """Definition of a single dashboard metric."""

    key: str
    label: str
    tier: MetricTier
    source_method: str
    known_drift_sources: list[str] = field(default_factory=list)
    geotab_reference_note: str | None = None


# ── Executive Dashboard Metrics ───────────────────── #

EXECUTIVE_METRICS: list[MetricDef] = [
    MetricDef(
        key="total_vehicles",
        label="Total Vehicles",
        tier=MetricTier.BASELINE,
        source_method="fleet_summary().total_vehicles",
        known_drift_sources=["DB count vs Geotab Device count if sync is stale"],
        geotab_reference_note="Should match MyGeotab device count when fully synced",
    ),
    MetricDef(
        key="active_vehicles",
        label="Active Vehicles",
        tier=MetricTier.BASELINE,
        source_method="fleet_summary().active_vehicles",
        known_drift_sources=[
            "Defined as 'vehicles with ≥1 trip in window'. MyGeotab may use other activity signals.",
            "FK drops from trip sync (trips without matching vehicle) reduce count",
        ],
    ),
    MetricDef(
        key="fleet_miles",
        label="Fleet Miles",
        tier=MetricTier.BASELINE,
        source_method="fleet_summary().total_fleet_miles",
        known_drift_sources=[
            "km→mi conversion factor (0.621371)",
            "Time window boundary inclusion",
            "Sync coverage: stale sync → missing trips → undercount",
        ],
    ),
    MetricDef(
        key="avg_mpg",
        label="Avg MPG",
        tier=MetricTier.BASELINE,
        source_method="fleet_summary().average_mpg",
        known_drift_sources=[
            "Computed as total_miles / total_fuel. MyGeotab may average per-vehicle MPGs instead.",
            "Only includes trips with fuel_used > 0",
        ],
    ),
    MetricDef(
        key="idle_pct",
        label="Idle %",
        tier=MetricTier.BASELINE,
        source_method="idling_summary().idle_pct",
        known_drift_sources=[
            "idlingDuration parsing from Geotab (HH:MM:SS vs numeric seconds)",
            "MyGeotab definition of 'idle' may differ from trip.idle_time field",
        ],
    ),
    MetricDef(
        key="speeding",
        label="Speeding Incidents",
        tier=MetricTier.BASELINE,
        source_method="speed_analysis().speeding_count",
        known_drift_sources=["Hardcoded 70 mph threshold — MyGeotab may be configurable"],
    ),
    MetricDef(
        key="fuel",
        label="Fuel Used",
        tier=MetricTier.BASELINE,
        source_method="fleet_summary().total_fuel_consumed",
        known_drift_sources=["L→gal conversion factor (0.264172)"],
    ),
    MetricDef(
        key="co2",
        label="CO₂ Emissions",
        tier=MetricTier.EXTENDED,
        source_method="emissions_estimate().co2_tons",
        known_drift_sources=[
            "EPA-derived: 20 lb CO₂/gal diesel — not a Geotab-provided metric",
        ],
        geotab_reference_note="Custom EPA calculation, not available in MyGeotab",
    ),
]

EXECUTIVE_CHARTS: list[MetricDef] = [
    MetricDef(
        key="fleet_miles_trend",
        label="Fleet Miles Trend (daily)",
        tier=MetricTier.BASELINE,
        source_method="daily_trends().mileage",
        known_drift_sources=["Daily boundary: Trip.start_time date vs Geotab local-date grouping"],
    ),
    MetricDef(
        key="fuel_trend",
        label="Fuel Usage Trend (daily)",
        tier=MetricTier.BASELINE,
        source_method="daily_trends().fuel",
        known_drift_sources=["Same as fleet_miles_trend"],
    ),
    MetricDef(
        key="vehicle_utilization",
        label="Vehicle Utilization Ranking",
        tier=MetricTier.EXTENDED,
        source_method="vehicle_utilization()",
        known_drift_sources=["Custom calculation: hours_driven / period_hours, not a MyGeotab KPI"],
        geotab_reference_note="Custom ranking metric",
    ),
]

# ── Safety & Sustainability Dashboard Metrics ─────── #

SAFETY_METRICS: list[MetricDef] = [
    MetricDef(
        key="avg_speed",
        label="Avg Speed",
        tier=MetricTier.EXTENDED,
        source_method="speed_analysis().avg_speed",
        known_drift_sources=["Aggregate of all GPS points; MyGeotab may use trip-level averages"],
        geotab_reference_note="Detail metric, not a primary MyGeotab KPI",
    ),
    MetricDef(
        key="max_speed",
        label="Max Speed",
        tier=MetricTier.EXTENDED,
        source_method="speed_analysis().max_speed",
        known_drift_sources=["Single GPS point; may be anomalous"],
        geotab_reference_note="Detail metric",
    ),
    MetricDef(
        key="speeding_pct",
        label="Speeding %",
        tier=MetricTier.BASELINE,
        source_method="speed_analysis().speeding_pct",
        known_drift_sources=["Threshold alignment with MyGeotab"],
    ),
    MetricDef(
        key="idle_hours",
        label="Idle Time",
        tier=MetricTier.BASELINE,
        source_method="idling_summary().total_idle_hours",
        known_drift_sources=["idlingDuration parsing", "MyGeotab idle definition"],
    ),
    MetricDef(
        key="fleet_mpg",
        label="Fleet MPG (best)",
        tier=MetricTier.EXTENDED,
        source_method="fuel_efficiency()[0].mpg",
        known_drift_sources=["Shows best vehicle, not fleet average"],
        geotab_reference_note="Custom: showing top performer, not fleet aggregate",
    ),
    MetricDef(
        key="co2",
        label="CO₂ Emissions",
        tier=MetricTier.EXTENDED,
        source_method="emissions_estimate().co2_tons",
        known_drift_sources=["EPA-derived calculation"],
        geotab_reference_note="Custom EPA calculation",
    ),
    MetricDef(
        key="fuel_gal",
        label="Fuel Used",
        tier=MetricTier.BASELINE,
        source_method="emissions_estimate().total_fuel_gal",
        known_drift_sources=["L→gal conversion factor"],
    ),
    MetricDef(
        key="safety_events",
        label="Fault Events",
        tier=MetricTier.BASELINE,
        source_method="maintenance_metrics().open_fault_counts",
        known_drift_sources=["Count based on fault_code frequency in window"],
    ),
]

SAFETY_CHARTS: list[MetricDef] = [
    MetricDef(
        key="speed_distribution",
        label="Speed Distribution",
        tier=MetricTier.BASELINE,
        source_method="speed_analysis().speed_distribution",
        known_drift_sources=["Sampled 1000 GPS points (not full set)"],
    ),
    MetricDef(
        key="fuel_economy",
        label="Fuel Economy (MPG)",
        tier=MetricTier.BASELINE,
        source_method="fuel_efficiency()",
        known_drift_sources=["km→mi conversion", "only includes vehicles with fuel data"],
    ),
    MetricDef(
        key="idle_chart",
        label="Idle Time % by Vehicle",
        tier=MetricTier.BASELINE,
        source_method="idling_summary().vehicles",
        known_drift_sources=["idlingDuration parsing"],
    ),
    MetricDef(
        key="driver_safety",
        label="Driver Safety Rankings",
        tier=MetricTier.EXTENDED,
        source_method="driver_safety_rankings()",
        known_drift_sources=["Custom score = 100 - idle_pct — not a Geotab metric"],
        geotab_reference_note="Custom safety score based on idle percentage",
    ),
    MetricDef(
        key="fault_frequency",
        label="Safety Exceptions",
        tier=MetricTier.BASELINE,
        source_method="maintenance_metrics().fault_frequency",
        known_drift_sources=["Fault code counting method"],
    ),
]


# ── Lookup helpers ────────────────────────────────── #

_ALL_METRICS: list[MetricDef] = (
    EXECUTIVE_METRICS + EXECUTIVE_CHARTS + SAFETY_METRICS + SAFETY_CHARTS
)

_METRIC_INDEX: dict[str, MetricDef] = {m.key: m for m in _ALL_METRICS}


def get_metric(key: str) -> MetricDef | None:
    return _METRIC_INDEX.get(key)


def baseline_metrics() -> list[MetricDef]:
    return [m for m in _ALL_METRICS if m.tier == MetricTier.BASELINE]


def extended_metrics() -> list[MetricDef]:
    return [m for m in _ALL_METRICS if m.tier == MetricTier.EXTENDED]


def classify_metric(key: str) -> MetricTier | None:
    m = _METRIC_INDEX.get(key)
    return m.tier if m else None


def drift_sources_for(key: str) -> list[str]:
    m = _METRIC_INDEX.get(key)
    return m.known_drift_sources if m else []
