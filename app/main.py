from __future__ import annotations

import inspect
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from fasthtml.common import fast_app
except ModuleNotFoundError:
    from starlette.applications import Starlette

    def fast_app() -> tuple[Starlette, Any]:
        fallback_app = Starlette()

        def route(path: str, methods: list[str] | None = None):
            def decorator(func: Any) -> Any:
                signature = inspect.signature(func)

                async def endpoint(request: Request) -> Any:
                    kwargs: dict[str, Any] = {}
                    for name, parameter in signature.parameters.items():
                        if name == "request":
                            kwargs[name] = request
                        elif name in request.query_params:
                            value: Any = request.query_params[name]
                            if parameter.annotation is int:
                                value = int(value)
                            kwargs[name] = value
                    result = func(**kwargs)
                    if inspect.isawaitable(result):
                        return await result
                    return result

                fallback_app.add_route(path, endpoint, methods=methods or ["GET"])
                return func

            return decorator

        return fallback_app, route
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from app.analytics.services import AnalyticsService
from app.auth.security import (
    AuthMiddleware,
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
from app.dashboards.charts import bar_chart, histogram, line_chart, map_chart
from app.database.session import SessionLocal
from app.jobs.scheduler import start_scheduler
from app.logging_config import configure_logging
from app.models import Driver, FaultCode, Trip, Vehicle

configure_logging()
settings = get_settings()

app, rt = fast_app()
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.is_production,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
_scheduler = start_scheduler()


def page(request: Request, title: str, body: str) -> HTMLResponse:
    token = csrf_token(request)
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
      <nav class="nav">
        <a href="/">Executive</a>
        <a href="/vehicles">Vehicles</a>
        <a href="/drivers">Drivers</a>
        <a href="/maintenance">Maintenance</a>
        <a href="/fleet-map">Fleet Map</a>
        <form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{token}"><button class="logout" type="submit">Sign out</button></form>
      </nav>
    </aside>
    <main class="main">{body}</main>
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


def metric(label: str, value: Any) -> str:
    return f'<section class="card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div></section>'


def with_db() -> SessionLocal:
    return SessionLocal()


@rt("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


@rt("/login")
async def login_get(request: Request) -> HTMLResponse | RedirectResponse:
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return login_page(request)


@rt("/login", methods=["POST"])
async def login_post(request: Request) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    if not await validate_csrf(request, form):
        return login_page(request, "Session validation failed.")
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    if not login_allowed(request, username):
        return login_page(request, "Invalid username or password.")
    if secrets.compare_digest(username, settings.admin_username) and verify_admin_password(password):
        record_login_success(request, username)
        establish_authenticated_session(request)
        return RedirectResponse("/", status_code=303)
    record_login_failure(request, username)
    return login_page(request, "Invalid username or password.")


@rt("/logout", methods=["POST"])
async def logout(request: Request) -> RedirectResponse:
    form = await request.form()
    if await validate_csrf(request, form):
        request.session.clear()
    return RedirectResponse("/login", status_code=303)


@rt("/")
def executive(request: Request) -> HTMLResponse:
    with with_db() as db:
        analytics = AnalyticsService(db)
        summary = analytics.fleet_summary()
        trends = analytics.daily_trends()
        utilization = analytics.vehicle_utilization()[:10]
        body = f"""
<div class="topbar"><h1>Executive Dashboard</h1><span class="htmx-indicator">Loading...</span></div>
<div class="grid cards">
{metric("Total Vehicles", summary.total_vehicles)}
{metric("Active Vehicles", summary.active_vehicles)}
{metric("Total Fleet Miles", f"{summary.total_fleet_miles:,.0f}")}
{metric("Fuel Usage", f"{summary.total_fuel_consumed:,.1f} gal")}
</div>
<div class="grid charts" style="margin-top:16px">
<section class="panel">{line_chart(trends, "day", "mileage", "Fleet Miles Trend")}</section>
<section class="panel">{line_chart(trends, "day", "fuel", "Fuel Usage Trend")}</section>
<section class="panel span-2">{bar_chart(utilization, "label", "utilization_percentage", "Vehicle Utilization Ranking")}</section>
</div>"""
        return page(request, "Executive Dashboard", body)


@rt("/vehicles")
def vehicles(request: Request) -> HTMLResponse:
    with with_db() as db:
        vehicles = db.query(Vehicle).order_by(Vehicle.license_plate.asc().nullslast()).all()
        selected = vehicles[0].id if vehicles else 0
        options = "".join(f'<option value="{v.id}">{v.license_plate or v.vin or v.geotab_id}</option>' for v in vehicles)
        body = f"""
<div class="topbar"><h1>Vehicle Dashboard</h1><span class="htmx-indicator">Loading...</span></div>
<form class="filters" hx-get="/partials/vehicle" hx-target="#vehicle-content" hx-indicator=".htmx-indicator">
<label>Vehicle<select name="vehicle_id">{options}</select></label>
<label>From<input name="from_date" type="date" value="{(datetime.now(timezone.utc)-timedelta(days=30)).date()}"></label>
<label>To<input name="to_date" type="date" value="{datetime.now(timezone.utc).date()}"></label>
<button type="submit">Apply</button>
</form>
<div id="vehicle-content">{vehicle_partial(selected)}</div>"""
        return page(request, "Vehicle Dashboard", body)


@rt("/partials/vehicle")
def vehicle_partial_route(vehicle_id: int, from_date: str, to_date: str) -> HTMLResponse:
    return HTMLResponse(vehicle_partial(vehicle_id, from_date, to_date))


def vehicle_partial(vehicle_id: int, from_date: str | None = None, to_date: str | None = None) -> str:
    if not vehicle_id:
        return '<section class="panel">No vehicles are available yet.</section>'
    since = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc) if from_date else datetime.now(timezone.utc) - timedelta(days=30)
    until = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc) + timedelta(days=1) if to_date else datetime.now(timezone.utc)
    with with_db() as db:
        detail = AnalyticsService(db).vehicle_detail(vehicle_id, since, until)
        trips = "".join(
            f"<tr><td>{row['start_time'][:10]}</td><td>{row['distance_miles']}</td><td>{row['fuel_used']}</td></tr>"
            for row in detail["trip_history"][:100]
        )
        return f"""
<div class="grid charts">
<section class="panel">{line_chart(detail["daily_mileage"], "day", "miles", "Daily Mileage")}</section>
<section class="panel">{histogram(detail["speed_distribution"], "Speed Distribution")}</section>
<section class="panel">{map_chart([{"vehicle": "selected", "latitude": p["lat"], "longitude": p["lon"], "status": "moving" if p["speed"] > 1 else "stopped"} for p in detail["gps_points"]], "Recent GPS Points")}</section>
<section class="panel"><h2>Trip History</h2><table><thead><tr><th>Date</th><th>Miles</th><th>Fuel</th></tr></thead><tbody>{trips}</tbody></table></section>
</div>"""


@rt("/drivers")
def drivers(request: Request) -> HTMLResponse:
    with with_db() as db:
        metrics = AnalyticsService(db).driver_metrics()
        rows = "".join(
            f"<tr><td>{row['name']}</td><td>{row['trip_count']}</td><td>{row['distance_driven']}</td><td>{row['average_trip_length']}</td></tr>"
            for row in metrics
        )
        body = f"""
<div class="topbar"><h1>Driver Dashboard</h1></div>
<div class="grid charts">
<section class="panel">{bar_chart(metrics[:15], "name", "distance_driven", "Distance Driven")}</section>
<section class="panel">{bar_chart(metrics[:15], "name", "trip_count", "Trips Completed")}</section>
<section class="panel span-2"><h2>Driver Performance</h2><table><thead><tr><th>Driver</th><th>Trips</th><th>Distance</th><th>Avg Trip</th></tr></thead><tbody>{rows}</tbody></table></section>
</div>"""
        return page(request, "Driver Dashboard", body)


@rt("/maintenance")
def maintenance(request: Request) -> HTMLResponse:
    with with_db() as db:
        metrics = AnalyticsService(db).maintenance_metrics()
        current = "".join(
            f"<tr><td>{row['vehicle']}</td><td>{row['timestamp'][:10]}</td><td>{row['fault_code']}</td><td>{row['description'] or ''}</td></tr>"
            for row in metrics["current_faults"]
        )
        body = f"""
<div class="topbar"><h1>Maintenance Dashboard</h1></div>
<div class="grid charts">
<section class="panel">{bar_chart(metrics["fault_frequency"][:15], "fault_code", "count", "Fault Frequency")}</section>
<section class="panel">{bar_chart(metrics["fault_frequency"][:15], "fault_code", "count", "Fault Types")}</section>
<section class="panel span-2"><h2>Current Faults</h2><table><thead><tr><th>Vehicle</th><th>Date</th><th>Code</th><th>Description</th></tr></thead><tbody>{current}</tbody></table></section>
</div>"""
        return page(request, "Maintenance Dashboard", body)


@rt("/fleet-map")
def fleet_map(request: Request) -> HTMLResponse:
    with with_db() as db:
        locations = AnalyticsService(db).latest_locations()
        body = f'<div class="topbar"><h1>Fleet Map</h1></div><section class="panel">{map_chart(locations, "Latest Vehicle Locations")}</section>'
        return page(request, "Fleet Map", body)


@rt("/api/fleet-summary")
def api_fleet_summary() -> JSONResponse:
    with with_db() as db:
        return JSONResponse(AnalyticsService(db).fleet_summary().model_dump())


@rt("/api/vehicles")
def api_vehicles() -> JSONResponse:
    with with_db() as db:
        rows = [{"id": v.id, "geotab_id": v.geotab_id, "vin": v.vin, "license_plate": v.license_plate, "make": v.make, "model": v.model, "year": v.year} for v in db.query(Vehicle).all()]
        return JSONResponse(rows)


@rt("/api/drivers")
def api_drivers() -> JSONResponse:
    with with_db() as db:
        rows = [{"id": d.id, "geotab_id": d.geotab_id, "name": d.name, "employee_id": d.employee_id} for d in db.query(Driver).all()]
        return JSONResponse(rows)


@rt("/api/trips")
def api_trips() -> JSONResponse:
    with with_db() as db:
        rows = [
            {"id": t.id, "vehicle_id": t.vehicle_id, "driver_id": t.driver_id, "start_time": t.start_time.isoformat(), "end_time": t.end_time.isoformat(), "distance_miles": t.distance_miles, "fuel_used": t.fuel_used}
            for t in db.query(Trip).order_by(Trip.start_time.desc()).limit(1000)
        ]
        return JSONResponse(rows)


@rt("/api/faults")
def api_faults() -> JSONResponse:
    with with_db() as db:
        rows = [{"id": f.id, "vehicle_id": f.vehicle_id, "timestamp": f.timestamp.isoformat(), "fault_code": f.fault_code, "description": f.description} for f in db.query(FaultCode).order_by(FaultCode.timestamp.desc()).limit(1000)]
        return JSONResponse(rows)


if __name__ == "__main__":
    try:
        from fasthtml.common import serve

        serve()
    except ModuleNotFoundError:
        import uvicorn

        uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
