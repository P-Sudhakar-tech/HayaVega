"""Mangoo's own typed project configuration loader."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class ProjectSettings:
    base_dir: Path
    secret_key: str
    debug: bool
    allowed_hosts: tuple[str, ...]
    installed_apps: tuple[str, ...]
    middleware: tuple[str, ...]
    root_urlconf: str
    database: dict
    server_host: str
    server_port: int
    server_engine: str
    server_tls_cert: str | None
    server_tls_key: str | None
    security: dict
    job_handlers: dict
    email: dict
    commands: dict
    password_validators: tuple[str, ...]
    module_name: str = "config.settings"

    @property
    def database_path(self) -> Path:
        """Compatibility accessor for SQLite-only integrations."""
        return Path(self.database["NAME"])


def load_settings(module_name: str = "config.settings") -> ProjectSettings:
    return load_settings_from_module(importlib.import_module(module_name))


def _normalize_section(section: object) -> dict:
    """Convert a `Settings` section value -- a nested class/object with
    lowercase attributes, or a plain dict -- into the upper-cased dict the
    rest of the framework consumes. Attributes/keys this function doesn't
    know about pass through verbatim, so a project can add its own section
    fields freely."""
    if section is None:
        return {}
    if isinstance(section, dict):
        return {str(key).upper(): value for key, value in section.items()}
    return {attribute.upper(): value for attribute, value in vars(section).items() if not attribute.startswith("_")}


def _section(config: object, name: str) -> dict:
    return _normalize_section(getattr(config, name, None))


def load_database(section: object, *, base_dir: Path | str | None = None, label: str = "database") -> dict:
    """Validate and normalize one database section into the upper-cased
    dict `mangoo.db.connect()` and every database-backed helper
    (`Collection`, `admin_routes`, `auth_routes`, migrations, ...) expects.

    `section` is a `Settings.database`-shaped nested class/object or dict
    -- including one of your own extra named sections, for a project that
    talks to more than one database (e.g. `Settings.databases.analytics`,
    a section you define yourself; Mangoo's built-in routing/admin/auth
    helpers only ever address one `database` dict each, so wiring a second
    one into a view is your own project's code, not framework config).
    `label` only affects error messages -- pass the section's own dotted
    name (e.g. "Settings.databases.analytics") so a validation error points
    at the section that's actually wrong. `base_dir` resolves a relative
    SQLite `name`; it's required only when `name` is relative.
    """
    database = _normalize_section(section)
    engine = str(database.get("ENGINE", "sqlite3")).lower()
    if engine not in {"sqlite", "sqlite3", "postgres", "postgresql", "mysql", "mariadb"}:
        raise ValueError(f"{label}.engine must be sqlite3, postgresql, mysql, or mariadb -- got {engine!r}.")
    database_name = database.get("NAME")
    if not database_name:
        raise ValueError(f"{label}.name must be configured. Add a `name` attribute to {label}.")
    database["ENGINE"] = "sqlite3" if engine == "sqlite" else engine
    if database["ENGINE"] == "sqlite3":
        database_path = Path(database_name)
        if not database_path.is_absolute():
            if base_dir is None:
                raise ValueError(f"{label}.name ({database_name!r}) is a relative path -- pass base_dir= to load_database() to resolve it, or make it absolute.")
            database_path = Path(base_dir) / database_path
        database["NAME"] = database_path
    return database


def load_settings_from_module(module: ModuleType) -> ProjectSettings:
    """Validate a project's `Settings` instance and return runtime-ready values."""
    config = getattr(module, "settings", None)
    if config is None:
        raise ValueError(f"{module.__name__}: no `settings` found. Define a `class Settings` with your project's configuration and instantiate it as `settings = Settings()` in {module.__name__}.")

    base_dir = Path(getattr(config, "base_dir", Path.cwd())).resolve()

    try:
        database = load_database(getattr(config, "database", None), base_dir=base_dir, label="Settings.database")
    except ValueError as error:
        raise ValueError(f"{module.__name__}: {error}") from error

    server = _section(config, "server")
    port = server.get("PORT", 8000)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"{module.__name__}: Settings.server.port must be an integer between 1 and 65535 -- got {port!r}. Fix Settings.server.port in {module.__name__}.")
    server_engine = server.get("ENGINE", "python")
    if server_engine not in {"python", "rust"}:
        raise ValueError(f"{module.__name__}: Settings.server.engine must be 'python' or 'rust' -- got {server_engine!r}. Fix Settings.server.engine in {module.__name__}.")

    tls_cert = server.get("TLS_CERT")
    tls_key = server.get("TLS_KEY")
    if (tls_cert is None) != (tls_key is None):
        raise ValueError(f"{module.__name__}: Settings.server.tls_cert and Settings.server.tls_key must both be set, or neither. Fix the `server` section in {module.__name__}.")
    if tls_cert is not None and server_engine != "rust":
        # Silently ignoring tls_cert/tls_key under the Python engine would
        # be a real security footgun: a developer sets these expecting
        # HTTPS and gets plaintext instead, with no error telling them so.
        # mangoo.runtime's wsgiref server has no TLS support to wire this
        # into in this version.
        raise ValueError(f"{module.__name__}: Settings.server.tls_cert/tls_key are only supported with Settings.server.engine = 'rust' (got {server_engine!r}). Set Settings.server.engine = 'rust', or remove tls_cert/tls_key, in {module.__name__}.")

    return ProjectSettings(
        base_dir=base_dir,
        secret_key=str(getattr(config, "secret_key", "")),
        debug=bool(getattr(config, "debug", False)),
        allowed_hosts=tuple(getattr(config, "allowed_hosts", [])),
        installed_apps=tuple(getattr(config, "installed_apps", [])),
        middleware=tuple(getattr(config, "middleware", [])),
        root_urlconf=getattr(config, "root_urlconf", "config.routes"),
        database=database,
        server_host=server.get("HOST", "127.0.0.1"),
        server_port=port,
        server_engine=server_engine,
        server_tls_cert=tls_cert,
        server_tls_key=tls_key,
        security=_section(config, "security"),
        job_handlers=dict(getattr(config, "job_handlers", {})),
        email=_section(config, "email"),
        commands=dict(getattr(config, "commands", {})),
        password_validators=tuple(getattr(config, "password_validators", [])),
        module_name=module.__name__,
    )
