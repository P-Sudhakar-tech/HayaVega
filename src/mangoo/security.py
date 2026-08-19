"""Cookie sessions, CSRF checks, and reusable authorization decorators."""
from __future__ import annotations

from functools import wraps
from hmac import compare_digest
import secrets

from .auth import User, authenticate_session
from .response import json_response


SESSION_COOKIE_NAME = "mangoo_session"
CSRF_COOKIE_NAME = "mangoo_csrf"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


def cookie(name: str, value: str, *, max_age: int | None = None, http_only: bool = False, same_site: str = "Lax", secure: bool = False) -> tuple[str, str]:
    """Return a host-only cookie header with safe framework defaults."""
    parts = [f"{name}={value}", "Path=/", f"SameSite={same_site}"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "Set-Cookie", "; ".join(parts)


def csrf_token(request) -> str:
    return request.cookies.get(CSRF_COOKIE_NAME) or secrets.token_urlsafe(32)


def csrf_token_response(request, *, secure: bool = False):
    token = csrf_token(request)
    return json_response({"csrf_token": token}, headers=(cookie(CSRF_COOKIE_NAME, token, max_age=8 * 60 * 60, same_site="Strict", secure=secure),))


def csrf_protect(view):
    """Reject unsafe cookie-authenticated requests without a matching CSRF token."""
    @wraps(view)
    def protected(request):
        if request.method not in SAFE_METHODS and request.cookies.get(SESSION_COOKIE_NAME):
            expected = request.cookies.get(CSRF_COOKIE_NAME, "")
            supplied = request.headers.get("X-Csrf-Token", "")
            if not expected or not supplied or not compare_digest(expected, supplied):
                return json_response({"detail": "CSRF token missing or incorrect."}, 403)
        return view(request)
    return protected


def request_session_token(request) -> str | None:
    """Read a Bearer token, falling back to the framework's session cookie."""
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    return token if scheme.lower() == "bearer" and token else request.cookies.get(SESSION_COOKIE_NAME)


def request_user(request, database_path) -> User | None:
    """Resolve a user from a Bearer token or the framework's HttpOnly session cookie."""
    token = request_session_token(request)
    return authenticate_session(database_path, token) if token else None


def permission_required(database_path, predicate, *, message: str = "Permission denied."):
    """Require an authenticated user satisfying ``predicate(user)`` for a view."""
    def decorate(view):
        @wraps(view)
        def protected(request):
            user = request_user(request, database_path)
            if user is None:
                return json_response({"detail": "Authentication required."}, 401)
            if not predicate(user):
                return json_response({"detail": message}, 403)
            return view(request)
        return protected
    return decorate


def login_required(database_path):
    return permission_required(database_path, lambda user: True)


def superuser_required(database_path):
    return permission_required(database_path, lambda user: user.is_superuser, message="Administrator permission required.")


def email_verified_required(database_path):
    return permission_required(database_path, lambda user: user.email_verified, message="Email verification required.")
