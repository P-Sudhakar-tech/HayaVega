"""Built-in JSON authentication endpoints backed by Mangoo's local auth store."""
from pathlib import Path
from typing import TYPE_CHECKING

from .auth import DEFAULT_SESSION_SECONDS, authenticate, create_session, revoke_session
from .response import json_response
from .rest import api_route
from .security import SESSION_COOKIE_NAME, cookie, csrf_protect, csrf_token_response, request_session_token, request_user

if TYPE_CHECKING:
    from .addons.pulse import PulseBus


def auth_routes(database_path: str | Path, *, secure_cookies: bool = False, pulse: "PulseBus | None" = None):
    """Return REST routes for login, logout, and the authenticated user.

    ``pulse``, when given a ``PulseBus``, publishes login and session events
    after the database work that produced them has committed.
    """
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
        return {"id": user.id, "username": user.username, "email": user.email, "is_superuser": user.is_superuser}

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

    return [
        api_route("api/auth/login", login, methods=("POST",)),
        api_route("api/auth/csrf", lambda request: csrf_token_response(request, secure=secure_cookies), methods=("GET",)),
        api_route("api/auth/me", current_user, methods=("GET",)),
        api_route("api/auth/logout", logout, methods=("POST",)),
    ]
