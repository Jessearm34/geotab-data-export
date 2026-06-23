"""Geotab Fleet Analytics Dashboard
Mirrors the EWS SiteDocs design: navy sidebar, light theme, HTMX, Plotly.
Only shows metrics with real data from the Geotab API.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
import os
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from os import getenv
from pathlib import Path
from urllib.parse import parse_qs, urlencode

import pandas as pd
import plotly.graph_objects as go
from fasthtml.common import *
from dotenv import load_dotenv

load_dotenv()

from database import SessionLocal
from analytics import AnalyticsService

# ── App ────────────────────────────────────────────────────────────────────

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.2.min.js"

STYLE = Style("""
:root {
  --navy: #0a1f33; --navy-2: #0d2840; --page: #eef2f7; --card: #ffffff;
  --ink: #0f172a; --muted: #64748b; --line: #e2e8f0; --accent: #2563eb;
  --good: #16a34a; --bad: #dc2626; --warn: #ea580c;
}
* { box-sizing: border-box; }
body { margin: 0; font-family: Inter, system-ui, -apple-system, sans-serif;
       background: var(--page); color: var(--ink); }
.layout { display: flex; min-height: 100vh; }
.sidebar { width: 232px; flex: 0 0 232px; background: var(--navy); color: #e8eef5;
           display: flex; flex-direction: column; padding: 22px 14px; }
.brand { display: flex; align-items: center; gap: 10px; padding: 6px 8px 20px; }
.brand .mark { font-size: 22px; }
.brand .name { font-weight: 800; font-size: 14px; line-height: 1.15; letter-spacing: .04em; }
.brand .name small { display:block; font-weight:600; font-size:10px; color:#7e93a8; letter-spacing:.14em; }
.nav { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.nav a { display: flex; align-items: center; gap: 11px; padding: 10px 12px; border-radius: 10px;
         color: #b8c6d6; text-decoration: none; font-size: 14px; font-weight: 500; cursor: pointer; }
.nav a:hover { background: var(--navy-2); color: #fff; }
.nav a.active { background: var(--accent); color: #fff; }
.sidebar .foot { margin-top: auto; font-size: 11px; color: #64788f; padding: 8px; }
.main { flex: 1; min-width: 0; padding: 22px 26px 40px; }
.header { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 12px; }
.header h1 { margin: 0; font-size: 26px; font-weight: 800; }
.header .refreshed { text-align: right; color: var(--muted); font-size: 12px; }
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(176px, 1fr)); gap: 14px; margin-bottom: 20px; }
.kpi { background: var(--card); border:1px solid var(--line); border-radius: 16px; padding: 16px 18px; }
.kpi .k-label { color: var(--muted); font-size: 13px; font-weight: 600; }
.kpi .k-value { font-size: 28px; font-weight: 800; margin: 6px 0 4px; }
.kpi .k-hint { color:#94a3b8; font-size: 11px; margin-top:6px; }
.grid { display: grid; gap: 16px; }
.grid.two { grid-template-columns: 1fr 1fr; }
.panel { background: var(--card); border:1px solid var(--line); border-radius: 16px; padding: 16px 18px; min-width: 0; }
.panel h3 { margin: 0 0 12px; font-size: 14px; font-weight: 700; display:flex; align-items:center; gap:8px; }
.panel h3 .dot { width:9px; height:9px; border-radius: 3px; display:inline-block; }
.chart-empty { display:flex; align-items:center; justify-content:center; height: 280px; color: var(--muted);
               border: 1px dashed var(--line); border-radius: 12px; font-size: 13px; }
.mt { margin-top: 16px; }
.tbl-wrap { overflow-x: auto; max-height: 340px; overflow-y: auto; }
table.data { width: 100%; border-collapse: collapse; font-size: 13px; }
table.data th { text-align: left; color: var(--muted); font-weight: 600; padding: 8px 10px;
                border-bottom: 2px solid var(--line); white-space: nowrap; position: sticky; top: 0; background: #fff; }
table.data td { padding: 8px 10px; border-bottom: 1px solid #f1f5f9; white-space: nowrap; }
table.data td.num { text-align: right; }
.badge { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; background:#e2e8f0; color:#475569; }
.badge.green { background:#dcfce7; color:#15803d; }
.badge.red { background:#fee2e2; color:#b91c1c; }
.badge.warn { background:#fef3c7; color:#92400e; }
.note { color: var(--muted); font-size: 12px; }
.htmx-indicator { opacity: 0; transition: opacity .2s; font-size: 12px; color: var(--accent); }
.htmx-request .htmx-indicator { opacity: 1; }
""")

app, rt = fast_app(
    pico=False,
    hdrs=(
        Meta(name="viewport", content="width=device-width, initial-scale=1"),
        Link(rel="preconnect", href="https://fonts.googleapis.com"),
        Link(rel="stylesheet", href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap"),
        Script(src=PLOTLY_CDN),
        STYLE,
    ),
    secret_key=os.getenv("SESSION_SECRET", "change-this-secret"),
)

# ── Auth ───────────────────────────────────────────────────────────────────

AUTH_PASSWORD = os.getenv("ADMIN_PASSWORD")
AUTH_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "").strip()
AUTH_DOMAIN = os.getenv("DASHBOARD_LOGIN_DOMAIN", "").strip().lower()

def verify_password(password: str) -> bool:
    """Verify against pbkdf2_sha256$salt$digest format (matching old auth.security)."""
    if not AUTH_PASSWORD_HASH and not AUTH_PASSWORD:
        return False
    if AUTH_PASSWORD_HASH:
        if not AUTH_PASSWORD_HASH.startswith("pbkdf2_sha256$"):
            return False
        try:
            _, salt, digest = AUTH_PASSWORD_HASH.split("$", 2)
        except ValueError:
            return False
        import hashlib
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 250_000).hex()
        return compare_digest(candidate, digest)
    return compare_digest(password, AUTH_PASSWORD or "")

def email_allowed(email: str) -> bool:
    if not AUTH_DOMAIN or not email:
        return True
    return email.strip().lower().endswith(f"@{AUTH_DOMAIN}")

def user_authenticated(req) -> bool:
    try: return bool(req.session.get("user"))
    except AssertionError: return False

def require_login(req):
    if user_authenticated(req): return None
    n = str(req.url.path)
    if req.url.query: n += f"?{req.url.query}"
    return Redirect(f"/login?{urlencode({'next': n})}")

# ── Sections ───────────────────────────────────────────────────────────────

SECTIONS = [
    ("fleet", "Fleet Overview", "📊"),
    ("vehicles", "Vehicles", "🚛"),
    ("maintenance", "Maintenance", "🔧"),
    ("map", "Fleet Map", "🗺️"),
]

# Drivers section shown only if driver data exists
def has_drivers(data: dict) -> bool:
    return len(data.get("drivers", [])) > 0 and any(d.get("distance_driven", 0) > 0 for d in data["drivers"])

def active_sections(data: dict) -> list:
    sections = [("fleet", "Fleet Overview", "📊"),
                ("vehicles", "Vehicles", "🚛")]
    if has_drivers(data):
        sections.append(("drivers", "Drivers", "👤"))
    sections.extend([("maintenance", "Maintenance", "🔧"),
                     ("map", "Fleet Map", "🗺️")])
    return sections
SWAP = dict(hx_target="#app", hx_swap="outerHTML", hx_indicator="#loading")
ACCENT = "#2563eb"
_ids = itertools.count()
_PLOT_CONFIG = {"displayModeBar": False, "responsive": True}

def _rgba(h: str, a: float) -> str:
    h = h.lstrip("#")
    return f"rgba({int(h[0:2],16)},{int(h[2:4],16)},{int(h[4:6],16)},{a})"

def _layout(fig, h=300):
    fig.update_layout(template="plotly_white", height=h,
        margin=dict(l=20, r=10, t=30, b=10),
        font=dict(family="Inter, system-ui, sans-serif", size=12, color="#0f172a"),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        showlegend=fig.layout.showlegend, title=None)
    return fig

def render(fig): 
    return fig.to_html(include_plotlyjs=False, full_html=False,
        config=_PLOT_CONFIG, div_id=f"plot-{next(_ids)}", default_width="100%")

def empty(msg="No data for this period"): 
    return f"<div class='chart-empty'>{msg}</div>"

def panel(title, body, dot="#2563eb", scroll=False):
    cls = "panel panel-scroll" if scroll else "panel"
    return Div(H3(Span(cls="dot", style=f"background:{dot}"), title),
               NotStr(body) if isinstance(body, str) else body, cls=cls)

def url(state, **over):
    return "/view?" + urlencode({**state, **over})

# ── Data ───────────────────────────────────────────────────────────────────

def load_data():
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=365)
    until = now
    db = SessionLocal()
    try:
        a = AnalyticsService(db)
        return {
            "summary": a.fleet_summary(since, until),
            "trends": a.daily_trends(since, until),
            "utilization": a.vehicle_utilization(since, until),
            "idling": a.idling_summary(since, until),
            "speed": a.speed_analysis(since, until),
            "drivers": a.driver_metrics(since, until),
            "faults": a.maintenance_metrics(since, until),
            "locations": a.latest_locations(max_age_days=365),
        }
    finally:
        db.close()

# ── KPI Cards ──────────────────────────────────────────────────────────────

def kpi_card(label, value, hint="", unit=""):
    if value is None: val = "—"
    elif isinstance(value, float):
        if unit == "%": val = f"{value:.1f}%"
        elif value == int(value): val = f"{int(value):,}"
        else: val = f"{value:,.1f}"
    else: val = f"{value:,}"
    return Div(Div(label, cls="k-label"), Div(val, cls="k-value"),
               Div(hint, cls="k-hint") if hint else "", cls="kpi")

def kpi_row(data):
    s = data["summary"]
    spd = data["speed"]
    idl = data["idling"]
    return Div(
        kpi_card("Active Vehicles", s.active_vehicles, f"of {s.total_vehicles} total"),
        kpi_card("Fleet Miles", s.total_fleet_miles, "Total distance"),
        kpi_card("Idle Time", idl.get("idle_pct", 0), f"{idl.get('total_idle_hours', 0):.0f} hours", "%"),
        kpi_card("Speeding Events", spd.get("speeding_count", 0), f"{spd.get('speeding_pct', 0):.1f}% of GPS"),
        cls="kpis",
    )

# ── Charts ─────────────────────────────────────────────────────────────────

def mileage_trend(trends):
    if not trends: return empty("No trip data")
    df = pd.DataFrame(trends)
    if df.empty or df["mileage"].sum() == 0: return empty("No mileage data")
    fig = go.Figure(go.Scatter(
        x=pd.to_datetime(df["day"]), y=df["mileage"],
        mode="lines+markers", line=dict(color=ACCENT, width=3, shape="spline"),
        marker=dict(size=6, color=ACCENT), fill="tozeroy",
        fillcolor=_rgba(ACCENT, 0.10),
        hovertemplate="%{x|%b %d}<br>%{y:,.0f} miles<extra></extra>"))
    fig.update_layout(showlegend=False)
    fig.update_yaxes(gridcolor="#e2e8f0")
    fig.update_xaxes(gridcolor="#f1f5f9", tickformat="%b %d", tickfont=dict(size=10))
    return render(_layout(fig, 300))

def utilization_chart(util):
    top = [u for u in util[:10] if u["total_miles"] > 0]
    if not top: return empty("No vehicles with miles")
    fig = go.Figure(go.Bar(
        x=[u["total_miles"] for u in top], y=[u["label"] for u in top],
        orientation="h", marker=dict(color=ACCENT),
        hovertemplate="%{y}<br>%{x:,.0f} miles<extra></extra>"))
    fig.update_layout(showlegend=False)
    fig.update_xaxes(gridcolor="#e2e8f0", tickfont=dict(size=10))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    return render(_layout(fig, max(260, 28 * len(top))))

def speed_dist(spd):
    samples = spd.get("speed_distribution", [])
    if not samples: return empty("No GPS speed data")
    fig = go.Figure(go.Histogram(
        x=samples, marker=dict(color="#ea580c"),
        hovertemplate="%{x:.0f} mph<br>%{y} records<extra></extra>"))
    fig.update_layout(showlegend=False)
    fig.update_yaxes(gridcolor="#e2e8f0")
    fig.update_xaxes(title="Speed (mph)", gridcolor="#f1f5f9", tickfont=dict(size=10))
    return render(_layout(fig, 280))

def idle_chart(idl):
    vehicles = idl.get("vehicles", [])
    active = [v for v in vehicles if v["idle_pct"] > 0][:10]
    if not active: return empty("No idle data")
    fig = go.Figure(go.Bar(
        x=[v["idle_pct"] for v in active], y=[v["label"] for v in active],
        orientation="h", marker=dict(color="#ea580c"),
        hovertemplate="%{y}<br>%{x:.1f}% idle<extra></extra>"))
    fig.update_layout(showlegend=False)
    fig.update_xaxes(gridcolor="#e2e8f0", tickformat=".0f", ticksuffix="%", tickfont=dict(size=10))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    return render(_layout(fig, max(260, 28 * len(active))))

def fault_chart(faults):
    freq = faults.get("fault_frequency", [])
    if not freq: return empty("No fault codes")
    top = freq[:10]
    fig = go.Figure(go.Bar(
        x=[f["count"] for f in top], y=[f["fault_code"] for f in top],
        orientation="h", marker=dict(color="#dc2626"),
        hovertemplate="%{y}<br>%{x} events<extra></extra>"))
    fig.update_layout(showlegend=False)
    fig.update_xaxes(gridcolor="#e2e8f0", tickfont=dict(size=10))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    return render(_layout(fig, max(260, 28 * len(top))))

def driver_chart(drivers):
    active = [d for d in drivers if d["distance_driven"] > 0][:10]
    if not active: return empty("No driver trip data")
    fig = go.Figure(go.Bar(
        x=[d["distance_driven"] for d in active], y=[d["name"] for d in active],
        orientation="h", marker=dict(color="#0e7490"),
        hovertemplate="%{y}<br>%{x:,.0f} miles<extra></extra>"))
    fig.update_layout(showlegend=False)
    fig.update_xaxes(gridcolor="#e2e8f0", tickfont=dict(size=10))
    fig.update_yaxes(autorange="reversed", tickfont=dict(size=10))
    return render(_layout(fig, max(260, 28 * len(active))))

def fleet_map_chart(locs):
    if not locs: return empty("No GPS location data")
    moving = [l for l in locs if l["speed"] > 1]
    stopped = [l for l in locs if l["speed"] <= 1]
    fig = go.Figure()
    if moving:
        fig.add_trace(go.Scattergeo(
            lat=[l["latitude"] for l in moving], lon=[l["longitude"] for l in moving],
            mode="markers", marker=dict(size=8, color="#16a34a"),
            text=[l["vehicle"] for l in moving], name="Moving",
            hovertemplate="%{text}<extra></extra>"))
    if stopped:
        fig.add_trace(go.Scattergeo(
            lat=[l["latitude"] for l in stopped], lon=[l["longitude"] for l in stopped],
            mode="markers", marker=dict(size=6, color="#94a3b8"),
            text=[l["vehicle"] for l in stopped], name="Stopped",
            hovertemplate="%{text}<extra></extra>"))
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=1.1),
                      geo=dict(projection_type="natural earth"), height=420,
                      margin=dict(l=10, r=10, t=10, b=10))
    return render(fig)

# ── Tables ─────────────────────────────────────────────────────────────────

def util_table(util):
    active = [u for u in util if u["total_miles"] > 0]
    if not active: return empty("No vehicle activity")
    rows = "".join(
        f"<tr><td>{u['label']}</td><td class='num'>{u['total_miles']:,.0f}</td>"
        f"<td class='num'>{u['hours_driven']:.1f}</td>"
        f"<td class='num'>{u['utilization_percentage']:.1f}%</td></tr>"
        for u in active[:15])
    return f"<div class='tbl-wrap'><table class='data'><thead><tr><th>Vehicle</th><th class='num'>Miles</th><th class='num'>Hours</th><th class='num'>Util %</th></tr></thead><tbody>{rows}</tbody></table></div>"

def fault_table(faults):
    cur = faults.get("current_faults", [])
    if not cur: return empty("No current faults")
    rows = "".join(
        f"<tr><td>{f['vehicle']}</td><td>{f['fault_code']}</td>"
        f"<td>{f.get('description','') or '—'}</td>"
        f"<td>{f['timestamp'][:10]}</td></tr>"
        for f in cur[:20])
    return f"<div class='tbl-wrap'><table class='data'><thead><tr><th>Vehicle</th><th>Code</th><th>Description</th><th>Date</th></tr></thead><tbody>{rows}</tbody></table></div>"

def driver_table(drivers):
    active = [d for d in drivers if d["distance_driven"] > 0]
    if not active: return empty("No driver activity")
    rows = "".join(
        f"<tr><td>{d['name']}</td><td class='num'>{d['trip_count']}</td>"
        f"<td class='num'>{d['distance_driven']:,.0f}</td>"
        f"<td class='num'>{d['average_trip_length']:,.0f}</td></tr>"
        for d in active[:20])
    return f"<div class='tbl-wrap'><table class='data'><thead><tr><th>Driver</th><th class='num'>Trips</th><th class='num'>Distance</th><th class='num'>Avg Trip</th></tr></thead><tbody>{rows}</tbody></table></div>"

# ── Section bodies ─────────────────────────────────────────────────────────

def section_body(state, data):
    sec = state["section"]

    if sec == "fleet":
        return Div(
            Div(panel("Daily Mileage Trend", mileage_trend(data["trends"]), ACCENT),
                panel("Vehicle Utilization", utilization_chart(data["utilization"]), "#0e7490"),
                cls="grid two"),
            Div(panel("Speed Distribution", speed_dist(data["speed"]), "#ea580c"),
                panel("Idle Time by Vehicle", idle_chart(data["idling"]), "#ea580c"),
                cls="grid two mt"),
            Div(panel("Vehicle Activity", NotStr(util_table(data["utilization"])), ACCENT, scroll=True),
                cls="grid mt"),
        )

    if sec == "vehicles":
        return Div(
            Div(panel("Vehicle Utilization", utilization_chart(data["utilization"]), ACCENT),
                panel("Idle Time", idle_chart(data["idling"]), "#ea580c"),
                cls="grid two"),
            Div(panel("Vehicle Activity Details", NotStr(util_table(data["utilization"])), ACCENT, scroll=True),
                cls="grid mt"),
        )

    if sec == "drivers":
        return Div(
            Div(panel("Driver Distance", driver_chart(data["drivers"]), "#0e7490"),
                panel("Driver Performance", NotStr(driver_table(data["drivers"])), "#0e7490", scroll=True),
                cls="grid two"),
        )

    if sec == "maintenance":
        return Div(
            Div(panel("Fault Code Frequency", fault_chart(data["faults"]), "#dc2626"),
                panel("Current Faults", NotStr(fault_table(data["faults"])), "#dc2626", scroll=True),
                cls="grid two"),
        )

    if sec == "map":
        return Div(
            Div(panel("Fleet Locations", fleet_map_chart(data["locations"]), "#16a34a"), cls="grid"),
        )

    return Div()

# ── Shell ──────────────────────────────────────────────────────────────────

def sidebar(state, data):
    links = []
    for key, label, icon in active_sections(data):
        active = "active" if state["section"] == key else ""
        links.append(A(Span(icon), Span(label), cls=active, hx_get=f"/view?section={key}", **SWAP))
    return Div(
        Div(Span("🚛", cls="mark"),
            Span(NotStr("GEOTAB<small>FLEET ANALYTICS</small>"), cls="name"), cls="brand"),
        Div(*links, cls="nav"),
        Div(A("Logout", href="/logout",
              style="color: #64788f; text-decoration: none; font-size: 11px;"), cls="foot"),
        cls="sidebar")

def header():
    now_str = datetime.now(timezone.utc).strftime("%b %d, %Y %I:%M %p")
    return Div(
        H1("Fleet Analytics"),
        Div(Span(f"Refreshed {now_str} UTC", cls="note"), cls="refreshed"),
        cls="header")

def get_state(req):
    return {"section": req.query_params.get("section", "fleet")}

def app_shell(state):
    data = load_data()
    return Div(
        sidebar(state, data),
        Div(header(), kpi_row(data), section_body(state, data), cls="main"),
        id="app", cls="layout")

# ── Routes ─────────────────────────────────────────────────────────────────

@rt("/")
def index(req):
    g = require_login(req)
    if g: return g
    return Title("Geotab Fleet Analytics"), app_shell(get_state(req))

@rt("/view")
def view(req):
    g = require_login(req)
    if g: return g
    return app_shell(get_state(req))

# ── Login ──────────────────────────────────────────────────────────────────

def login_page(error=None, next_url="/"):
    alert = Div(error, style="color:#b91c1c; margin-bottom:14px;") if error else ""
    return Div(
        Div(H1("Fleet Analytics", style="margin-top:0;"),
            Div("Authorized access only.", cls="note", style="margin-bottom:18px;"),
            alert,
            Form(
                Label("Email", html_for="email"),
                Input(type="email", name="email", id="email", required=True,
                      style="width:100%; padding:10px; border:1px solid #d1d5db; border-radius:10px; margin-bottom:12px;"),
                Label("Password", html_for="password"),
                Input(type="password", name="password", id="password", required=True,
                      style="width:100%; padding:10px; border:1px solid #d1d5db; border-radius:10px; margin-bottom:16px;"),
                Input(type="hidden", name="next", value=next_url),
                Button("Sign in", type="submit",
                       style="width:100%; padding:10px; background:var(--accent); color:#fff; border:none; border-radius:10px; font-weight:600; cursor:pointer;"),
                action="/login", method="post"),
            cls="panel", style="max-width:420px; width:100%; margin:auto;"),
        style="display:flex; align-items:center; justify-content:center; min-height:100vh; padding:0 18px; background:var(--page);")

@rt("/login")
async def login(req):
    if user_authenticated(req):
        return Redirect(req.query_params.get("next", "/"))
    error = None
    next_url = req.query_params.get("next", "/")
    if req.method == "POST":
        try:
            form_data = await req.form()
            payload = {k: v for k, v in form_data.items()}
        except Exception:
            raw = parse_qs((await req.body()).decode("utf-8", errors="ignore"))
            payload = {k: v[0] for k, v in raw.items() if v}
        email = payload.get("email", "").strip()
        pw = payload.get("password", "")
        next_url = payload.get("next", next_url) or "/"
        if email_allowed(email) and verify_password(pw):
            req.session["user"] = email.lower()
            return Redirect(next_url)
        error = "Invalid email or password."
    return Title("Login"), login_page(error, next_url)

@rt("/logout")
def logout(req):
    try: req.session.clear()
    except AssertionError: pass
    return Redirect("/login")

# ── Health ─────────────────────────────────────────────────────────────────

@rt("/health")
def health():
    from database import engine, SessionLocal
    from models import Vehicle, Trip, Driver
    from sqlalchemy import text, select, func
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
        db = SessionLocal()
        vc = db.scalar(select(func.count(Vehicle.id)))
        tc = db.scalar(select(func.count(Trip.id)))
        dc = db.scalar(select(func.count(Driver.id)))
        db.close()
        return {"status": "ok", "db": "connected",
                "vehicles": vc or 0, "trips": tc or 0, "drivers": dc or 0}
    except Exception as e:
        return {"status": "error", "db": str(e)}

# ── Main ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    serve(port=5003)
