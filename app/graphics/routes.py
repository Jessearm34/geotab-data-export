"""Route definitions, page builders, and API endpoints.

All dashboard routes and API handlers live here. Imported by app/main.py
which provides the `rt` decorator via register_routes().
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.auth.security import (
    csrf_token,
    establish_authenticated_session,
    is_authenticated,
    login_allowed,
    record_login_failure,
    record_login_success,
    validate_csrf,
    verify_admin_password,
)
from app.config import get_settings
from app.data_refining.metrics import AnalyticsService
from app.database.session import SessionLocal
from app.graphics.charts import bar_chart, histogram, line_chart, map_chart, monthly_trend_chart
from app.graphics.components import (
    chart_container,
    data_table,
    date_controls,
    empty_state,
    kpi_row,
    page_header,
    panel,
    resolve_date_range,
)
from app.data_refining.reference_metrics import MetricTier, classify_metric
from app.graphics.kpi import Kpi
from app.models import Driver, FaultCode, Trip, Vehicle

logger = logging.getLogger(__name__)


def page(request: Request, title: str, body: str, active_nav: str | None = None) -> HTMLResponse:
    token = csrf_token(request)
    nav_links = [
        ("/", "Executive"),
        ("/safety", "Safety &amp; Sustainability"),
        ("/vehicles", "Vehicles"),
        ("/drivers", "Drivers"),
        ("/maintenance", "Maintenance"),
        ("/fleet-map", "Fleet Map"),
    ]
    nav_html = "".join(
        f'<a href="{path}"{" class=\"active\"" if path == active_nav else ""}>{label}</a>'
        for path, label in nav_links
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} | Geotab Fleet Analytics</title>
  <link rel="stylesheet" href="/static/styles.css">
  <script src="https://unpkg.com/htmx.org@2.0.4"></script>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
</head>
<body>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">Fleet Analytics</div>
      <nav class="nav">{nav_html}
        <form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{token}"><button class="logout" type="submit">Sign out</button></form>
      </nav>
    </aside>
    <main class="main" id="main-content" hx-boost="true">{body}</main>
  </div>
</body>
</html>"""
    return HTMLResponse(html)


def login_page(request: Request, error: str | None = None) -> HTMLResponse:
    token = csrf_token(request)
    error_html = f'<div class="error">{error}</div>' if error else ""
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Login | Geotab Fleet Analytics</title><link rel="stylesheet" href="/static/styles.css"></head>
<body class="login-page"><form class="login-card" method="post" action="/login">
<h1>Fleet Analytics</h1>{error_html}
<input type="hidden" name="csrf_token" value="{token}">
<label>Username<input name="username" autocomplete="username" required></label><br>
<label>Password<input name="password" type="password" autocomplete="current-password" required></label><br>
<button type="submit">Sign in</button>
</form></body></html>"""
    )


def with_db() -> SessionLocal:
    return SessionLocal()


# ── Helpers ──────────────────────────────────────── #


def _kpi_with_tier(key: str, **kwargs) -> Kpi:
    tier = classify_metric(key)
    return Kpi(key=key, tier=tier.value if tier else "", **kwargs)


def _safety_kpis(speed: dict, idling: dict, efficiency: list, emissions: dict, faults: dict) -> list[Kpi]:
    top_mpg = efficiency[0]["mpg"] if efficiency else None
    return [
        _kpi_with_tier(key="avg_speed", label="Avg Speed", value=speed["avg_speed"], unit="mph", hint="Average vehicle speed across all GPS points in this period"),
        _kpi_with_tier(key="max_speed", label="Max Speed", value=speed["max_speed"], unit="mph", hint="Highest recorded speed in this period"),
        _kpi_with_tier(key="speeding_pct", label="Speeding %", value=speed["speeding_pct"], unit="%", hint="Percentage of GPS points above 70 mph", delta_good_when_up=False),
        _kpi_with_tier(key="idle_hours", label="Idle Time", value=idling["total_idle_hours"], unit="hours", hint="Total engine idling time across all vehicles", delta_good_when_up=False),
        _kpi_with_tier(key="fleet_mpg", label="Fleet MPG", value=top_mpg, unit="mpg", hint="Best fuel economy among active vehicles"),
        _kpi_with_tier(key="co2", label="CO₂ Emissions", value=emissions["co2_tons"], unit="tons", hint="Estimated CO₂ from fuel consumption (20 lb/gal diesel)", delta_good_when_up=False),
        _kpi_with_tier(key="fuel_gal", label="Fuel Used", value=emissions["total_fuel_gal"], unit="gal", hint="Total fuel consumed by all vehicles", delta_good_when_up=False),
        _kpi_with_tier(key="safety_events", label="Fault Events", value=faults["open_fault_counts"], hint="Total diagnostic fault code occurrences", delta_good_when_up=False),
    ]


def _exec_kpis(summary: Any, idling: dict, speed: dict, emissions: dict) -> list[Kpi]:
    return [
        _kpi_with_tier(key="total_vehicles", label="Total Vehicles", value=summary.total_vehicles, hint="All vehicles registered in Geotab"),
        _kpi_with_tier(key="active_vehicles", label="Active Vehicles", value=summary.active_vehicles, hint="Vehicles with at least one trip in this period"),
        _kpi_with_tier(key="fleet_miles", label="Fleet Miles", value=summary.total_fleet_miles, unit="miles", hint="Total distance driven across all vehicles"),
        _kpi_with_tier(key="avg_mpg", label="Avg MPG", value=summary.average_mpg, unit="mpg", hint="Fleet-wide average miles per gallon"),
        _kpi_with_tier(key="idle_pct", label="Idle %", value=idling["idle_pct"], unit="%", hint="Percentage of engine-on time spent idling", delta_good_when_up=False),
        _kpi_with_tier(key="speeding", label="Speeding Incidents", value=speed["speeding_count"], hint="GPS points exceeding 70 mph", delta_good_when_up=False),
        _kpi_with_tier(key="co2", label="CO₂ Emissions", value=emissions["co2_tons"], unit="tons", hint="Estimated CO₂ from fuel consumption", delta_good_when_up=False),
        _kpi_with_tier(key="fuel", label="Fuel Used", value=summary.total_fuel_consumed, unit="gal", hint="Total fuel consumed fleet-wide", delta_good_when_up=False),
    ]


def register_routes(rt):
    """Register all routes using the provided decorator."""

    # ── Health ───────────────────────────────────────── #

    @rt("/health")
    def health() -> JSONResponse:
        sync_status: dict[str, Any] = {"status": "ok"}
        try:
            with SessionLocal() as db:
                from app.models import SyncMetadata

                rows = db.query(SyncMetadata).all()
                sync_status["sync"] = {
                    m.entity_name: m.last_sync_timestamp.isoformat() if m.last_sync_timestamp else None
                    for m in rows
                }
                sync_status["db"] = "connected"
        except Exception as exc:
            sync_status["db"] = "error"
            sync_status["error"] = str(exc)
        return JSONResponse(sync_status)

    # ── Auth ─────────────────────────────────────────── #

    @rt("/login")
    async def login(request: Request) -> HTMLResponse | RedirectResponse:
        if is_authenticated(request):
            return RedirectResponse("/", status_code=303)
        if request.method == "POST":
            form = await request.form()
            if not await validate_csrf(request, form):
                return login_page(request, "Session validation failed.")
            username = str(form.get("username", ""))
            password = str(form.get("password", ""))
            if not login_allowed(request, username):
                return login_page(request, "Invalid username or password.")
            settings = get_settings()
            if secrets.compare_digest(username, settings.admin_username) and verify_admin_password(password):
                record_login_success(request, username)
                establish_authenticated_session(request)
                return RedirectResponse("/", status_code=303)
            record_login_failure(request, username)
            return login_page(request, "Invalid username or password.")
        return login_page(request)

    @rt("/logout", methods=["POST"])
    async def logout(request: Request) -> RedirectResponse:
        form = await request.form()
        if await validate_csrf(request, form):
            request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ── Executive Dashboard ─────────────────────────── #

    @rt("/")
    def executive(request: Request, range: str | None = None, start: str | None = None, end: str | None = None) -> HTMLResponse:
        since, until, rng = resolve_date_range(range, start, end)
        with with_db() as db:
            analytics = AnalyticsService(db)
            summary = analytics.fleet_summary(since, until)
            trends = analytics.daily_trends(since, until) or []
            utilization = analytics.vehicle_utilization(since, until)[:10]
            idling = analytics.idling_summary(since, until)
            speed = analytics.speed_analysis(since, until)
            emissions = analytics.emissions_estimate(since, until)
            all_kpis = _exec_kpis(summary, idling, speed, emissions)
            baseline_kpis = [k for k in all_kpis if k.tier == "baseline"]
            extended_kpis = [k for k in all_kpis if k.tier == "extended"]
            body = (page_header("Executive Dashboard", refreshed=datetime.now(timezone.utc))
                    + date_controls(rng, hx_target="#main-content")
                    + kpi_row(baseline_kpis)
                    + '<div class="grid charts">'
                    + chart_container(monthly_trend_chart(trends, "day", "mileage", "Fleet Miles Trend"), "Fleet Miles Trend", dot="#38bdf8")
                    + chart_container(line_chart(trends, "day", "fuel", "Fuel Usage Trend"), "Fuel Usage Trend", dot="#22c55e")
                    + chart_container(bar_chart(utilization, "label", "utilization_percentage", "Vehicle Utilization Ranking"), "Vehicle Utilization Ranking", span_2=True, dot="#f59e0b")
                    + "</div>"
                    + '<h2 class="section-extended">Extended Insights</h2>'
                    + kpi_row(extended_kpis)
                    + '<p class="extended-note">These metrics are custom calculations derived from Geotab data and are not available in MyGeotab.</p>')
            return page(request, "Executive Dashboard", body, active_nav="/")

    # ── Vehicles ────────────────────────────────────── #

    @rt("/vehicles")
    def vehicles(request: Request) -> HTMLResponse:
        with with_db() as db:
            vehicle_list = db.query(Vehicle).order_by(Vehicle.license_plate.asc().nullslast()).all()
            selected = vehicle_list[0].id if vehicle_list else 0
            options = "".join(f'<option value="{v.id}">{v.license_plate or v.vin or v.geotab_id}</option>' for v in vehicle_list)
            body = (page_header("Vehicle Dashboard")
                    + f"""
        <form class="filters" hx-get="/partials/vehicle" hx-target="#vehicle-content" hx-indicator=".htmx-indicator">
        <label>Vehicle<select name="vehicle_id">{options}</select></label>
        <label>From<input name="from_date" type="date" value="{(datetime.now(timezone.utc)-timedelta(days=30)).date()}"></label>
        <label>To<input name="to_date" type="date" value="{datetime.now(timezone.utc).date()}"></label>
        <button type="submit">Apply</button>
        </form>
        <div id="vehicle-content">{_vehicle_partial(selected)}</div>""")
            return page(request, "Vehicle Dashboard", body, active_nav="/vehicles")

    @rt("/partials/vehicle")
    def vehicle_partial_route(vehicle_id: int, from_date: str, to_date: str) -> HTMLResponse:
        return HTMLResponse(_vehicle_partial(vehicle_id, from_date, to_date))

    # ── Drivers ─────────────────────────────────────── #

    @rt("/drivers")
    def drivers(request: Request, range: str | None = None, start: str | None = None, end: str | None = None) -> HTMLResponse:
        since, until, rng = resolve_date_range(range, start, end)
        with with_db() as db:
            metrics = AnalyticsService(db).driver_metrics(since, until)
            rows = data_table(
                ["Driver", "Trips", "Distance", "Avg Trip"],
                [
                    [row["name"], str(row["trip_count"]), str(row["distance_driven"]), str(row["average_trip_length"])]
                    for row in metrics
                ],
                num_cols={1, 2, 3},
            )
            body = (page_header("Driver Dashboard", refreshed=datetime.now(timezone.utc))
                    + date_controls(rng, hx_target="#main-content")
                    + '<div class="grid charts">'
                    + chart_container(bar_chart(metrics[:15], "name", "distance_driven", "Distance Driven"), "Distance Driven", dot="#38bdf8")
                    + chart_container(bar_chart(metrics[:15], "name", "trip_count", "Trips Completed"), "Trips Completed", dot="#22c55e")
                    + panel(rows, title="Driver Performance", span_2=True)
                    + "</div>")
            return page(request, "Driver Dashboard", body, active_nav="/drivers")

    # ── Maintenance ─────────────────────────────────── #

    @rt("/maintenance")
    def maintenance(request: Request, range: str | None = None, start: str | None = None, end: str | None = None) -> HTMLResponse:
        since, until, rng = resolve_date_range(range, start, end)
        with with_db() as db:
            metrics = AnalyticsService(db).maintenance_metrics(since, until)
            current = data_table(
                ["Vehicle", "Date", "Code", "Description"],
                [
                    [row["vehicle"], row["timestamp"][:10], row["fault_code"], row["description"] or ""]
                    for row in metrics["current_faults"]
                ],
            )
            body = (page_header("Maintenance Dashboard", refreshed=datetime.now(timezone.utc))
                    + date_controls(rng, hx_target="#main-content")
                    + '<div class="grid charts">'
                    + chart_container(bar_chart(metrics["fault_frequency"][:15], "fault_code", "count", "Fault Frequency"), "Fault Frequency", dot="#ef4444")
                    + chart_container(bar_chart(metrics["fault_frequency"][:15], "fault_code", "count", "Fault Types"), "Fault Types", dot="#f59e0b")
                    + panel(current, title="Current Faults", span_2=True)
                    + "</div>")
            return page(request, "Maintenance Dashboard", body, active_nav="/maintenance")

    # ── Fleet Map ───────────────────────────────────── #

    @rt("/fleet-map")
    def fleet_map(request: Request) -> HTMLResponse:
        with with_db() as db:
            locations = AnalyticsService(db).latest_locations()
            body = (page_header("Fleet Map")
                    + panel(map_chart(locations, "Latest Vehicle Locations"), title="Latest Vehicle Locations", dot="#38bdf8"))
            return page(request, "Fleet Map", body, active_nav="/fleet-map")

    # ── Safety & Sustainability ─────────────────────── #

    @rt("/safety")
    def safety(request: Request, range: str | None = None, start: str | None = None, end: str | None = None) -> HTMLResponse:
        since, until, rng = resolve_date_range(range, start, end)
        with with_db() as db:
            analytics = AnalyticsService(db)
            speed = analytics.speed_analysis(since, until)
            efficiency = analytics.fuel_efficiency(since, until)[:10]
            idling = analytics.idling_summary(since, until)
            emissions = analytics.emissions_estimate(since, until)
            driver_safety = analytics.driver_safety_rankings(since, until)[:10]
            faults = analytics.maintenance_metrics(since, until)

            kpis = _safety_kpis(speed, idling, efficiency, emissions, faults)
            baseline_kpis = [k for k in kpis if k.tier == "baseline"]
            extended_kpis = [k for k in kpis if k.tier == "extended"]

            speed_hist = chart_container(histogram(speed["speed_distribution"], "Speed Distribution (30d)"), "Speed Distribution", dot="#38bdf8")
            mpg_chart = chart_container(bar_chart(efficiency, "label", "mpg", "Fuel Economy (MPG)"), "Fuel Economy (MPG)", dot="#22c55e")
            idle_chart = chart_container(bar_chart(idling["vehicles"], "label", "idle_pct", "Idle Time % by Vehicle"), "Idle Time % by Vehicle", dot="#f59e0b")
            driver_rows = data_table(
                ["Driver", "Trips", "Miles", "Idle %", "Score"],
                [
                    [d["name"], str(d["trip_count"]), str(d["distance_driven"]), f'{d["idle_pct"]}%', str(d["score"])]
                    for d in driver_safety
                ],
                num_cols={1, 2, 3, 4},
            )
            fault_rows = data_table(
                ["Code", "Description", "Count"],
                [
                    [f["fault_code"], f["description"] or "", str(f["count"])]
                    for f in faults["fault_frequency"][:10]
                ],
                num_cols={2},
            )
            body = (page_header("Safety & Sustainability", refreshed=datetime.now(timezone.utc))
                    + date_controls(rng, hx_target="#main-content")
                    + kpi_row(baseline_kpis)
                    + '<div class="grid charts">'
                    + speed_hist + mpg_chart + idle_chart
                    + panel(driver_rows, title="Driver Safety Rankings")
                    + panel(fault_rows, title="Safety Exceptions (Top Fault Codes)", span_2=True)
                    + "</div>"
                    + '<h2 class="section-extended">Extended Insights</h2>'
                    + kpi_row(extended_kpis)
                    + '<p class="extended-note">These metrics are custom calculations derived from Geotab data and are not available in MyGeotab.</p>')
            return page(request, "Safety & Sustainability", body, active_nav="/safety")

    # ── API Endpoints ───────────────────────────────── #

    @rt("/api/fleet-summary")
    def api_fleet_summary() -> JSONResponse:
        with with_db() as db:
            return JSONResponse(AnalyticsService(db).fleet_summary().model_dump())

    @rt("/api/vehicles")
    def api_vehicles() -> JSONResponse:
        with with_db() as db:
            rows = [
                {"id": v.id, "geotab_id": v.geotab_id, "vin": v.vin, "license_plate": v.license_plate, "make": v.make, "model": v.model, "year": v.year}
                for v in db.query(Vehicle).all()
            ]
            return JSONResponse(rows)

    @rt("/api/drivers")
    def api_drivers() -> JSONResponse:
        with with_db() as db:
            rows = [
                {"id": d.id, "geotab_id": d.geotab_id, "name": d.name, "employee_id": d.employee_id}
                for d in db.query(Driver).all()
            ]
            return JSONResponse(rows)

    @rt("/api/trips")
    def api_trips() -> JSONResponse:
        with with_db() as db:
            rows = [
                {
                    "id": t.id,
                    "vehicle_id": t.vehicle_id,
                    "driver_id": t.driver_id,
                    "start_time": t.start_time.isoformat(),
                    "end_time": t.end_time.isoformat(),
                    "distance_miles": t.distance_miles,
                    "fuel_used": t.fuel_used,
                }
                for t in db.query(Trip).order_by(Trip.start_time.desc()).limit(1000)
            ]
            return JSONResponse(rows)

    @rt("/api/faults")
    def api_faults() -> JSONResponse:
        with with_db() as db:
            rows = [
                {"id": f.id, "vehicle_id": f.vehicle_id, "timestamp": f.timestamp.isoformat(), "fault_code": f.fault_code, "description": f.description}
                for f in db.query(FaultCode).order_by(FaultCode.timestamp.desc()).limit(1000)
            ]
            return JSONResponse(rows)


def _vehicle_partial(vehicle_id: int, from_date: str | None = None, to_date: str | None = None) -> str:
    if not vehicle_id:
        return empty_state("No vehicles are available yet.")
    since = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc) if from_date else datetime.now(timezone.utc) - timedelta(days=30)
    until = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc) + timedelta(days=1) if to_date else datetime.now(timezone.utc)
    with with_db() as db:
        detail = AnalyticsService(db).vehicle_detail(vehicle_id, since, until)
        trips = data_table(
            ["Date", "Miles", "Fuel"],
            [
                [row["start_time"][:10], str(row["distance_miles"]), str(row["fuel_used"])]
                for row in detail["trip_history"][:100]
            ],
            num_cols={1, 2},
        )
        return (f'<div class="grid charts">'
                + chart_container(line_chart(detail["daily_mileage"], "day", "miles", "Daily Mileage"), "Daily Mileage", dot="#38bdf8")
                + chart_container(histogram(detail["speed_distribution"], "Speed Distribution"), "Speed Distribution", dot="#f59e0b")
                + chart_container(
                    map_chart([{"vehicle": "selected", "latitude": p["lat"], "longitude": p["lon"], "status": "moving" if p["speed"] > 1 else "stopped"} for p in detail["gps_points"]], "Recent GPS Points"),
                    "Recent GPS Points", span_2=True, dot="#22c55e")
                + panel(trips, title="Trip History", span_2=True)
                + "</div>")
