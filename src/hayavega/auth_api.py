"""Built-in JSON authentication endpoints backed by Hayavega's local auth store."""

from __future__ import annotations

import logging
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .auth import (
    DEFAULT_SESSION_SECONDS,
    User,
    authenticate,
    create_email_verification_token,
    create_password_reset_token,
    create_session,
    get_user_by_username,
    list_sessions,
    reset_password,
    revoke_all_sessions,
    revoke_session,
    verify_email,
)
from .hardening import parse_trusted_proxy_networks, resolve_client_ip
from .response import json_response
from .rest import api_route
from .security import SESSION_COOKIE_NAME, cookie, csrf_protect, csrf_token_response, request_session_token, request_user
from .swagger import ERROR_SCHEMA

if TYPE_CHECKING:
    from .addons.signals import SignalBus
    from .hardening import HardeningOptions


MAIL_LOGGER = logging.getLogger("hayavega.auth.mail")

_USER_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "username": {"type": "string"},
        "email": {"type": "string"},
        "is_superuser": {"type": "boolean"},
        "email_verified": {"type": "boolean"},
    },
}
_SESSION_TOKEN_SCHEMA = {
    "type": "object",
    "properties": {"token": {"type": "string"}, "token_type": {"type": "string", "enum": ["Bearer"]}, "expires_in": {"type": "integer"}},
}
_SESSION_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "created_at": {"type": "string"},
        "expires_at": {"type": "integer"},
        "ip_address": {"type": ["string", "null"]},
        "user_agent": {"type": ["string", "null"]},
    },
}


def log_mail_notifier(kind: str, user: User, token: str) -> None:
    """Default notifier: log the token instead of sending real mail.

    Pass your own ``notify`` callable to ``auth_routes`` to deliver a real
    email; this default only exists so password reset and email verification
    work out of the box without a mail provider configured.
    """
    MAIL_LOGGER.info("hayavega.auth.mail kind=%s user_id=%s token=%s", kind, user.id, token)


def auth_routes(
    database_path: str | Path,
    *,
    secure_cookies: bool = False,
    secret_key: str | None = None,
    signals: SignalBus | None = None,
    pulse: SignalBus | None = None,
    notify: Callable[[str, User, str], None] | None = None,
    hardening: HardeningOptions | None = None,
):
    """Return REST routes for login, logout, password reset, email verification, and session management.

    ``signals``, when given a ``SignalBus``, publishes login and session
    events after the database work that produced them has committed.
    ``pulse`` is a deprecated alias for ``signals``. ``notify``,
    when given a callable, is called as ``notify(kind, user, token)`` for
    ``"password_reset"`` and ``"email_verification"`` instead of the default,
    which only logs the token -- pass your own to send real email. ``hardening``,
    when given the same ``HardeningOptions`` passed to ``create_application``,
    lets login recover the real client IP behind a trusted proxy instead of
    the proxy's own address; without it, the immediate connection's address
    is used. Every login and session records its IP address and user agent
    (when known) in the audit log and on the session itself. ``secret_key``
    (pass ``Settings.secret_key``) is only needed if any user has TOTP
    enabled with their secret stored encrypted (``enable_totp(..., secret_key=...)``)
    -- without it here, such a user's TOTP check fails closed rather than
    silently skipping it.
    """
    if pulse is not None:
        warnings.warn("`pulse=` is deprecated; use `signals=` instead.", DeprecationWarning, stacklevel=2)
        signals = pulse if signals is None else signals
    notify = notify or log_mail_notifier
    trust_proxy_headers = bool(hardening and hardening.trust_proxy_headers)
    trusted_proxy_networks = parse_trusted_proxy_networks(hardening.trusted_proxy_ips) if hardening else ()

    def login(request):
        try:
            payload = request.json()
            client_ip = resolve_client_ip(request, trust_proxy_headers=trust_proxy_headers, trusted_proxy_networks=trusted_proxy_networks)
            user_agent = request.headers.get("User-Agent") or None
            user = authenticate(database_path, payload["username"], payload["password"], payload.get("totp_code"), ip_address=client_ip, user_agent=user_agent, secret_key=secret_key)
        except (KeyError, TypeError, ValueError):
            return json_response({"detail": "Invalid authentication request."}, 400)
        if user is None:
            if signals is not None:
                signals.publish("auth.login_failed")
            return json_response({"detail": "Invalid credentials."}, 401)
        token = create_session(database_path, user, ip_address=client_ip, user_agent=user_agent)
        if signals is not None:
            signals.publish("auth.login_succeeded", actor_id=user.id, data={"method": "totp" if payload.get("totp_code") else "password"})
            signals.publish("auth.session_created", actor_id=user.id)
        return json_response(
            {"token": token, "token_type": "Bearer", "expires_in": DEFAULT_SESSION_SECONDS},
            headers=(cookie(SESSION_COOKIE_NAME, token, max_age=DEFAULT_SESSION_SECONDS, http_only=True, secure=secure_cookies),),
        )

    def current_user(request):
        user = request_user(request, database_path)
        if user is None:
            return json_response({"detail": "Authentication required."}, 401)
        return {"id": user.id, "username": user.username, "email": user.email, "is_superuser": user.is_superuser, "email_verified": user.email_verified}

    @csrf_protect
    def logout(request):
        user = request_user(request, database_path)
        if user is None:
            return json_response({"detail": "Authentication required."}, 401)
        token = request_session_token(request)
        if token:
            revoke_session(database_path, token)
            if signals is not None:
                signals.publish("auth.session_revoked", actor_id=user.id)
        return json_response("", 204, headers=(cookie(SESSION_COOKIE_NAME, "", max_age=0, http_only=True, secure=secure_cookies),))

    def request_password_reset(request):
        try:
            username = request.json()["username"]
        except (KeyError, TypeError, ValueError):
            return json_response({"detail": "Invalid request."}, 400)
        user = get_user_by_username(database_path, username)
        if user is not None:
            token = create_password_reset_token(database_path, user)
            notify("password_reset", user, token)
        # Always the same response, whether or not the username exists.
        return json_response("", 202)

    def confirm_password_reset(request):
        try:
            payload = request.json()
            token, password = payload["token"], payload["password"]
        except (KeyError, TypeError, ValueError):
            return json_response({"detail": "Invalid request."}, 400)
        try:
            user = reset_password(database_path, token, password)
        except ValueError as error:
            return json_response({"detail": str(error)}, 400)
        if user is None:
            return json_response({"detail": "Invalid or expired token."}, 400)
        return json_response("", 204)

    def request_email_verification(request):
        user = request_user(request, database_path)
        if user is None:
            return json_response({"detail": "Authentication required."}, 401)
        token = create_email_verification_token(database_path, user)
        notify("email_verification", user, token)
        return json_response("", 202)

    def confirm_email_verification(request):
        try:
            token = request.json()["token"]
        except (KeyError, TypeError, ValueError):
            return json_response({"detail": "Invalid request."}, 400)
        user = verify_email(database_path, token)
        if user is None:
            return json_response({"detail": "Invalid or expired token."}, 400)
        return json_response("", 204)

    def sessions_view(request):
        user = request_user(request, database_path)
        if user is None:
            return json_response({"detail": "Authentication required."}, 401)
        return json_response([{"id": s.id, "created_at": s.created_at, "expires_at": s.expires_at, "ip_address": s.ip_address, "user_agent": s.user_agent} for s in list_sessions(database_path, user)])

    @csrf_protect
    def revoke_other_sessions(request):
        user = request_user(request, database_path)
        if user is None:
            return json_response({"detail": "Authentication required."}, 401)
        revoked = revoke_all_sessions(database_path, user, except_token=request_session_token(request))
        return json_response({"revoked": revoked})

    return [
        api_route(
            "api/auth/login",
            login,
            methods=("POST",),
            request_schema={"type": "object", "properties": {"username": {"type": "string"}, "password": {"type": "string"}, "totp_code": {"type": "string"}}, "required": ["username", "password"]},
            responses={200: {"description": "Session created.", "schema": _SESSION_TOKEN_SCHEMA}, 401: {"description": "Invalid credentials.", "schema": ERROR_SCHEMA}},
        ),
        api_route(
            "api/auth/csrf",
            lambda request: csrf_token_response(request, secure=secure_cookies),
            methods=("GET",),
            responses={200: {"description": "A CSRF token for the current session.", "schema": {"type": "object", "properties": {"csrf_token": {"type": "string"}}}}},
        ),
        api_route(
            "api/auth/me",
            current_user,
            methods=("GET",),
            secured=True,
            responses={200: {"description": "The authenticated user.", "schema": _USER_SCHEMA}},
        ),
        api_route(
            "api/auth/logout",
            logout,
            methods=("POST",),
            secured=True,
            responses={204: {"description": "Session revoked."}},
        ),
        api_route(
            "api/auth/password-reset/request",
            request_password_reset,
            methods=("POST",),
            request_schema={"type": "object", "properties": {"username": {"type": "string"}}, "required": ["username"]},
            responses={202: {"description": "Accepted regardless of whether the username exists, to avoid revealing valid accounts."}},
        ),
        api_route(
            "api/auth/password-reset/confirm",
            confirm_password_reset,
            methods=("POST",),
            request_schema={"type": "object", "properties": {"token": {"type": "string"}, "password": {"type": "string"}}, "required": ["token", "password"]},
            responses={204: {"description": "Password changed; every existing session was revoked."}, 400: {"description": "The token was invalid or expired, or the password did not meet policy.", "schema": ERROR_SCHEMA}},
        ),
        api_route(
            "api/auth/email/verify/request",
            request_email_verification,
            methods=("POST",),
            secured=True,
            responses={202: {"description": "A verification token was issued and passed to the configured notifier."}},
        ),
        api_route(
            "api/auth/email/verify/confirm",
            confirm_email_verification,
            methods=("POST",),
            request_schema={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            responses={204: {"description": "Email verified."}, 400: {"description": "The token was invalid or expired.", "schema": ERROR_SCHEMA}},
        ),
        api_route(
            "api/auth/sessions",
            sessions_view,
            methods=("GET",),
            secured=True,
            responses={200: {"description": "The current user's active sessions.", "schema": {"type": "array", "items": _SESSION_SUMMARY_SCHEMA}}},
        ),
        api_route(
            "api/auth/sessions/revoke-others",
            revoke_other_sessions,
            methods=("POST",),
            secured=True,
            responses={200: {"description": "Every other session for this user was revoked.", "schema": {"type": "object", "properties": {"revoked": {"type": "integer"}}}}},
        ),
    ]
