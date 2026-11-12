from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def startproject(name: str, target: Path | None = None) -> Path:
    root = (target or Path.cwd()) / name
    if root.exists():
        raise FileExistsError(f"Project already exists: {root}")
    _write(root / "manage.py", "from hayavega.management.commands import execute_from_command_line\n\nif __name__ == '__main__':\n    execute_from_command_line()\n")
    _write(root / "config" / "__init__.py", "")
    _write(
        root / "config" / "settings.py",
        (
            "from pathlib import Path\n"
            "import os\n\n"
            "BASE_DIR = Path(__file__).resolve().parent.parent\n\n\n"
            "class Settings:\n"
            "    base_dir = BASE_DIR\n"
            "    secret_key = os.getenv('HAYAVEGA_SECRET_KEY', 'change-this-in-production')\n"
            "    debug = os.getenv('HAYAVEGA_DEBUG', 'true').lower() == 'true'\n"
            "    allowed_hosts = ['127.0.0.1', 'localhost']\n\n"
            "    installed_apps = []\n"
            "    middleware = []\n"
            "    root_urlconf = 'config.routes'\n\n"
            "    class database:\n"
            "        engine = 'sqlite3'\n"
            "        name = BASE_DIR / 'hayavega.sqlite3'\n\n"
            "    class server:\n"
            "        host = '127.0.0.1'\n"
            "        port = 8000\n\n\n"
            "settings = Settings()\n"
        ),
    )
    _write(
        root / "config" / "routes.py",
        "from hayavega import Response, api_route, path\nfrom hayavega.admin import admin_routes\nfrom hayavega.auth_api import auth_routes\nfrom hayavega.conf import load_settings\nfrom hayavega.graphql import Schema, graphql_view\n\n_settings = load_settings()\n\ndef home(request):\n    return Response('<h1>Welcome to Hayavega</h1><p>Your Rust-backed project is running.</p>')\n\ndef health(request):\n    return {'status': 'ok'}\n\nschema = Schema(query={'greeting': lambda request: 'Hello from Hayavega GraphQL'})\n\nurlpatterns = [path('', home), api_route('api/health', health), path('graphql', graphql_view(schema), methods=('POST',)), *auth_routes(_settings.database, secret_key=_settings.secret_key), *admin_routes(_settings.database)]\n",
    )
    _write(
        root / "config" / "wsgi.py",
        (
            '"""WSGI entry point for production servers -- gunicorn, waitress, uWSGI, etc.\n\n'
            "`hayavega launch`/`manage.py launch` is for development only; point a real\n"
            "server at the `application` object below instead. For example:\n\n"
            "    gunicorn config.wsgi:application --workers 5 --bind 0.0.0.0:8000   # Linux/macOS\n"
            "    waitress-serve --port=8000 config.wsgi:application                 # any platform, incl. Windows\n\n"
            "See DEPLOYMENT.md for worker-count guidance and the gunicorn/waitress split.\n"
            '"""\n'
            "from hayavega.conf import load_settings\n"
            "from hayavega.hardening import HardeningOptions\n"
            "from hayavega.middleware import load_middleware\n"
            "from hayavega.runtime import create_application\n\n"
            "settings = load_settings('config.settings')\n"
            "urls = __import__(settings.root_urlconf, fromlist=['urlpatterns'])\n\n"
            "application = create_application(\n"
            "    urls.urlpatterns,\n"
            "    hardening=HardeningOptions.from_settings(allowed_hosts=settings.allowed_hosts, security=settings.security),\n"
            "    debug=settings.debug,\n"
            "    middleware=load_middleware(settings.middleware),\n"
            ")\n"
        ),
    )
    _write(
        root / "config" / "asgi.py",
        (
            '"""ASGI entry point -- opt in only if a view needs `async def` for\n'
            "concurrent I/O; the WSGI path above (`config/wsgi.py`) remains the\n"
            "default and does not require this file. Point an ASGI server at the\n"
            "`application` object below. For example:\n\n"
            "    uvicorn config.asgi:application --workers 4 --host 0.0.0.0 --port 8000\n"
            "    hypercorn config.asgi:application --workers 4 --bind 0.0.0.0:8000\n\n"
            "See DEPLOYMENT.md for when ASGI is worth the switch and when it isn't.\n"
            '"""\n'
            "from hayavega.asgi import create_asgi_application\n"
            "from hayavega.conf import load_settings\n"
            "from hayavega.hardening import HardeningOptions\n"
            "from hayavega.middleware import load_middleware\n\n"
            "settings = load_settings('config.settings')\n"
            "urls = __import__(settings.root_urlconf, fromlist=['urlpatterns'])\n\n"
            "application = create_asgi_application(\n"
            "    urls.urlpatterns,\n"
            "    hardening=HardeningOptions.from_settings(allowed_hosts=settings.allowed_hosts, security=settings.security),\n"
            "    debug=settings.debug,\n"
            "    middleware=load_middleware(settings.middleware),\n"
            ")\n"
        ),
    )
    return root


def startapp(name: str, target: Path | None = None) -> Path:
    root = (target or Path.cwd()) / name
    if root.exists():
        raise FileExistsError(f"App already exists: {root}")
    _write(root / "__init__.py", "")
    _write(root / "apps.py", f"class {name.title()}Config:\n    name = '{name}'\n")
    _write(root / "views.py", "from hayavega import Response\n\ndef index(request):\n    return Response('<h1>App is ready</h1>')\n")
    _write(root / "routes.py", "from hayavega import path\nfrom .views import index\n\nurlpatterns = [path('', index)]\n")
    _write(root / "models.py", "# Define application models here.\n")
    _write(root / "admin.py", "# Register models for the future admin here.\n")
    _write(root / "migrations" / "__init__.py", "")
    return root
