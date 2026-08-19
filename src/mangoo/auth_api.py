"""Built-in JSON authentication endpoints backed by Mangoo's local auth store."""
from __future__ import annotations

from collections.abc import Callable
import logging
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
from .response import json_response
from .rest import api_route
from .security import SESSION_COOKIE_NAME, cookie, csrf_protect, csrf_token_response, request_session_token, request_user

if TYPE_CHECKING:
    from .addons.pulse import PulseBus


MAIL_LOGGER = logging.getLogger("mangoo.auth.mail")


def log_mail_notifier(kind: str, user: User, token: str) -> None:
    """Default notifier: log the token instead of sending real mail.

    Pass your own ``notify`` callable to ``auth_routes`` to deliver a real
    email; this default only exists so password reset and email verification
    work out of the box without a mail provider configured.
    """
    MAIL_LOGGER.info("mangoo.auth.mail kind=%s user_id=%s token=%s", kind, user.id, token)


def auth_routes(
    database_path: str | Path,
    *,
    secure_cookies: bool = False,
    pulse: "PulseBus | None" = None,
    notify: Callable[[str, User, str], None] | None = None,
):
    """Return REST routes for login, logout, password reset, email verification, and session management.

    ``pulse``, when given a ``PulseBus``, publishes login and session events
    after the database work that produced them has committed. ``notify``,
    when given a callable, is called as ``notify(kind, user, token)`` for
    ``"password_reset"`` and ``"email_verification"`` instead of the default,
    which only logs the token -- pass your own to send real email.
    """
    notify = notify or log_mail_notifier

    def login(request):
        try:
            payload = request.json()
            user = authenticate(database_path, payload["username"], payload["password"], payload.get("totp_code"))
        except (KeyError, TypeError, ValueError):
            return json_response({"detail": "Invalid authentication request."}, 400)
        if user is None:
            if pulse is not None:
                pulse.publish("auth.login_failed")
            return json_response({"detail": "Invalid credentials."}, 401)
        token = create_session(database_path, user)
        if pulse is not None:
            pulse.publish("auth.login_succeeded", actor_id=user.id, data={"method": "totp" if payload.get("totp_code") else "password"})
            pulse.publish("auth.session_created", actor_id=user.id)
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
            if pulse is not None:
                pulse.publish("auth.session_revoked", actor_id=user.id)
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
        return json_response([{"id": s.id, "created_at": s.created_at, "expires_at": s.expires_at} for s in list_sessions(database_path, user)])

    @csrf_protect
    def revoke_other_sessions(request):
        user = request_user(request, database_path)
        if user is None:
            return json_response({"detail": "Authentication required."}, 401)
        revoked = revoke_all_sessions(database_path, user, except_token=request_session_token(request))
        return json_response({"revoked": revoked})

    return [
        api_route("api/auth/login", login, methods=("POST",)),
        api_route("api/auth/csrf", lambda request: csrf_token_response(request, secure=secure_cookies), methods=("GET",)),
        api_route("api/auth/me", current_user, methods=("GET",)),
        api_route("api/auth/logout", logout, methods=("POST",)),
        api_route("api/auth/password-reset/request", request_password_reset, methods=("POST",)),
        api_route("api/auth/password-reset/confirm", confirm_password_reset, methods=("POST",)),
        api_route("api/auth/email/verify/request", request_email_verification, methods=("POST",)),
        api_route("api/auth/email/verify/confirm", confirm_email_verification, methods=("POST",)),
        api_route("api/auth/sessions", sessions_view, methods=("GET",)),
        api_route("api/auth/sessions/revoke-others", revoke_other_sessions, methods=("POST",)),
    ]
