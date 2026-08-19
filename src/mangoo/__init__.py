"""Django-like framework surface for Mangoo."""

from .routing import path
from .response import Response
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
from .rest import api_route
from .swagger import openapi_document
from .admin import admin_routes
from .security import csrf_protect, csrf_token, email_verified_required, login_required, permission_required, superuser_required
from .hardening import HardeningOptions
from .static import static_routes
from .templates import render_template
from .testing import TestClient
from .response import json_response
from .collections import MangoList, mango_list
from .models import Decimal, Entity, Integer, Key, Text, Timestamp, Toggle
from .serializers import EntitySerializer, Serializer
from .migrations import apply as apply_migrations, generate as generate_migration, status as migration_status
from .production import health_routes, validate_production
from .logging import JsonFormatter, configure_logging
from .audit import audit_events, record_audit_event

__all__ = ["Decimal", "Entity", "EntitySerializer", "HardeningOptions", "Integer", "JsonFormatter", "Key", "MangoList", "Response", "Serializer", "TestClient", "Text", "Timestamp", "Toggle", "User", "admin_routes", "api_route", "apply_migrations", "audit_events", "authenticate", "authenticate_session", "configure_logging", "create_email_verification_token", "create_password_reset_token", "create_session", "create_superuser", "create_user", "csrf_protect", "csrf_token", "email_verified_required", "enable_totp", "generate_migration", "get_user_by_username", "health_routes", "json_response", "list_sessions", "login_required", "mango_list", "migration_status", "openapi_document", "path", "permission_required", "record_audit_event", "render_template", "reset_password", "revoke_all_sessions", "revoke_session", "rotate_session", "static_routes", "superuser_required", "validate_production", "verify_email"]
