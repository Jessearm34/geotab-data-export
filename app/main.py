from __future__ import annotations

import inspect
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from fasthtml.common import fast_app
    _fasthtml_available = True
except ModuleNotFoundError:
    from starlette.applications import Starlette
    _fasthtml_available = False

    def fast_app(**kwargs: Any) -> tuple[Starlette, Any]:
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
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
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
from app.dashboards.components import (
    chart_container,
    data_table,
    date_controls,
    empty_state,
    kpi_row,
    page_header,
    panel,
    resolve_date_range,
)
from app.dashboards.kpi import Kpi
from app.data_refining.comparison import compute_delta, prior_period
from app.database.session import SessionLocal
from app.jobs.scheduler import start_scheduler
from app.logging_config import configure_logging
from app.models import Driver, FaultCode, Trip, Vehicle

import logging
logger = logging.getLogger(__name__)

configure_logging()
settings = get_settings()

if _fasthtml_available:
    app, rt = fast_app(
        secret_key=settings.session_secret.get_secret_value(),
        max_age=settings.session_max_age_seconds,
        same_site="lax",
        sess_https_only=settings.is_production,
    )
    # FastHTML adds SessionMiddleware internally, but we need strict ASGI
    # ordering: Session must populate scope["session"] BEFORE AuthMiddleware
    # checks it. Remove the internal session middleware so we can re-add all
    # middleware below in the correct order.
    app.user_middleware = [m for m in app.user_middleware if m.cls is not SessionMiddleware]
else:
    app, rt = fast_app()

# Ordering: add_middleware prepends. Session is added second (middle of list),
# so in the ASGI chain it runs BEFORE Auth (which is innermost, closer to the
# handler). This ensures scope["session"] is populated when AuthMiddleware checks it.
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret.get_secret_value(),
    max_age=settings.session_max_age_seconds,
    same_site="lax",
    https_only=settings.is_production,
)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
# Move the static mount before any route-based static handler so it takes
# priority over fasthtml's built-in /{fname:path}.{ext:static} route.
routes = list(app.routes)
for i, r in enumerate(routes):
    if hasattr(r, 'name') and r.name == 'static':
        routes.insert(0, routes.pop(i))
        break
app.router.routes = routes

import logging
logger = logging.getLogger("app.startup")
_settings = get_settings()
if _settings.is_geotab_configured and _settings.scheduler_enabled:
    try:
        logger.info("startup_sync_all begin")
        with SessionLocal() as db:
            from app.services.sync_service import SyncService
            results = SyncService(db).sync_all()
        logger.info("startup_sync_all done results=%s", results)
    except Exception:
        logger.exception("startup_sync_all failed — scheduler will retry on interval")

_scheduler = start_scheduler()


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
        f'<a href="{path}"{" class=active" if path == active_nav else ""}>{label}</a>'
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

def _safety_kpis(speed: dict, idling: dict, efficiency: list, emissions: dict, faults: dict, prior_speed: dict | None = None, prior_idling: dict | None = None, prior_efficiency: list | None = None, prior_emissions: dict | None = None, prior_faults: dict | None = None) -> list[Kpi]:
    top_mpg = efficiency[0]["mpg"] if efficiency else None
    prior_top_mpg = prior_efficiency[0]["mpg"] if prior_efficiency else None
    return [
        Kpi(key="avg_speed", label="Avg Speed", value=speed["avg_speed"], unit="mph",
            delta=compute_delta(speed["avg_speed"], prior_speed["avg_speed"] if prior_speed else None)),
        Kpi(key="max_speed", label="Max Speed", value=speed["max_speed"], unit="mph",
            delta=compute_delta(speed["max_speed"], prior_speed["max_speed"] if prior_speed else None), delta_good_when_up=False),
        Kpi(key="speeding_pct", label="Speeding %", value=speed["speeding_pct"], unit="%",
            delta=compute_delta(speed["speeding_pct"], prior_speed["speeding_pct"] if prior_speed else None), delta_good_when_up=False),
        Kpi(key="idle_hours", label="Idle Time", value=idling["total_idle_hours"], unit="hours",
            delta=compute_delta(idling["total_idle_hours"], prior_idling["total_idle_hours"] if prior_idling else None), delta_good_when_up=False),
        Kpi(key="fleet_mpg", label="Fleet MPG", value=top_mpg, unit="mpg",
            delta=compute_delta(top_mpg, prior_top_mpg)),
        Kpi(key="co2", label="CO₂ Emissions", value=emissions["co2_tons"], unit="tons",
            delta=compute_delta(emissions["co2_tons"], prior_emissions["co2_tons"] if prior_emissions else None), delta_good_when_up=False),
        Kpi(key="fuel_gal", label="Fuel Used", value=emissions["total_fuel_gal"], unit="gal",
            delta=compute_delta(emissions["total_fuel_gal"], prior_emissions["total_fuel_gal"] if prior_emissions else None), delta_good_when_up=False),
        Kpi(key="safety_events", label="Fault Events", value=faults["open_fault_counts"],
            delta=compute_delta(faults["open_fault_counts"], prior_faults["open_fault_counts"] if prior_faults else None), delta_good_when_up=False),
    ]


def _exec_kpis(summary: Any, idling: dict, speed: dict, emissions: dict, prior_summary: Any = None, prior_idling: dict | None = None, prior_speed: dict | None = None, prior_emissions: dict | None = None) -> list[Kpi]:
    def delta(curr: float | int | None, prior: float | int | None) -> float | None:
        return compute_delta(curr, prior)

    return [
        Kpi(key="total_vehicles", label="Total Vehicles", value=summary.total_vehicles,
            delta=delta(summary.total_vehicles, prior_summary.total_vehicles if prior_summary else None)),
        Kpi(key="active_vehicles", label="Active Vehicles", value=summary.active_vehicles,
            delta=delta(summary.active_vehicles, prior_summary.active_vehicles if prior_summary else None)),
        Kpi(key="fleet_miles", label="Fleet Miles", value=summary.total_fleet_miles, unit="miles",
            delta=delta(summary.total_fleet_miles, prior_summary.total_fleet_miles if prior_summary else None)),
        Kpi(key="avg_mpg", label="Avg MPG", value=summary.average_mpg, unit="mpg",
            delta=delta(summary.average_mpg, prior_summary.average_mpg if prior_summary else None)),
        Kpi(key="idle_pct", label="Idle %", value=idling["idle_pct"], unit="%",
            delta=delta(idling["idle_pct"], prior_idling["idle_pct"] if prior_idling else None), delta_good_when_up=False),
        Kpi(key="speeding", label="Speeding Incidents", value=speed["speeding_count"],
            delta=delta(speed["speeding_count"], prior_speed["speeding_count"] if prior_speed else None), delta_good_when_up=False),
        Kpi(key="co2", label="CO₂ Emissions", value=emissions["co2_tons"], unit="tons",
            delta=delta(emissions["co2_tons"], prior_emissions["co2_tons"] if prior_emissions else None), delta_good_when_up=False),
        Kpi(key="fuel", label="Fuel Used", value=summary.total_fuel_consumed, unit="gal",
            delta=delta(summary.total_fuel_consumed, prior_summary.total_fuel_consumed if prior_summary else None), delta_good_when_up=False),
    ]


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


@rt("/api/diagnostics")
def diagnostics() -> JSONResponse:
    """Diagnostic endpoint: env var presence, DB state, sync status.

    This endpoint is intentionally NOT auth-guarded so Railway health
    monitors and operators can check it without a session. It exposes no
    secrets — only present/absent status for sensitive fields.
    """
    from app.config import get_settings, missing_geotab_credentials
    from app.models import Driver, FaultCode, FuelEvent, GPSLog, SyncLog, SyncMetadata, Trip, Vehicle
    from app.database.session import SessionLocal

    s = get_settings()
    env_checks = {
        "environment": s.environment,
        "GEOTAB_DATABASE": "present" if s.geotab_database else "missing",
        "GEOTAB_USERNAME": "present" if s.geotab_username else "missing",
        "GEOTAB_PASSWORD": "present" if s.geotab_password else "missing",
        "GEOTAB_SERVER": s.geotab_server,
        "DATABASE_URL_type": "postgresql" if "postgres" in (s.database_url or "") else "sqlite",
        "DATABASE_URL_target": s.database_url.split("@")[-1].split("?")[0] if "@" in (s.database_url or "") else "local_file",
        "scheduler_enabled": s.scheduler_enabled,
        "is_geotab_configured": s.is_geotab_configured,
        "missing_credentials": missing_geotab_credentials(s),
    }

    db_info = {"status": "error", "detail": ""}
    row_counts: dict[str, int] = {}
    sync_meta: dict[str, str | None] = {}
    last_sync_logs: list[dict] = []
    try:
        with SessionLocal() as db:
            for label, model in [
                ("vehicles", Vehicle),
                ("drivers", Driver),
                ("trips", Trip),
                ("gps_logs", GPSLog),
                ("fault_codes", FaultCode),
                ("fuel_events", FuelEvent),
            ]:
                row_counts[label] = db.query(model).count()
            sync_rows = db.query(SyncMetadata).all()
            sync_meta = {m.entity_name: m.last_sync_timestamp.isoformat() if m.last_sync_timestamp else None for m in sync_rows}
            log_rows = (
                db.query(SyncLog).order_by(SyncLog.started_at.desc()).limit(20).all()
            )
            last_sync_logs = [
                {
                    "entity": log.entity_name,
                    "started_at": log.started_at.isoformat(),
                    "finished_at": log.finished_at.isoformat() if log.finished_at else None,
                    "status": log.status,
                    "records": log.records_processed,
                }
                for log in log_rows
            ]
            db_info = {"status": "ok"}
    except Exception as exc:
        db_info = {"status": "error", "detail": str(exc)}

    return JSONResponse({
        "env": env_checks,
        "db": db_info,
        "row_counts": row_counts,
        "sync_watermarks": sync_meta,
        "recent_sync_logs": last_sync_logs,
    })


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

        # Compute prior-period deltas
        prior_since, prior_until = prior_period(since, until)
        prior_summary = analytics.fleet_summary(prior_since, prior_until)
        prior_idling = analytics.idling_summary(prior_since, prior_until)
        prior_speed = analytics.speed_analysis(prior_since, prior_until)
        prior_emissions = analytics.emissions_estimate(prior_since, prior_until)

        # Empty state: no trips in range
        has_trips = bool(trends)
        if not has_trips:
            body = (page_header("Executive Dashboard", refreshed=datetime.now(timezone.utc))
                    + date_controls(rng, hx_target="#main-content")
                    + kpi_row(_exec_kpis(summary, idling, speed, emissions, prior_summary, prior_idling, prior_speed, prior_emissions))
                    + empty_state("No trips in selected date range. Try adjusting the date filter to include periods with fleet activity."))
            return page(request, "Executive Dashboard", body, active_nav="/")

        # 2-row grid: KPI cards top, 2 charts + table bottom
        from app.dashboards.charts import COLORS
        utilization_table = data_table(
            ["Vehicle", "Miles", "Hours", "Utilization %"],
            [[r["label"], str(r["total_miles"]), str(r["hours_driven"]), f'{r["utilization_percentage"]}%'] for r in utilization],
            num_cols={1, 2, 3},
        )
        body = (page_header("Executive Dashboard", refreshed=datetime.now(timezone.utc))
                + date_controls(rng, hx_target="#main-content")
                + kpi_row(_exec_kpis(summary, idling, speed, emissions, prior_summary, prior_idling, prior_speed, prior_emissions))
                + '<div class="grid charts">'
                + "".join(filter(None, [
                    chart_container(line_chart(trends, "day", "mileage", "Fleet Miles Trend", color=COLORS["primary"]), "Fleet Miles Trend", dot=COLORS["primary"]),
                    chart_container(line_chart(trends, "day", "fuel", "Fuel Usage Trend", color=COLORS["success"]), "Fuel Usage Trend", dot=COLORS["success"]),
                    chart_container(bar_chart(utilization, "label", "utilization_percentage", "Vehicle Utilization Ranking", color=COLORS["warning"]), "Vehicle Utilization Ranking", span_2=True, dot=COLORS["warning"]),
                ]))
                + "</div>"
                + '<div class="grid charts mt">'
                + panel(utilization_table, title="Vehicle Utilization Details", span_2=True)
                + "</div>")
        return page(request, "Executive Dashboard", body, active_nav="/")


# ── Vehicles ────────────────────────────────────── #


@rt("/vehicles")
def vehicles(request: Request) -> HTMLResponse:
    with with_db() as db:
        vehicle_list = db.query(Vehicle).order_by(Vehicle.license_plate.asc().nullslast()).all()
        logger.info("dashboard vehicle_list count=%s", len(vehicle_list))
        if not vehicle_list:
            body = (page_header("Vehicle Dashboard")
                    + empty_state(
                        "No vehicles are available yet. "
                        "Vehicle data must be synced from Geotab before this dashboard can display vehicle details. "
                        "This happens automatically when Geotab credentials are configured and the scheduler runs."
                    ))
            return page(request, "Vehicle Dashboard", body, active_nav="/vehicles")
        selected = vehicle_list[0].id if vehicle_list else 0
        options = "".join(f'<option value="{v.id}">{v.license_plate or v.vin or v.geotab_id}</option>' for v in vehicle_list)
        # Build vehicle info card
        v = vehicle_list[0]
        vehicle_info = f"""<div class="vehicle-info">
          <h3>{v.license_plate or v.vin or v.geotab_id}</h3>
          <p>Make: {v.make or 'Unknown'} · Model: {v.model or 'Unknown'} · Year: {v.year or 'Unknown'}</p>
          <p>VIN: {v.vin or 'N/A'}</p>
        </div>"""
        body = (page_header("Vehicle Dashboard")
                + vehicle_info
                + f"""
<form class="filters" hx-get="/partials/vehicle" hx-target="#vehicle-content" hx-indicator=".htmx-indicator">
<label>Vehicle<select name="vehicle_id">{options}</select></label>
<label>From<input name="from_date" type="date" value="{(datetime.now(timezone.utc)-timedelta(days=30)).date()}"></label>
<label>To<input name="to_date" type="date" value="{datetime.now(timezone.utc).date()}"></label>
<button type="submit">Apply</button>
</form>
<div id="vehicle-content">{vehicle_partial(selected)}</div>""")
        return page(request, "Vehicle Dashboard", body, active_nav="/vehicles")


@rt("/partials/vehicle")
def vehicle_partial_route(vehicle_id: int, from_date: str, to_date: str) -> HTMLResponse:
    return HTMLResponse(vehicle_partial(vehicle_id, from_date, to_date))


def vehicle_partial(vehicle_id: int, from_date: str | None = None, to_date: str | None = None) -> str:
    if not vehicle_id:
        return empty_state("No vehicles are available yet.")
    since = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc) if from_date else datetime.now(timezone.utc) - timedelta(days=30)
    until = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc) + timedelta(days=1) if to_date else datetime.now(timezone.utc)
    with with_db() as db:
        from app.dashboards.charts import COLORS
        detail = AnalyticsService(db).vehicle_detail(vehicle_id, since, until)
        trips_table = data_table(
            ["Date", "Miles", "Fuel (est)"],
            [
                [row["start_time"][:10], str(row["distance_miles"]), str(row["fuel_used"])]
                for row in detail["trip_history"][:20]
            ],
            num_cols={1, 2},
        )
        # 2x2 grid: daily mileage, speed histogram, map, trip history
        return (f'<div class="grid charts">'
                + "".join(filter(None, [
                    chart_container(line_chart(detail["daily_mileage"], "day", "miles", "Daily Mileage", color=COLORS["primary"]), "Daily Mileage", dot=COLORS["primary"]),
                    chart_container(histogram(detail["speed_distribution"], "Speed Distribution", color=COLORS["warning"]), "Speed Distribution", dot=COLORS["warning"]),
                    chart_container(
                        map_chart([{"vehicle": "Selected", "latitude": p["lat"], "longitude": p["lon"], "status": "moving" if p["speed"] > 1 else "stopped"} for p in detail["gps_points"]], "Recent GPS Points"),
                        "Recent GPS Points", dot=COLORS["success"]),
                    panel(trips_table or empty_state("No trips in selected date range."), title="Trip History"),
                ]))
                + "</div>")


# ── Drivers ─────────────────────────────────────── #


@rt("/drivers")
def drivers(request: Request, range: str | None = None, start: str | None = None, end: str | None = None) -> HTMLResponse:
    since, until, rng = resolve_date_range(range, start, end)
    with with_db() as db:
        analytics = AnalyticsService(db)
        metrics = analytics.driver_metrics(since, until)
        logger.info("dashboard driver_metrics count=%s range=%s", len(metrics), rng)
        has_activity = any(m.get("trip_count", 0) > 0 for m in metrics)
        if not has_activity:
            body = (page_header("Driver Dashboard", refreshed=datetime.now(timezone.utc))
                    + date_controls(rng, hx_target="#main-content")
                    + empty_state(
                        "No trips in selected date range. "
                        "Try adjusting the date filter to include periods with driver activity.",
                    ))
            return page(request, "Driver Dashboard", body, active_nav="/drivers")
        # KPI summary row
        total_drivers = len(metrics)
        active_drivers = sum(1 for m in metrics if m["trip_count"] > 0)
        total_distance = sum(m["distance_driven"] for m in metrics)
        total_trips = sum(m["trip_count"] for m in metrics)
        driver_kpis = [
            Kpi(key="total_vehicles", label="Total Drivers", value=total_drivers),
            Kpi(key="active_vehicles", label="Active Drivers", value=active_drivers),
            Kpi(key="fleet_miles", label="Total Distance", value=total_distance, unit="miles"),
            Kpi(key="avg_mpg", label="Total Trips", value=total_trips),
        ]
        from app.dashboards.charts import COLORS
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
                + kpi_row(driver_kpis)
                + '<div class="grid charts">'
                + "".join(filter(None, [
                    chart_container(bar_chart(metrics[:15], "name", "distance_driven", "Distance Driven", color=COLORS["primary"]), "Distance Driven", dot=COLORS["primary"]),
                    chart_container(bar_chart(metrics[:15], "name", "trip_count", "Trips Completed", color=COLORS["success"]), "Trips Completed", dot=COLORS["success"]),
                    panel(rows, title="Driver Performance", span_2=True),
                ]))
                + "</div>")
        return page(request, "Driver Dashboard", body, active_nav="/drivers")


# ── Maintenance ─────────────────────────────────── #


@rt("/maintenance")
def maintenance(request: Request, range: str | None = None, start: str | None = None, end: str | None = None) -> HTMLResponse:
    since, until, rng = resolve_date_range(range, start, end)
    with with_db() as db:
        metrics = AnalyticsService(db).maintenance_metrics(since, until)
        fault_count = metrics["open_fault_counts"]
        logger.info("dashboard maintenance_metrics faults=%s range=%s", fault_count, rng)
        if not fault_count and not metrics["current_faults"]:
            body = (page_header("Maintenance Dashboard", refreshed=datetime.now(timezone.utc))
                    + date_controls(rng, hx_target="#main-content")
                    + empty_state(
                        "No diagnostic faults in selected date range. "
                        "Vehicle health data is synced from Geotab when credentials are configured.",
                    ))
            return page(request, "Maintenance Dashboard", body, active_nav="/maintenance")
        from app.dashboards.charts import COLORS
        current = data_table(
            ["Vehicle", "Date", "Code", "Description"],
            [
                [row["vehicle"], row["timestamp"][:10], row["fault_code"], row["description"] or ""]
                for row in metrics["current_faults"]
            ],
        )
        fault_freq_table = data_table(
            ["Fault Code", "Count"],
            [[row["fault_code"], str(row["count"])] for row in metrics["fault_frequency"][:20]],
            num_cols={1},
        )
        body = (page_header("Maintenance Dashboard", refreshed=datetime.now(timezone.utc))
                + date_controls(rng, hx_target="#main-content")
                + '<div class="grid charts">'
                + "".join(filter(None, [
                    chart_container(bar_chart(metrics["fault_frequency"][:15], "fault_code", "count", "Fault Frequency", color=COLORS["danger"]), "Fault Frequency", dot=COLORS["danger"]),
                    panel(fault_freq_table or empty_state("No fault data."), title="Fault Code Breakdown"),
                    panel(current, title="Current Faults", span_2=True),
                ]))
                + "</div>")
        return page(request, "Maintenance Dashboard", body, active_nav="/maintenance")


# ── Fleet Map ───────────────────────────────────── #


@rt("/fleet-map")
def fleet_map(request: Request) -> HTMLResponse:
    with with_db() as db:
        locations = AnalyticsService(db).latest_locations(max_age_days=365)
        logger.info("dashboard fleet_map locations=%s", len(locations))
        if not locations:
            body = (page_header("Fleet Map")
                    + empty_state(
                        "No vehicle location data available. "
                        "GPS log data must be synced from Geotab before vehicle positions can appear on the map. "
                        "This happens automatically when Geotab credentials are configured and the scheduler runs.",
                    ))
            return page(request, "Fleet Map", body, active_nav="/fleet-map")
        from app.dashboards.charts import COLORS
        # Build vehicle list sidebar
        vehicle_items = "".join(
            f'<div class="fleet-list-item"><span class="status-dot {loc["status"]}"></span><span class="vehicle-label">{loc["vehicle"]}</span><span class="vehicle-speed">{loc["speed"]:.0f} mph</span></div>'
            for loc in locations
        )
        map_panel = panel(map_chart(locations, "Live Fleet Positions"), title="Live Fleet Positions", dot=COLORS["primary"])
        list_panel = panel(
            f'<div class="fleet-list">{vehicle_items}</div>',
            title=f"Vehicles ({len(locations)})",
            dot=COLORS["success"]
        )
        body = (page_header("Fleet Map")
                + f'<div class="fleet-map-grid"><div class="map-panel">{map_panel}</div>{list_panel}</div>')
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

        # Compute prior-period deltas
        prior_since, prior_until = prior_period(since, until)
        prior_speed = analytics.speed_analysis(prior_since, prior_until)
        prior_efficiency = analytics.fuel_efficiency(prior_since, prior_until)
        prior_idling = analytics.idling_summary(prior_since, prior_until)
        prior_emissions = analytics.emissions_estimate(prior_since, prior_until)
        prior_faults = analytics.maintenance_metrics(prior_since, prior_until)

        kpis = _safety_kpis(speed, idling, efficiency, emissions, faults, prior_speed, prior_idling, prior_efficiency, prior_emissions, prior_faults)
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
        from app.dashboards.charts import COLORS
        body = (page_header("Safety & Sustainability", refreshed=datetime.now(timezone.utc))
                + date_controls(rng, hx_target="#main-content")
                + kpi_row(kpis)
                + '<div class="grid charts">'
                + "".join(filter(None, [
                    chart_container(histogram(speed["speed_distribution"], "Speed Distribution", color=COLORS["warning"]), "Speed Distribution", dot=COLORS["warning"]),
                    chart_container(bar_chart(efficiency, "label", "mpg", "Fuel Economy (MPG)", color=COLORS["success"]), "Fuel Economy (MPG)", dot=COLORS["success"]),
                    chart_container(bar_chart(idling["vehicles"], "label", "idle_pct", "Idle Time % by Vehicle", color=COLORS["danger"]), "Idle Time % by Vehicle", dot=COLORS["danger"]),
                ]))
                + "</div>"
                + '<div class="grid charts mt">'
                + panel(driver_rows, title="Driver Safety Rankings")
                + panel(fault_rows, title="Safety Exceptions (Top Fault Codes)")
                + "</div>")
        return page(request, "Safety & Sustainability", body, active_nav="/safety")


# ── API Endpoints ───────────────────────────────── #


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
