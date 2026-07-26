"""Built-in JSON authentication endpoints backed by Mangoo's local auth store."""
from pathlib import Path

from .auth import DEFAULT_SESSION_SECONDS, authenticate, authenticate_session, create_session, revoke_session
from .response import json_response
from .rest import api_route


def _bearer_token(request) -> str | None:
    value = request.headers.get("Authorization", "")
    scheme, _, token = value.partition(" ")
    return token if scheme.lower() == "bearer" and token else None


def auth_routes(database_path: str | Path):
    """Return REST routes for login, logout, and the authenticated user."""
    def login(request):
        try:
            payload = request.json()
            user = authenticate(database_path, payload["username"], payload["password"], payload.get("totp_code"))
        except (KeyError, TypeError, ValueError):
            return json_response({"detail": "Invalid authentication request."}, 400)
        if user is None:
            return json_response({"detail": "Invalid credentials."}, 401)
        token = create_session(database_path, user)
        return {"token": token, "token_type": "Bearer", "expires_in": DEFAULT_SESSION_SECONDS}

    def current_user(request):
        token = _bearer_token(request)
        user = authenticate_session(database_path, token) if token else None
        if user is None:
            return json_response({"detail": "Authentication required."}, 401)
        return {"id": user.id, "username": user.username, "email": user.email, "is_superuser": user.is_superuser}

    def logout(request):
        token = _bearer_token(request)
        if not token or authenticate_session(database_path, token) is None:
            return json_response({"detail": "Authentication required."}, 401)
        revoke_session(database_path, token)
        return json_response("", 204)

    return [
        api_route("api/auth/login", login, methods=("POST",)),
        api_route("api/auth/me", current_user, methods=("GET",)),
        api_route("api/auth/logout", logout, methods=("POST",)),
    ]
