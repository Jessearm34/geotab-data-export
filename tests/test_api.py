import base64
import json

from itsdangerous import TimestampSigner
from starlette.testclient import TestClient


def test_health_is_public():
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


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


def test_login_success_and_logout(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)

    app, settings = _reload_app_settings(monkeypatch)

    client = TestClient(app)
    login_page = client.get("/login")
    token = login_page.text.split('name="csrf_token" value="')[1].split('"')[0]

    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

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
