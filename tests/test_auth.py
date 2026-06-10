from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.auth.security import (
    csrf_token,
    establish_authenticated_session,
    hash_password,
    is_authenticated,
    validate_csrf,
    verify_admin_password,
)
from app.config import Settings, get_settings
from app.geotab.client import GeotabAPIError, GeotabClient


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def test_production_requires_strong_session_secret_and_password_hash(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET", "dev-session-secret")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("secret"))
    _clear_settings_cache()
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings()

    monkeypatch.setenv("SESSION_SECRET", "production-secret-value")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    _clear_settings_cache()
    with pytest.raises(ValidationError, match="ADMIN_PASSWORD_HASH"):
        Settings()


def test_settings_loads_without_geotab_for_migrations(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.delenv("GEOTAB_DATABASE", raising=False)
    monkeypatch.delenv("GEOTAB_USERNAME", raising=False)
    monkeypatch.delenv("GEOTAB_PASSWORD", raising=False)
    _clear_settings_cache()
    assert Settings().scheduler_enabled is True


def test_scheduler_skips_start_without_geotab_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.delenv("GEOTAB_DATABASE", raising=False)
    monkeypatch.delenv("GEOTAB_USERNAME", raising=False)
    monkeypatch.delenv("GEOTAB_PASSWORD", raising=False)
    _clear_settings_cache()

    from app.jobs.scheduler import start_scheduler

    assert start_scheduler() is None


def test_password_hash_roundtrip(monkeypatch):
    """hash_password() + verify_admin_password() roundtrips correctly.

    This catches future dependency breakage (the app uses stdlib hashlib
    pbkdf2_hmac, not passlib+bcrypt).
    """
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("my-password"))
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    _clear_settings_cache()

    assert verify_admin_password("my-password") is True
    assert verify_admin_password("wrong-password") is False


def test_admin_password_hash_precedence_over_plain_password(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_PASSWORD", "plain-password")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("hashed-password"))
    _clear_settings_cache()

    assert verify_admin_password("hashed-password") is True
    assert verify_admin_password("plain-password") is False


def test_unrecognized_hash_format_returns_false(monkeypatch):
    """A non-bcrypt ADMIN_PASSWORD_HASH must not crash — return False instead."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "a1b2c3d4e5f6")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    _clear_settings_cache()

    assert verify_admin_password("anything") is False


def test_malformed_hash_does_not_crash(monkeypatch):
    """Garbage in ADMIN_PASSWORD_HASH must never propagate a 500."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "not-a-valid-hash-format")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    _clear_settings_cache()

    assert verify_admin_password("anything") is False


def test_login_with_unrecognized_hash_returns_failure_not_500(monkeypatch):
    """Integration-style: POST /login with unrecognized hash must not crash."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "not-a-valid-hash")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    _clear_settings_cache()

    from app.main import app
    from starlette.testclient import TestClient

    client = TestClient(app)
    login_page = client.get("/login")
    token = login_page.text.split('name="csrf_token" value="')[1].split('"')[0]
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "anything", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "Invalid username or password." in resp.text


def test_production_rejects_sqlite_database_url(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET", "production-secret-value")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("secret"))
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./local.db")
    _clear_settings_cache()

    with pytest.raises(ValidationError, match="DATABASE_URL must point to PostgreSQL"):
        Settings()


def test_plain_admin_password_ignored_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("SESSION_SECRET", "production-secret-value")
    monkeypatch.setenv("ADMIN_PASSWORD", "plain-password")
    monkeypatch.delenv("ADMIN_PASSWORD_HASH", raising=False)
    _clear_settings_cache()

    with pytest.raises(ValidationError, match="ADMIN_PASSWORD_HASH"):
        Settings()


def test_geotab_client_reauthenticates_on_session_error(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("GEOTAB_DATABASE", "demo")
    monkeypatch.setenv("GEOTAB_USERNAME", "service@example.com")
    monkeypatch.setenv("GEOTAB_PASSWORD", "secret")
    _clear_settings_cache()

    settings = get_settings()
    client = GeotabClient(settings=settings)
    client._credentials = {"sessionId": "stale", "database": "demo", "userName": "service@example.com"}

    auth_result = {"credentials": {"sessionId": "fresh", "database": "demo", "userName": "service@example.com"}}
    session_error = {"error": {"name": "InvalidSessionException", "message": "Session expired"}}
    success = {"result": [{"id": "device-1"}]}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = [session_error, {"result": auth_result}, success]
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "post", return_value=mock_response) as post:
        rows = client.get("Device")

    assert rows == [{"id": "device-1"}]
    assert post.call_count == 3
    assert client._credentials == auth_result["credentials"]


# --------------------------------------------------------------------------- #
# Auth security unit tests — is_authenticated, csrf_token, validate_csrf, etc.
# --------------------------------------------------------------------------- #


def _make_request(scope: dict | None = None) -> MagicMock:
    """Create a mock request with a controlled scope dict."""
    req = MagicMock()
    req.scope = scope or {}
    return req


def test_is_authenticated_with_no_session():
    """is_authenticated returns False when scope has no 'session' key."""
    req = _make_request({})
    assert is_authenticated(req) is False


def test_is_authenticated_with_none_session():
    """is_authenticated returns False when scope['session'] is None."""
    req = _make_request({"session": None})
    assert is_authenticated(req) is False


def test_is_authenticated_with_empty_session():
    """is_authenticated returns False when session dict is empty."""
    req = _make_request({"session": {}})
    assert is_authenticated(req) is False


def test_is_authenticated_without_authenticated_key():
    """is_authenticated returns False when session lacks 'authenticated' key."""
    req = _make_request({"session": {"csrf_token": "abc"}})
    assert is_authenticated(req) is False


def test_is_authenticated_with_authenticated_true():
    """is_authenticated returns True when session['authenticated'] is True."""
    req = _make_request({"session": {"authenticated": True}})
    assert is_authenticated(req) is True


def test_is_authenticated_with_authenticated_false():
    """is_authenticated returns False when session['authenticated'] is False."""
    req = _make_request({"session": {"authenticated": False}})
    assert is_authenticated(req) is False


def test_csrf_token_generates_and_persists():
    """csrf_token generates a token, stores in session, returns same on next call."""
    session: dict = {}
    req = _make_request({"session": session})
    token1 = csrf_token(req)
    assert len(token1) > 20, "CSRF token should be a non-trivial string"
    assert session.get("csrf_token") == token1

    token2 = csrf_token(req)
    assert token2 == token1, "Subsequent calls should return the same token"


def test_csrf_token_with_no_session():
    """csrf_token returns empty string when no session dict is available."""
    req = _make_request({"session": None})
    assert csrf_token(req) == ""


def test_validate_csrf_get_method(monkeypatch):
    """validate_csrf returns True for GET/HEAD/OPTIONS without checking tokens."""
    req = _make_request({"session": {"csrf_token": "abc"}})
    req.method = "GET"
    import asyncio
    assert asyncio.run(validate_csrf(req)) is True


def test_establish_authenticated_session_clears_and_sets():
    """establish_authenticated_session clears existing session and sets authenticated."""
    session = {"csrf_token": "old", "other_data": "stuff"}
    req = _make_request({"session": session})
    establish_authenticated_session(req)
    assert session.get("authenticated") is True
    assert session.get("csrf_token") is not None
    assert session.get("other_data") is None or "other_data" not in session


def test_establish_authenticated_session_no_session():
    """establish_authenticated_session does not crash when scope has no session."""
    req = _make_request({"session": None})
    establish_authenticated_session(req)
    assert True


def test_geotab_authenticate_logs_sanitized_failure(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("GEOTAB_DATABASE", "demo")
    monkeypatch.setenv("GEOTAB_USERNAME", "service@example.com")
    monkeypatch.setenv("GEOTAB_PASSWORD", "secret")
    _clear_settings_cache()

    client = GeotabClient(settings=get_settings())
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"error": {"name": "InvalidUserException", "message": "Incorrect login credentials"}}
    mock_response.raise_for_status = MagicMock()

    with patch.object(client._session, "post", return_value=mock_response):
        with patch("app.geotab.client.logger.warning") as warning:
            with pytest.raises(GeotabAPIError):
                client.authenticate()

    warning.assert_called_once()
    log_message = " ".join(str(arg) for arg in warning.call_args[0])
    assert "secret" not in log_message.lower()
    assert "service@example.com" in log_message
