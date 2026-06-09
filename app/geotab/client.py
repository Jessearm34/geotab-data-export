from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class GeotabAPIError(RuntimeError):
    pass


class GeotabRateLimitError(GeotabAPIError):
    pass


class GeotabClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = f"https://{self.settings.geotab_server}/apiv1"
        self._credentials: dict[str, Any] | None = None
        self._session = requests.Session()

    @retry(
        retry=retry_if_exception_type((requests.RequestException, GeotabAPIError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        payload = {"method": method, "params": params}
        try:
            response = self._session.post(
                self.base_url,
                json=payload,
                timeout=self.settings.geotab_timeout_seconds,
            )
        except requests.Timeout as exc:
            logger.warning("geotab_timeout method=%s", method)
            raise exc

        if response.status_code == 429:
            raise GeotabRateLimitError("Geotab API rate limit reached")
        if response.status_code >= 500:
            raise GeotabAPIError(f"Geotab server error {response.status_code}")
        response.raise_for_status()

        body = response.json()
        if "error" in body:
            error = body["error"]
            message = error.get("message", "Unknown Geotab API error") if isinstance(error, dict) else str(error)
            raise GeotabAPIError(message)
        return body.get("result")

    def authenticate(self) -> dict[str, Any]:
        if not self.settings.is_geotab_configured:
            raise GeotabAPIError("Geotab credentials are not configured")

        params: dict[str, Any] = {
            "database": self.settings.geotab_database,
            "userName": self.settings.geotab_username,
            "password": self.settings.geotab_password.get_secret_value() if self.settings.geotab_password else None,
        }
        if self.settings.geotab_api_key:
            params["apiKey"] = self.settings.geotab_api_key.get_secret_value()
        result = self._rpc("Authenticate", params)
        credentials = result.get("credentials", result) if isinstance(result, dict) else result
        if not isinstance(credentials, dict):
            raise GeotabAPIError("Invalid Geotab authentication response")
        self._credentials = credentials
        return credentials

    @property
    def credentials(self) -> dict[str, Any]:
        if self._credentials is None:
            self.authenticate()
        if self._credentials is None:
            raise GeotabAPIError("Authentication failed")
        return self._credentials

    def get(self, type_name: str, search: dict[str, Any] | None = None, results_limit: int = 5000) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "typeName": type_name,
            "credentials": self.credentials,
            "resultsLimit": results_limit,
        }
        if search:
            params["search"] = search
        result = self._rpc("Get", params)
        return result if isinstance(result, list) else []


def iso_geotab(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")
