from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def startproject(name: str, target: Path | None = None) -> Path:
    root = (target or Path.cwd()) / name
    if root.exists():
        raise FileExistsError(f"Project already exists: {root}")
    _write(root / "manage.py", "from mangoo.management.commands import execute_from_command_line\n\nif __name__ == '__main__':\n    execute_from_command_line()\n")
    _write(root / "project" / "__init__.py", "")
    _write(
        root / "project" / "settings.py",
        "from pathlib import Path\nimport os\n\nBASE_DIR = Path(__file__).resolve().parent.parent\nSECRET_KEY = os.getenv('MANGOO_SECRET_KEY', 'change-this-in-production')\nDEBUG = os.getenv('MANGOO_DEBUG', 'true').lower() == 'true'\nALLOWED_HOSTS = ['127.0.0.1', 'localhost']\n\nINSTALLED_APPS = []\nMIDDLEWARE = []\nROOT_URLCONF = 'project.urls'\n\nDATABASES = {\n    'default': {\n        'ENGINE': 'sqlite3',\n        'NAME': BASE_DIR / 'mangoo.sqlite3',\n    },\n}\n\nSERVER = {\n    'HOST': '127.0.0.1',\n    'PORT': 8000,\n}\n",
    )
    _write(
        root / "project" / "urls.py",
        "from mangoo import Response, api_route, path\nfrom mangoo.auth_api import auth_routes\nfrom mangoo.graphql import Schema, graphql_view\nfrom .settings import DATABASES\n\ndef home(request):\n    return Response('<h1>Welcome to Mangoo</h1><p>Your Rust-backed project is running.</p>')\n\ndef health(request):\n    return {'status': 'ok'}\n\nschema = Schema(query={'greeting': lambda request: 'Hello from Mangoo GraphQL'})\n\nurlpatterns = [path('', home), api_route('api/health', health), path('graphql', graphql_view(schema), methods=('POST',)), *auth_routes(DATABASES['default'])]\n",
    )
    _write(
        root / "project" / "wsgi.py",
        (
            '"""WSGI entry point for production servers -- gunicorn, waitress, uWSGI, etc.\n\n'
            "`mango launch`/`manage.py launch` is for development only; point a real\n"
            "server at the `application` object below instead. For example:\n\n"
            "    gunicorn project.wsgi:application --workers 5 --bind 0.0.0.0:8000   # Linux/macOS\n"
            "    waitress-serve --port=8000 project.wsgi:application                 # any platform, incl. Windows\n\n"
            "See DEPLOYMENT.md for worker-count guidance and the gunicorn/waitress split.\n"
            '"""\n'
            "from mangoo.conf import load_settings\n"
            "from mangoo.hardening import HardeningOptions\n"
            "from mangoo.runtime import create_application\n\n"
            "settings = load_settings('project.settings')\n"
            "urls = __import__(settings.root_urlconf, fromlist=['urlpatterns'])\n\n"
            "application = create_application(\n"
            "    urls.urlpatterns,\n"
            "    hardening=HardeningOptions.from_settings(allowed_hosts=settings.allowed_hosts, security=settings.security),\n"
            "    debug=settings.debug,\n"
            ")\n"
        ),
    )
    _write(
        root / "project" / "asgi.py",
        (
            '"""ASGI entry point -- opt in only if a view needs `async def` for\n'
            "concurrent I/O; the WSGI path above (`project/wsgi.py`) remains the\n"
            "default and does not require this file. Point an ASGI server at the\n"
            "`application` object below. For example:\n\n"
            "    uvicorn project.asgi:application --workers 4 --host 0.0.0.0 --port 8000\n"
            "    hypercorn project.asgi:application --workers 4 --bind 0.0.0.0:8000\n\n"
            "See DEPLOYMENT.md for when ASGI is worth the switch and when it isn't.\n"
            '"""\n'
            "from mangoo.asgi import create_asgi_application\n"
            "from mangoo.conf import load_settings\n"
            "from mangoo.hardening import HardeningOptions\n\n"
            "settings = load_settings('project.settings')\n"
            "urls = __import__(settings.root_urlconf, fromlist=['urlpatterns'])\n\n"
            "application = create_asgi_application(\n"
            "    urls.urlpatterns,\n"
            "    hardening=HardeningOptions.from_settings(allowed_hosts=settings.allowed_hosts, security=settings.security),\n"
            "    debug=settings.debug,\n"
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
    _write(root / "views.py", "from mangoo import Response\n\ndef index(request):\n    return Response('<h1>App is ready</h1>')\n")
    _write(root / "urls.py", "from mangoo import path\nfrom .views import index\n\nurlpatterns = [path('', index)]\n")
    _write(root / "models.py", "# Define application models here.\n")
    _write(root / "admin.py", "# Register models for the future admin here.\n")
    _write(root / "migrations" / "__init__.py", "")
    return root
