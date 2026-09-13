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
    security: dict
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
        security=dict(getattr(module, "SECURITY", {})),
        module_name=module.__name__,
    )
