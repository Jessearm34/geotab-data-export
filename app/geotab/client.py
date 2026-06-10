from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_SESSION_ERROR_MARKERS = (
    "invaliduser",
    "invalid credentials",
    "session",
    "sessionid",
    "authentication",
    "not authenticated",
    "credential",
)
_MIN_BISECT_GAP_SECONDS = 60


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

    @staticmethod
    def _error_message(error: Any) -> str:
        if isinstance(error, dict):
            return str(error.get("message", "Unknown Geotab API error"))
        return str(error)

    @classmethod
    def _is_session_error(cls, error: Any, message: str) -> bool:
        if isinstance(error, dict):
            name = str(error.get("name", "")).lower()
            if any(token in name for token in ("session", "invalid", "auth", "credential")):
                return True
        lowered = message.lower()
        return any(marker in lowered for marker in _SESSION_ERROR_MARKERS)

    @retry(
        retry=retry_if_exception_type((requests.RequestException, GeotabRateLimitError)),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _rpc(self, method: str, params: dict[str, Any], *, allow_reauth: bool = True) -> Any:
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
            message = self._error_message(error)
            if (
                allow_reauth
                and method != "Authenticate"
                and "credentials" in params
                and self._is_session_error(error, message)
            ):
                logger.warning("geotab_session_invalid method=%s", method)
                self._credentials = None
                retry_params = dict(params)
                retry_params["credentials"] = self.authenticate()
                return self._rpc(method, retry_params, allow_reauth=False)
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

        try:
            result = self._rpc("Authenticate", params, allow_reauth=False)
        except GeotabAPIError:
            logger.warning(
                "geotab_auth_failed server=%s database=%s username=%s",
                self.settings.geotab_server,
                self.settings.geotab_database,
                self.settings.geotab_username,
            )
            raise

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

    def get_all(self, type_name: str, search: dict[str, Any] | None = None, results_limit: int = 50000) -> list[dict[str, Any]]:
        """Fetch all records, bisecting time range if result count hits the limit.

        Geotab returns at most ``results_limit`` records. If we hit that limit,
        the result may be truncated. This method splits the search window in half
        and recurses to guarantee completeness.
        """
        if not search or "fromDate" not in search:
            return self.get(type_name, search, results_limit=results_limit)
        return self._get_bisect(type_name, search, results_limit, _MIN_BISECT_GAP_SECONDS)

    def _get_bisect(
        self, type_name: str, search: dict[str, Any], results_limit: int, min_gap: int
    ) -> list[dict[str, Any]]:
        result = self.get(type_name, search, results_limit=results_limit)
        if len(result) < results_limit:
            return result
        from_str = search.get("fromDate", "")
        to_str = search.get("toDate", "")
        from_dt = datetime.fromisoformat(from_str.replace("Z", "+00:00")) if from_str else datetime.now(timezone.utc) - timedelta(days=365)
        to_dt = datetime.fromisoformat(to_str.replace("Z", "+00:00")) if to_str else datetime.now(timezone.utc)
        gap = int((to_dt - from_dt).total_seconds())
        if gap < min_gap:
            logger.warning("get_all_bisect_min_gap type=%s gap=%ds — returning partial result (%d records)", type_name, gap, len(result))
            return result
        mid = from_dt + (to_dt - from_dt) / 2
        logger.info("get_all_bisect type=%s gap=%ds split=%s", type_name, gap, mid.isoformat())
        first = self._get_bisect(type_name, {**search, "fromDate": iso_geotab(from_dt), "toDate": iso_geotab(mid)}, results_limit, min_gap)
        second = self._get_bisect(type_name, {**search, "fromDate": iso_geotab(mid), "toDate": iso_geotab(to_dt)}, results_limit, min_gap)
        return first + second


def iso_geotab(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")
