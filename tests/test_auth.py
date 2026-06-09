from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.auth.security import hash_password, verify_admin_password
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


def test_scheduler_requires_geotab_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.delenv("GEOTAB_DATABASE", raising=False)
    monkeypatch.delenv("GEOTAB_USERNAME", raising=False)
    monkeypatch.delenv("GEOTAB_PASSWORD", raising=False)
    _clear_settings_cache()

    from app.jobs.scheduler import start_scheduler

    with pytest.raises(RuntimeError, match="SCHEDULER_ENABLED=true requires Geotab credentials"):
        start_scheduler()


def test_admin_password_hash_precedence_over_plain_password(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("ADMIN_PASSWORD", "plain-password")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", hash_password("hashed-password"))
    _clear_settings_cache()

    assert verify_admin_password("hashed-password") is True
    assert verify_admin_password("plain-password") is False


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
