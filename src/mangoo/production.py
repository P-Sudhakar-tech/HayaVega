"""Production configuration checks and database liveness routes."""
from __future__ import annotations

from .db import connect
from .response import json_response
from .routing import path


class ProductionConfigurationError(ValueError):
    """Raised when a non-debug project has unsafe deployment settings."""


def validate_production(settings) -> None:
    """Reject known-unsafe settings before a non-debug server starts."""
    if getattr(settings, "debug", True):
        return
    errors = []
    if not getattr(settings, "secret_key", "") or settings.secret_key in {"change-this-in-production", "changeme", "secret"}:
        errors.append("SECRET_KEY must be a unique environment-provided value")
    if not getattr(settings, "allowed_hosts", ()) or "*" in settings.allowed_hosts:
        errors.append("ALLOWED_HOSTS must list explicit production hosts")
    security = getattr(settings, "security", {})
    if not security.get("FORCE_HTTPS", False):
        errors.append("SECURITY['FORCE_HTTPS'] must be True")
    if not security.get("SECURE_COOKIES", False):
        errors.append("SECURITY['SECURE_COOKIES'] must be True")
    if errors:
        raise ProductionConfigurationError("Unsafe production configuration: " + "; ".join(errors) + ".")


def health_routes(database):
    """Return unauthenticated liveness and database-readiness endpoints."""
    def health(_request):
        return json_response({"status": "ok"})

    def ready(_request):
        try:
            connection = connect(database)
            try:
                connection.execute("SELECT 1").fetchone()
            finally:
                connection.close()
        except Exception:
            return json_response({"status": "unavailable", "database": "unavailable"}, status=503)
        return json_response({"status": "ok", "database": "ok"})

    return (path("healthz", health), path("readyz", ready))
