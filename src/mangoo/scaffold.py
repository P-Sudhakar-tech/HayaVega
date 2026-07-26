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
    _write(root / "project" / "settings.py", "from pathlib import Path\nimport os\n\nBASE_DIR = Path(__file__).resolve().parent.parent\nSECRET_KEY = os.getenv('MANGOO_SECRET_KEY', 'change-this-in-production')\nDEBUG = os.getenv('MANGOO_DEBUG', 'true').lower() == 'true'\nALLOWED_HOSTS = ['127.0.0.1', 'localhost']\n\nINSTALLED_APPS = []\nMIDDLEWARE = []\nROOT_URLCONF = 'project.urls'\n\nDATABASES = {\n    'default': {\n        'ENGINE': 'sqlite3',\n        'NAME': BASE_DIR / 'mangoo.sqlite3',\n    },\n}\n\nSERVER = {\n    'HOST': '127.0.0.1',\n    'PORT': 8000,\n}\n")
    _write(root / "project" / "urls.py", "from mangoo import Response, api_route, path\nfrom mangoo.auth_api import auth_routes\nfrom mangoo.graphql import Schema, graphql_view\nfrom .settings import DATABASES\n\ndef home(request):\n    return Response('<h1>Welcome to Mangoo</h1><p>Your Rust-backed project is running.</p>')\n\ndef health(request):\n    return {'status': 'ok'}\n\nschema = Schema(query={'greeting': lambda request: 'Hello from Mangoo GraphQL'})\n\nurlpatterns = [path('', home), api_route('api/health', health), path('graphql', graphql_view(schema), methods=('POST',)), *auth_routes(DATABASES['default'])]\n")
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
