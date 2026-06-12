import base64
import json

import pytest
from itsdangerous import TimestampSigner
from starlette.testclient import TestClient


def test_health_is_public():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_static_served():
    from app.main import app

    client = TestClient(app)
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/css")


def test_api_requires_auth():
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/fleet-summary", follow_redirects=False)
    assert response.status_code == 303


def _reload_app_settings(monkeypatch):
    from app.config import get_settings
    import app.main as main_module

    get_settings.cache_clear()
    main_module.settings = get_settings()
    return main_module.app, get_settings()


def _session_csrf(client: TestClient, secret: str, max_age: int) -> str:
    signer = TimestampSigner(secret)
    data = signer.unsign(client.cookies["session"], max_age=max_age)
    session = json.loads(base64.b64decode(data))
    return str(session["csrf_token"])


def _login(client: TestClient, username: str = "admin", password: str = "admin") -> str | None:
    """Helper: GET /login, submit credentials, return CSRF or None."""
    login_page = client.get("/login")
    if 'name="csrf_token" value="' not in login_page.text:
        return None
    token = login_page.text.split('name="csrf_token" value="')[1].split('"')[0]
    resp = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    return resp.headers.get("location")


def test_login_success_and_logout(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    app, settings = _reload_app_settings(monkeypatch)
    client = TestClient(app)

    location = _login(client)
    assert location == "/"

    authenticated = client.get("/login", follow_redirects=False)
    assert authenticated.status_code == 303
    assert authenticated.headers["location"] == "/"

    logout_token = _session_csrf(
        client,
        settings.session_secret.get_secret_value(),
        settings.session_max_age_seconds,
    )
    logged_out = client.post("/logout", data={"csrf_token": logout_token}, follow_redirects=False)
    assert logged_out.status_code == 303

    blocked = client.get("/api/fleet-summary", follow_redirects=False)
    assert blocked.status_code == 303


def test_login_rejects_invalid_csrf(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    app, _settings = _reload_app_settings(monkeypatch)

    client = TestClient(app)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin", "csrf_token": "invalid"},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Session validation failed." in response.text


def test_login_rejects_wrong_password(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    app, _settings = _reload_app_settings(monkeypatch)
    client = TestClient(app)

    location = _login(client, password="wrongpass")
    assert location is None or location == "/login"


def test_login_rejects_wrong_username(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    app, _settings = _reload_app_settings(monkeypatch)
    client = TestClient(app)

    location = _login(client, username="hacker")
    assert location is None or location == "/login"


def test_session_persists_across_multiple_requests(monkeypatch):
    """After login, multiple protected-route requests remain authenticated."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    app, _settings = _reload_app_settings(monkeypatch)
    client = TestClient(app)

    assert _login(client) == "/"

    for _ in range(5):
        resp = client.get("/login", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/"


def test_all_protected_routes_redirect_when_unauthenticated(monkeypatch):
    """Every non-public route returns 303 when no session exists."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")

    app, _settings = _reload_app_settings(monkeypatch)
    client = TestClient(app)

    protected = [
        "/",
        "/vehicles",
        "/drivers",
        "/maintenance",
        "/fleet-map",
        "/api/fleet-summary",
        "/api/vehicles",
        "/api/drivers",
        "/api/trips",
        "/api/faults",
    ]
    for path in protected:
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 303, f"{path} should return 303, got {resp.status_code}"


def _authenticated_client(monkeypatch) -> TestClient:
    """Return a TestClient that has an active admin session."""
    import tempfile
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    # Use a temp file database (file:// works with check_same_thread=False)
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_db.name}")

    from app.config import get_settings
    import app.main as main_module
    get_settings.cache_clear()
    main_module.settings = get_settings()

    # Rebuild engine + SessionLocal for the temp database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import session as db_session
    db_session.engine = create_engine(
        f"sqlite:///{tmp_db.name}", pool_pre_ping=True, future=True,
        connect_args={"check_same_thread": False},
    )
    db_session.SessionLocal = sessionmaker(
        bind=db_session.engine, autoflush=False, autocommit=False,
        expire_on_commit=False, future=True,
    )

    # Replace the reference in main.py too (it captures SessionLocal at import)
    main_module.SessionLocal = db_session.SessionLocal

    # Create all tables
    from app.models import Base
    Base.metadata.create_all(db_session.engine)

    client = TestClient(main_module.app, follow_redirects=False)

    _ = _login(client)
    return client


class TestDashboardEmptyStates:
    """Verify each dashboard tab shows a proper empty state when no data exists."""

    def test_vehicles_empty_state(self, monkeypatch):
        client = _authenticated_client(monkeypatch)
        resp = client.get("/vehicles")
        assert resp.status_code == 200
        assert "No vehicles are available yet" in resp.text
        assert "Vehicle Dashboard" in resp.text
        assert "synced from Geotab" in resp.text

    def test_drivers_empty_state(self, monkeypatch):
        client = _authenticated_client(monkeypatch)
        resp = client.get("/drivers")
        assert resp.status_code == 200
        assert "No driver data is available" in resp.text
        assert "Driver Dashboard" in resp.text

    def test_maintenance_empty_state(self, monkeypatch):
        client = _authenticated_client(monkeypatch)
        resp = client.get("/maintenance")
        assert resp.status_code == 200
        assert "No diagnostic fault data is available" in resp.text
        assert "Maintenance Dashboard" in resp.text

    def test_fleet_map_empty_state(self, monkeypatch):
        client = _authenticated_client(monkeypatch)
        resp = client.get("/fleet-map")
        assert resp.status_code == 200
        assert "No vehicle location data is available" in resp.text
        assert "Fleet Map" in resp.text


def test_middleware_stack_order_correct():
    """Verify middleware order: Auth (innermost) → Session → ProxyHeaders (outermost).

    Starlette's add_middleware prepends (inserts at 0), so user_middleware is
    stored in reverse order of addition:
      user_middleware[0] = last added = outermost = ProxyHeaders
      user_middleware[-1] = first added = innermost = AuthMiddleware
    """
    from app.main import app
    from app.auth.security import AuthMiddleware
    from starlette.middleware.sessions import SessionMiddleware
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    classes = [m.cls for m in app.user_middleware]

    # First added = innermost (AuthMiddleware)
    assert classes[-1] is AuthMiddleware, f"Expected AuthMiddleware innermost, got {classes[-1]}"
    # Second added = middle (SessionMiddleware)
    assert classes[1] is SessionMiddleware, f"Expected SessionMiddleware middle, got {classes[1]}"
    # Third added = outermost (ProxyHeadersMiddleware)
    assert classes[0] is ProxyHeadersMiddleware, f"Expected ProxyHeaders outermost, got {classes[0]}"

    # Runtime stack (outermost → innermost → handler):
    #   ProxyHeaders → SessionMiddleware → AuthMiddleware


def test_proxy_headers_middleware_registered():
    """ProxyHeadersMiddleware is in the stack so Railway's X-Forwarded-Proto is trusted."""
    from app.main import app
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    assert any(
        m.cls is ProxyHeadersMiddleware for m in app.user_middleware
    ), "ProxyHeadersMiddleware must be registered"


def test_production_session_via_proxy_headers():
    """SessionMiddleware with https_only=True and ProxyHeadersMiddleware:
    X-Forwarded-Proto: https → Secure cookie accepted → session survives auth guard."""
    from starlette.applications import Starlette
    from starlette.middleware.sessions import SessionMiddleware
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.testclient import TestClient
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    from collections.abc import Callable
    from starlette.requests import Request

    class AuthGuard:
        """Simulates production AuthMiddleware: checks session BEFORE passing to handler."""
        def __init__(self, inner: Callable):
            self.app = inner
        async def __call__(self, scope, receive, send):
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return
            request = Request(scope, receive)
            path = request.url.path
            if path in {"/login", "/health"}:
                await self.app(scope, receive, send)
                return
            session = request.scope.get("session")
            if session is None or not session.get("authenticated"):
                await RedirectResponse("/login", 303)(scope, receive, send)
                return
            await self.app(scope, receive, send)

    app = Starlette()

    async def set_session(request):
        request.session["authenticated"] = True
        return JSONResponse({"ok": True})

    async def check_session(request):
        return JSONResponse({"auth": request.session.get("authenticated", False)})

    app.add_route("/login", set_session, methods=["POST"])
    app.add_route("/protected", check_session)

    # CORRECT ORDER: Auth is innermost, Session wraps it, ProxyHeaders is outermost
    app.add_middleware(AuthGuard)
    app.add_middleware(
        SessionMiddleware, secret_key="test-secret", https_only=True, same_site="lax"
    )
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

    # Use HTTPS base URL so httpx's cookie jar accepts Secure cookies
    client = TestClient(app, base_url="https://testserver")

    # Login over proxy headers
    login_resp = client.post("/login", headers={"X-Forwarded-Proto": "https"})
    assert login_resp.status_code == 200
    assert "session=" in login_resp.headers.get("set-cookie", ""), "Session cookie must be set"

    # Protected route must be accessible — session must survive AuthGuard check
    protected_resp = client.get("/protected", headers={"X-Forwarded-Proto": "https"})
    assert protected_resp.status_code == 200, "AuthGuard must not reject valid session"
    assert protected_resp.json()["auth"] is True
