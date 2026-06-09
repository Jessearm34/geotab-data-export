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
