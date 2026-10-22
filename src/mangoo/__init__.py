"""Mangoo's own framework surface: routing, entities, migrations, auth, and more."""

from .admin import ModelAdmin, admin_routes
from .audit import audit_events, record_audit_event
from .auth import (
    Group,
    User,
    add_user_to_group,
    authenticate,
    authenticate_session,
    common_password_validator,
    create_email_verification_token,
    create_group,
    create_password_reset_token,
    create_session,
    create_superuser,
    create_user,
    enable_totp,
    get_group_by_name,
    get_user_by_username,
    list_sessions,
    min_length_validator,
    remove_user_from_group,
    reset_password,
    revoke_all_sessions,
    revoke_session,
    rotate_session,
    user_permissions,
    validate_password,
    verify_email,
)
from .auth_api import auth_routes
from .cache import CacheBackendError, InMemoryCache, RedisCache, SqliteCache, cache_from_settings, cached_view
from .collections import MangoList, mango_list
from .forms import BooleanField, CharField, DecimalField, EmailField, Field, Form, IntegerField, entity_form
from .graphql import Schema, graphql_view
from .hardening import HardeningOptions
from .i18n import Catalog, LanguageCatalog, catalog_from_settings, resolve_language, translator_for
from .logging import JsonFormatter, configure_logging
from .mail import ConsoleEmailBackend, EmailMessage, FileEmailBackend, SMTPEmailBackend, backend_from_settings, mail_notifier, send_mail
from .messages import Message, add_message, consume_messages
from .metrics import MetricsRegistry, metrics_routes
from .migrations import apply as apply_migrations
from .migrations import generate as generate_migration
from .migrations import status as migration_status
from .models import Decimal, Entity, Integer, Key, Text, Timestamp, Toggle
from .production import health_routes, validate_production
from .request import UploadedFile
from .response import Response, json_response
from .rest import api_route
from .routing import path
from .security import csrf_protect, csrf_token, email_verified_required, has_permission_required, login_required, permission_required, superuser_required
from .serializers import EntitySerializer, Serializer
from .static import static_routes
from .swagger import ERROR_SCHEMA, openapi_document
from .templates import render_template
from .testing import AsyncTestClient, TestClient
from .uploads import save_uploaded_file
from .websocket import WebSocket, WebSocketDisconnect, WebSocketRoute, websocket_path

__all__ = [
    "AsyncTestClient",
    "BooleanField",
    "CacheBackendError",
    "Catalog",
    "CharField",
    "ConsoleEmailBackend",
    "Decimal",
    "DecimalField",
    "EmailField",
    "EmailMessage",
    "ERROR_SCHEMA",
    "Entity",
    "EntitySerializer",
    "Field",
    "FileEmailBackend",
    "Form",
    "Group",
    "HardeningOptions",
    "InMemoryCache",
    "Integer",
    "IntegerField",
    "JsonFormatter",
    "Key",
    "LanguageCatalog",
    "MangoList",
    "Message",
    "MetricsRegistry",
    "ModelAdmin",
    "RedisCache",
    "Response",
    "SMTPEmailBackend",
    "Schema",
    "Serializer",
    "SqliteCache",
    "TestClient",
    "Text",
    "Timestamp",
    "Toggle",
    "UploadedFile",
    "User",
    "WebSocket",
    "WebSocketDisconnect",
    "WebSocketRoute",
    "add_message",
    "add_user_to_group",
    "admin_routes",
    "api_route",
    "apply_migrations",
    "audit_events",
    "auth_routes",
    "authenticate",
    "authenticate_session",
    "backend_from_settings",
    "cache_from_settings",
    "cached_view",
    "catalog_from_settings",
    "common_password_validator",
    "configure_logging",
    "consume_messages",
    "create_email_verification_token",
    "create_group",
    "create_password_reset_token",
    "create_session",
    "create_superuser",
    "create_user",
    "csrf_protect",
    "csrf_token",
    "email_verified_required",
    "enable_totp",
    "entity_form",
    "generate_migration",
    "get_group_by_name",
    "get_user_by_username",
    "graphql_view",
    "has_permission_required",
    "health_routes",
    "json_response",
    "list_sessions",
    "login_required",
    "mail_notifier",
    "mango_list",
    "metrics_routes",
    "min_length_validator",
    "migration_status",
    "openapi_document",
    "path",
    "permission_required",
    "record_audit_event",
    "remove_user_from_group",
    "render_template",
    "reset_password",
    "resolve_language",
    "revoke_all_sessions",
    "revoke_session",
    "rotate_session",
    "save_uploaded_file",
    "send_mail",
    "static_routes",
    "superuser_required",
    "translator_for",
    "user_permissions",
    "validate_password",
    "validate_production",
    "verify_email",
    "websocket_path",
]
