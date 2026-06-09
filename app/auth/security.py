from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.config import get_settings

try:
    from passlib.context import CryptContext
except ModuleNotFoundError:
    CryptContext = None

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") if CryptContext else None

_LOGIN_ATTEMPT_WINDOW_SECONDS = 15 * 60
_LOGIN_ATTEMPT_LIMIT = 5
_LOGIN_FAILURE_DELAY_SECONDS = 1.0
_login_attempts: dict[str, list[float]] = defaultdict(list)


def hash_password(password: str) -> str:
    if pwd_context:
        return pwd_context.hash(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 250_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def _verify_hash(password: str, stored: str) -> bool:
    if pwd_context:
        return pwd_context.verify(password, stored)
    if not stored.startswith("pbkdf2_sha256$"):
        return False
    _, salt, digest = stored.split("$", 2)
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 250_000).hex()
    return hmac.compare_digest(candidate, digest)


def verify_admin_password(password: str) -> bool:
    """ADMIN_PASSWORD_HASH takes precedence; plain ADMIN_PASSWORD is local/dev only."""
    settings = get_settings()
    if settings.admin_password_hash:
        return _verify_hash(password, settings.admin_password_hash)
    if settings.is_production:
        return False
    if settings.admin_password:
        return secrets.compare_digest(password, settings.admin_password.get_secret_value())
    return False


def _client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _prune_login_attempts(key: str) -> list[float]:
    now = time.monotonic()
    attempts = [stamp for stamp in _login_attempts[key] if now - stamp < _LOGIN_ATTEMPT_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    return attempts


def login_allowed(request: Request, username: str) -> bool:
    key = f"{_client_key(request)}:{username}"
    return len(_prune_login_attempts(key)) < _LOGIN_ATTEMPT_LIMIT


def record_login_failure(request: Request, username: str) -> None:
    key = f"{_client_key(request)}:{username}"
    _prune_login_attempts(key).append(time.monotonic())
    logger.warning("login_failed username=%s client=%s", username, _client_key(request))
    time.sleep(_LOGIN_FAILURE_DELAY_SECONDS)


def record_login_success(request: Request, username: str) -> None:
    key = f"{_client_key(request)}:{username}"
    _login_attempts.pop(key, None)


def establish_authenticated_session(request: Request) -> None:
    request.session.clear()
    request.session["authenticated"] = True
    csrf_token(request)


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get("authenticated"))


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return str(token)


async def validate_csrf(request: Request, form: FormData | None = None) -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    expected = request.session.get("csrf_token")
    actual = request.headers.get("x-csrf-token")
    if form is not None:
        actual = str(form.get("csrf_token") or actual or "")
    return bool(expected and actual and secrets.compare_digest(str(expected), str(actual)))


class AuthMiddleware:
    def __init__(self, app: Callable[..., Awaitable[Response]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        path = request.url.path
        public = path.startswith("/static") or path in {"/login", "/health"}
        if not public and not is_authenticated(request):
            response = RedirectResponse("/login", status_code=303)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
