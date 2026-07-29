"""Django-inspired, typed project configuration for Mangoo."""
from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
from types import ModuleType


@dataclass(frozen=True)
class ProjectSettings:
    base_dir: Path
    debug: bool
    allowed_hosts: tuple[str, ...]
    installed_apps: tuple[str, ...]
    middleware: tuple[str, ...]
    root_urlconf: str
    database: dict
    server_host: str
    server_port: int
    security: dict

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
        raise ValueError("DATABASES['default']['ENGINE'] must be sqlite3, postgresql, mysql, or mariadb.")
    database_name = default_database.get("NAME")
    if not database_name:
        raise ValueError("DATABASES['default']['NAME'] must be configured.")
    database = dict(default_database)
    database["ENGINE"] = "sqlite3" if engine == "sqlite" else engine
    if engine in {"sqlite", "sqlite3"}:
        database_path = Path(database_name)
        database["NAME"] = database_path if database_path.is_absolute() else base_dir / database_path

    server = getattr(module, "SERVER", {})
    port = server.get("PORT", 8000)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("SERVER['PORT'] must be an integer between 1 and 65535.")

    return ProjectSettings(
        base_dir=base_dir,
        debug=bool(getattr(module, "DEBUG", False)),
        allowed_hosts=tuple(getattr(module, "ALLOWED_HOSTS", [])),
        installed_apps=tuple(getattr(module, "INSTALLED_APPS", [])),
        middleware=tuple(getattr(module, "MIDDLEWARE", [])),
        root_urlconf=getattr(module, "ROOT_URLCONF", "project.urls"),
        database=database,
        server_host=server.get("HOST", "127.0.0.1"),
        server_port=port,
        security=dict(getattr(module, "SECURITY", {})),
    )
