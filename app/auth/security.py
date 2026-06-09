from __future__ import annotations

import secrets
import hashlib
import hmac
from collections.abc import Awaitable, Callable

from starlette.datastructures import FormData
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from app.config import get_settings

try:
    from passlib.context import CryptContext
except ModuleNotFoundError:
    CryptContext = None

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto") if CryptContext else None


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
    settings = get_settings()
    if settings.admin_password_hash:
        return _verify_hash(password, settings.admin_password_hash)
    if settings.environment != "production" and settings.admin_password:
        return secrets.compare_digest(password, settings.admin_password.get_secret_value())
    return False


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
