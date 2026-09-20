"""Django-inspired, typed project configuration for Mangoo."""

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
    module_name: str = "project.settings"

    @property
    def database_path(self) -> Path:
        """Compatibility accessor for SQLite-only integrations."""
        return Path(self.database["NAME"])


def load_settings(module_name: str = "project.settings") -> ProjectSettings:
    return load_settings_from_module(importlib.import_module(module_name))


def load_settings_from_module(module: ModuleType) -> ProjectSettings:
    """Validate a project's settings module and return runtime-ready values."""
    base_dir = Path(getattr(module, "BASE_DIR", Path.cwd())).resolve()
    databases = getattr(module, "DATABASES", {})
    default_database = databases.get("default", {})
    engine = str(default_database.get("ENGINE", "sqlite3")).lower()
    if engine not in {"sqlite", "sqlite3", "postgres", "postgresql", "mysql", "mariadb"}:
        raise ValueError(f"{module.__name__}: DATABASES['default']['ENGINE'] must be sqlite3, postgresql, mysql, or mariadb -- got {engine!r}. Fix DATABASES['default']['ENGINE'] in {module.__name__}.")
    database_name = default_database.get("NAME")
    if not database_name:
        raise ValueError(f"{module.__name__}: DATABASES['default']['NAME'] must be configured. Add a NAME entry to DATABASES['default'] in {module.__name__}.")
    database = dict(default_database)
    database["ENGINE"] = "sqlite3" if engine == "sqlite" else engine
    if engine in {"sqlite", "sqlite3"}:
        database_path = Path(database_name)
        database["NAME"] = database_path if database_path.is_absolute() else base_dir / database_path

    server = getattr(module, "SERVER", {})
    port = server.get("PORT", 8000)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"{module.__name__}: SERVER['PORT'] must be an integer between 1 and 65535 -- got {port!r}. Fix SERVER['PORT'] in {module.__name__}.")
    engine = server.get("ENGINE", "python")
    if engine not in {"python", "rust"}:
        raise ValueError(f"{module.__name__}: SERVER['ENGINE'] must be 'python' or 'rust' -- got {engine!r}. Fix SERVER['ENGINE'] in {module.__name__}.")

    tls_cert = server.get("TLS_CERT")
    tls_key = server.get("TLS_KEY")
    if (tls_cert is None) != (tls_key is None):
        raise ValueError(f"{module.__name__}: SERVER['TLS_CERT'] and SERVER['TLS_KEY'] must both be set, or neither. Fix SERVER in {module.__name__}.")
    if tls_cert is not None and engine != "rust":
        # Silently ignoring TLS_CERT/TLS_KEY under the Python engine would
        # be a real security footgun: a developer sets these expecting
        # HTTPS and gets plaintext instead, with no error telling them so.
        # mangoo.runtime's wsgiref server has no TLS support to wire this
        # into in this version.
        raise ValueError(f"{module.__name__}: SERVER['TLS_CERT']/SERVER['TLS_KEY'] are only supported with SERVER['ENGINE'] = 'rust' (got {engine!r}). Set SERVER['ENGINE'] = 'rust', or remove TLS_CERT/TLS_KEY, in {module.__name__}.")

    return ProjectSettings(
        base_dir=base_dir,
        secret_key=str(getattr(module, "SECRET_KEY", "")),
        debug=bool(getattr(module, "DEBUG", False)),
        allowed_hosts=tuple(getattr(module, "ALLOWED_HOSTS", [])),
        installed_apps=tuple(getattr(module, "INSTALLED_APPS", [])),
        middleware=tuple(getattr(module, "MIDDLEWARE", [])),
        root_urlconf=getattr(module, "ROOT_URLCONF", "project.urls"),
        database=database,
        server_host=server.get("HOST", "127.0.0.1"),
        server_port=port,
        server_engine=engine,
        server_tls_cert=tls_cert,
        server_tls_key=tls_key,
        security=dict(getattr(module, "SECURITY", {})),
        job_handlers=dict(getattr(module, "JOB_HANDLERS", {})),
        module_name=module.__name__,
    )
