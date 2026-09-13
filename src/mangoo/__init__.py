"""Django-like framework surface for Mangoo."""

from .admin import ModelAdmin, admin_routes
from .audit import audit_events, record_audit_event
from .auth import (
    User,
    authenticate,
    authenticate_session,
    create_email_verification_token,
    create_password_reset_token,
    create_session,
    create_superuser,
    create_user,
    enable_totp,
    get_user_by_username,
    list_sessions,
    reset_password,
    revoke_all_sessions,
    revoke_session,
    rotate_session,
    verify_email,
)
from .collections import MangoList, mango_list
from .hardening import HardeningOptions
from .logging import JsonFormatter, configure_logging
from .metrics import MetricsRegistry, metrics_routes
from .migrations import apply as apply_migrations
from .migrations import generate as generate_migration
from .migrations import status as migration_status
from .models import Decimal, Entity, Integer, Key, Text, Timestamp, Toggle
from .production import health_routes, validate_production
from .response import Response, json_response
from .rest import api_route
from .routing import path
from .security import csrf_protect, csrf_token, email_verified_required, login_required, permission_required, superuser_required
from .serializers import EntitySerializer, Serializer
from .static import static_routes
from .swagger import ERROR_SCHEMA, openapi_document
from .templates import render_template
from .testing import AsyncTestClient, TestClient

__all__ = [
    "AsyncTestClient",
    "Decimal",
    "ERROR_SCHEMA",
    "Entity",
    "EntitySerializer",
    "HardeningOptions",
    "Integer",
    "JsonFormatter",
    "Key",
    "MangoList",
    "MetricsRegistry",
    "ModelAdmin",
    "Response",
    "Serializer",
    "TestClient",
    "Text",
    "Timestamp",
    "Toggle",
    "User",
    "admin_routes",
    "api_route",
    "apply_migrations",
    "audit_events",
    "authenticate",
    "authenticate_session",
    "configure_logging",
    "create_email_verification_token",
    "create_password_reset_token",
    "create_session",
    "create_superuser",
    "create_user",
    "csrf_protect",
    "csrf_token",
    "email_verified_required",
    "enable_totp",
    "generate_migration",
    "get_user_by_username",
    "health_routes",
    "json_response",
    "list_sessions",
    "login_required",
    "mango_list",
    "metrics_routes",
    "migration_status",
    "openapi_document",
    "path",
    "permission_required",
    "record_audit_event",
    "render_template",
    "reset_password",
    "revoke_all_sessions",
    "revoke_session",
    "rotate_session",
    "static_routes",
    "superuser_required",
    "validate_production",
    "verify_email",
]
