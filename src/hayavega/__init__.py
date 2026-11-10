"""Hayavega's own framework surface: routing, entities, migrations, auth, and more.

Every public name here is resolved lazily (PEP 562 module __getattr__) on
first access instead of importing every submodule eagerly at
`import hayavega` time. The previous version imported all ~20 submodules
unconditionally, including ones with real, rarely-needed-at-boot import
cost for a typical request-serving process -- `testing` (pulls in asyncio
machinery for AsyncTestClient), `mail` (smtplib/urllib.parse), `admin`/
`security` (dis/tokenize, pulled in transitively) -- adding measurably to
cold-start time (see benchmarks/startup_time.py). A process that only ever
touches `path`/`Entity`/`Response` on its hot path no longer pays for the
other ~90 names it never imports. Behavior is unchanged: `from hayavega
import Entity` and `hayavega.Entity` both still work exactly as before --
Python's import machinery calls this module's __getattr__ automatically
whenever an attribute isn't already present on the module object.
"""

import importlib
from typing import Any

_LAZY: dict[str, tuple[str, str]] = {
    "AsyncTestClient": ("testing", "AsyncTestClient"),
    "BooleanField": ("forms", "BooleanField"),
    "CacheBackendError": ("cache", "CacheBackendError"),
    "Catalog": ("i18n", "Catalog"),
    "CharField": ("forms", "CharField"),
    "ConsoleEmailBackend": ("mail", "ConsoleEmailBackend"),
    "Decimal": ("models", "Decimal"),
    "DecimalField": ("forms", "DecimalField"),
    "ERROR_SCHEMA": ("swagger", "ERROR_SCHEMA"),
    "EmailField": ("forms", "EmailField"),
    "EmailMessage": ("mail", "EmailMessage"),
    "Entity": ("models", "Entity"),
    "EntitySerializer": ("serializers", "EntitySerializer"),
    "Field": ("forms", "Field"),
    "FileEmailBackend": ("mail", "FileEmailBackend"),
    "Form": ("forms", "Form"),
    "Group": ("auth", "Group"),
    "HardeningOptions": ("hardening", "HardeningOptions"),
    "InMemoryCache": ("cache", "InMemoryCache"),
    "Integer": ("models", "Integer"),
    "IntegerField": ("forms", "IntegerField"),
    "JsonFormatter": ("logging", "JsonFormatter"),
    "Key": ("models", "Key"),
    "LanguageCatalog": ("i18n", "LanguageCatalog"),
    "MangoList": ("collections", "MangoList"),
    "Message": ("messages", "Message"),
    "MetricsRegistry": ("metrics", "MetricsRegistry"),
    "ModelAdmin": ("admin", "ModelAdmin"),
    "RedisCache": ("cache", "RedisCache"),
    "Response": ("response", "Response"),
    "SMTPEmailBackend": ("mail", "SMTPEmailBackend"),
    "Schema": ("graphql", "Schema"),
    "Serializer": ("serializers", "Serializer"),
    "SqliteCache": ("cache", "SqliteCache"),
    "TestClient": ("testing", "TestClient"),
    "Text": ("models", "Text"),
    "Timestamp": ("models", "Timestamp"),
    "Toggle": ("models", "Toggle"),
    "UploadedFile": ("request", "UploadedFile"),
    "User": ("auth", "User"),
    "WebSocket": ("websocket", "WebSocket"),
    "WebSocketDisconnect": ("websocket", "WebSocketDisconnect"),
    "WebSocketRoute": ("websocket", "WebSocketRoute"),
    "add_message": ("messages", "add_message"),
    "add_user_to_group": ("auth", "add_user_to_group"),
    "admin_routes": ("admin", "admin_routes"),
    "api_route": ("rest", "api_route"),
    "apply_migrations": ("migrations", "apply"),
    "audit_events": ("audit", "audit_events"),
    "auth_routes": ("auth_api", "auth_routes"),
    "authenticate": ("auth", "authenticate"),
    "authenticate_session": ("auth", "authenticate_session"),
    "backend_from_settings": ("mail", "backend_from_settings"),
    "cache_from_settings": ("cache", "cache_from_settings"),
    "cached_view": ("cache", "cached_view"),
    "catalog_from_settings": ("i18n", "catalog_from_settings"),
    "common_password_validator": ("auth", "common_password_validator"),
    "configure_logging": ("logging", "configure_logging"),
    "consume_messages": ("messages", "consume_messages"),
    "create_email_verification_token": ("auth", "create_email_verification_token"),
    "create_group": ("auth", "create_group"),
    "create_password_reset_token": ("auth", "create_password_reset_token"),
    "create_session": ("auth", "create_session"),
    "create_superuser": ("auth", "create_superuser"),
    "create_user": ("auth", "create_user"),
    "csrf_protect": ("security", "csrf_protect"),
    "csrf_token": ("security", "csrf_token"),
    "email_verified_required": ("security", "email_verified_required"),
    "enable_totp": ("auth", "enable_totp"),
    "entity_form": ("forms", "entity_form"),
    "generate_migration": ("migrations", "generate"),
    "get_group_by_name": ("auth", "get_group_by_name"),
    "get_user_by_username": ("auth", "get_user_by_username"),
    "graphql_view": ("graphql", "graphql_view"),
    "has_permission_required": ("security", "has_permission_required"),
    "health_routes": ("production", "health_routes"),
    "json_response": ("response", "json_response"),
    "list_sessions": ("auth", "list_sessions"),
    "login_required": ("security", "login_required"),
    "mail_notifier": ("mail", "mail_notifier"),
    "mango_list": ("collections", "mango_list"),
    "metrics_routes": ("metrics", "metrics_routes"),
    "migration_status": ("migrations", "status"),
    "min_length_validator": ("auth", "min_length_validator"),
    "openapi_document": ("swagger", "openapi_document"),
    "path": ("routing", "path"),
    "permission_required": ("security", "permission_required"),
    "record_audit_event": ("audit", "record_audit_event"),
    "remove_user_from_group": ("auth", "remove_user_from_group"),
    "render_template": ("templates", "render_template"),
    "reset_password": ("auth", "reset_password"),
    "resolve_language": ("i18n", "resolve_language"),
    "revoke_all_sessions": ("auth", "revoke_all_sessions"),
    "revoke_session": ("auth", "revoke_session"),
    "rotate_session": ("auth", "rotate_session"),
    "save_uploaded_file": ("uploads", "save_uploaded_file"),
    "send_mail": ("mail", "send_mail"),
    "static_routes": ("static", "static_routes"),
    "superuser_required": ("security", "superuser_required"),
    "translator_for": ("i18n", "translator_for"),
    "user_permissions": ("auth", "user_permissions"),
    "validate_password": ("auth", "validate_password"),
    "validate_production": ("production", "validate_production"),
    "verify_email": ("auth", "verify_email"),
    "websocket_path": ("websocket", "websocket_path"),
}

__all__ = sorted(_LAZY)


def __getattr__(name: str) -> Any:
    try:
        module_name, attr_name = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(f".{module_name}", __name__)
    value = getattr(module, attr_name)
    globals()[name] = value  # cache on the module so repeat access skips __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY))
