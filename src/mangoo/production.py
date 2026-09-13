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
    module_name = getattr(settings, "module_name", "your settings module")
    errors = []
    if not getattr(settings, "secret_key", "") or settings.secret_key in {"change-this-in-production", "changeme", "secret"}:
        errors.append(f"SECRET_KEY must be a unique environment-provided value -- set SECRET_KEY in {module_name} (e.g. os.getenv('MANGOO_SECRET_KEY'))")
    if not getattr(settings, "allowed_hosts", ()) or "*" in settings.allowed_hosts:
        errors.append(f"ALLOWED_HOSTS must list explicit production hosts -- set ALLOWED_HOSTS in {module_name} to your real domain(s)")
    security = getattr(settings, "security", {})
    if not security.get("FORCE_HTTPS", False):
        errors.append(f"SECURITY['FORCE_HTTPS'] must be True -- set SECURITY['FORCE_HTTPS'] = True in {module_name}")
    if not security.get("SECURE_COOKIES", False):
        errors.append(f"SECURITY['SECURE_COOKIES'] must be True -- set SECURITY['SECURE_COOKIES'] = True in {module_name}")
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
