# Mangoo: build-from-scratch guide

Mangoo is a Django-inspired web framework experiment. Its application-facing syntax is Python-like, while its intended high-throughput runtime is Rust through PyO3 native bindings.

This guide describes the project as it exists today, how to run it from a clean machine, and how to extend it safely.

## 1. What is implemented

| Area | Current state |
|---|---|
| Project and app scaffolding | Implemented: `startproject`, `startapp`, `manage.py` layout |
| Development server | Implemented: WSGI server, route dispatch, autoreload, `Ctrl+C` shutdown |
| Settings | Implemented: Django-like `settings.py`, typed loader, structured database/server settings |
| REST | Implemented: JSON routes, method checks, example health endpoint |
| GraphQL | Implemented starter: single root query field per request |
| Authentication | Implemented: scrypt passwords, lockouts, sessions, TOTP, Bearer REST endpoints |
| Databases | SQLite works now; PostgreSQL/MySQL/MariaDB adapters activate when drivers are installed |
| Rust native layer | Rust core and PyO3 `MangoList` source are present; it still needs to be compiled and packaged |

## 2. Repository layout

```text
mangoo/
|- Cargo.toml                     # Rust workspace
|- pyproject.toml                 # Python package and CLI scripts
|- mango.cmd                      # Windows no-pip source launcher
|- src/mangoo/                    # Python framework API
|  |- auth.py                     # Passwords, sessions, lockouts, TOTP
|  |- auth_api.py                 # Bearer-token REST endpoints
|  |- autoreload.py               # Development code watcher
|  |- collections.py              # mango_list native/fallback selector
|  |- conf.py                     # Typed project settings loader
|  |- db.py                       # SQLite/PostgreSQL/MySQL DB-API adapter
|  |- graphql.py                  # Starter GraphQL executor
|  |- rest.py                     # JSON REST routes
|  |- runtime.py                  # WSGI dispatcher
|  |- scaffold.py                 # Project/app generator
|  `- management/commands.py      # runserver and auth commands
|- crates/
|  |- mangoo-core/                # Framework-neutral Rust primitives
|  `- mangoo-python/              # PyO3 module named mangoo_native
|- example_site/                  # Runnable demonstration project
`- tests/                         # Standard-library unittest suite
```

## 3. Prerequisites

### Required

- Python 3.10 or later
- UV (`uv --version`)
- Rust and Cargo (`rustc --version`, `cargo --version`) for native compilation

### Windows PowerShell activation

If PowerShell blocks activation scripts, enable it only for the current window:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
```

## 4. Create the Python environment with UV

From the repository root:

```powershell
cd D:\separate_code\mangoo
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv venv .venv
uv pip install -e .
```

Activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

If you do not want to activate the environment, use the executable directly:

```powershell
& .\.venv\Scripts\mango.exe --help
```

## 5. Start the example project

```powershell
cd D:\separate_code\mangoo\example_site
& D:\separate_code\mangoo\.venv\Scripts\mango.exe launch 8011
```

Open `http://127.0.0.1:8011/`.

The development server watches Python files and reloads when code changes. Press `Ctrl+C` in the same terminal to stop it. Disable reload only when needed:

```powershell
& D:\separate_code\mangoo\.venv\Scripts\mango.exe launch 8011 --noreload
```

## 6. Create a new project and app

With the environment active:

```powershell
mango startproject mysite
cd mysite
python manage.py startapp blog
mango launch 8000
```

Generated projects have this shape:

```text
mysite/
|- manage.py
|- project/
|  |- settings.py
|  `- urls.py
`- blog/
   |- views.py
   |- urls.py
   |- models.py
   `- migrations/
```

## 7. Project settings

`project/settings.py` intentionally resembles Django but uses Mangoo-specific runtime sections.

```python
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("MANGOO_SECRET_KEY", "change-this-in-production")
DEBUG = os.getenv("MANGOO_DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

INSTALLED_APPS = []
MIDDLEWARE = []
ROOT_URLCONF = "project.urls"

DATABASES = {
    "default": {
        "ENGINE": "sqlite3",
        "NAME": BASE_DIR / "mangoo.sqlite3",
    },
}

SERVER = {"HOST": "127.0.0.1", "PORT": 8000}
```

Never commit a production `SECRET_KEY` or database password. Use environment variables.

## 8. REST APIs

Use `api_route` for JSON endpoints.

```python
from mangoo import api_route

def health(request):
    return {"status": "ok"}

urlpatterns = [api_route("api/health", health, methods=("GET",))]
```

The example endpoint is available at `GET /api/health`.

## 9. GraphQL

The current GraphQL module deliberately supports a small, predictable subset: one root query field.

```python
from mangoo import path
from mangoo.graphql import Schema, graphql_view

schema = Schema(query={"greeting": lambda request: "Hello"})
urlpatterns = [path("graphql", graphql_view(schema), methods=("POST",))]
```

Send:

```json
{"query":"{ greeting }"}
```

The next GraphQL milestone is arguments, nested selections, mutations, and a spec-compliant executor.

## 10. Authentication and authorization

### Security controls

- Passwords use salted, memory-hard scrypt hashes.
- Passwords require 12+ characters with lower/upper/numeric/symbol characters.
- Five failed attempts lock an account for 15 minutes.
- Session tokens are random Bearer values; only SHA-256 token hashes are stored.
- TOTP authenticator-app two-factor authentication is available.

### Create a superuser

```powershell
python manage.py createsuperuser
python manage.py enable2fa admin
```

`enable2fa` prints a TOTP secret. Add it to an authenticator app and keep it private.

### Bearer API endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/auth/login` | POST | Receives `username`, `password`, optional `totp_code`; returns a Bearer token |
| `/api/auth/me` | GET | Returns the current user for `Authorization: Bearer <token>` |
| `/api/auth/logout` | POST | Revokes the provided Bearer token |

Always use HTTPS before exposing these endpoints outside local development.

## 11. Databases

### SQLite

No additional driver is needed:

```python
DATABASES = {"default": {"ENGINE": "sqlite3", "NAME": BASE_DIR / "mangoo.sqlite3"}}
```

### PostgreSQL

```powershell
uv add "psycopg[binary]"
```

```python
DATABASES = {
    "default": {
        "ENGINE": "postgresql",
        "NAME": "mangoo",
        "HOST": "localhost",
        "PORT": 5432,
        "USER": "mangoo",
        "PASSWORD": os.getenv("MANGOO_DATABASE_PASSWORD"),
    }
}
```

### MySQL or MariaDB

```powershell
uv add pymysql
```

```python
DATABASES = {
    "default": {
        "ENGINE": "mysql",
        "NAME": "mangoo",
        "HOST": "localhost",
        "PORT": 3306,
        "USER": "mangoo",
        "PASSWORD": os.getenv("MANGOO_DATABASE_PASSWORD"),
    }
}
```

SQLite is fully exercised by the existing test suite. PostgreSQL and MySQL/MariaDB use the adapter and require integration testing against real servers before production use.

## 12. Rust-native features

### Mangoo-specific collection syntax

```python
from mangoo import mango_list

items = mango_list(["a", "b"])
items.append("c")
```

`mango_list` has a Python-compatible fallback today. Its Rust implementation lives in:

- `crates/mangoo-core/src/lib.rs`
- `crates/mangoo-python/src/lib.rs`

The PyO3 module exports `MangoList`. Once compiled as `mangoo_native`, Mangoo automatically selects it instead of the fallback.

### Compile the Rust workspace

Open a new PowerShell window after installing Rust so its Cargo path is available, then run:

```powershell
cd D:\separate_code\mangoo
$env:Path += ";$env:USERPROFILE\.cargo\bin"
cargo test
```

The current Python package uses Hatchling, so native-wheel packaging is the next build-system step. Add Maturin only when switching the package build backend or producing platform wheels.

## 13. Test the project

```powershell
cd D:\separate_code\mangoo
$env:PYTHONPATH = "$PWD\src"
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The test suite covers routing, REST, GraphQL starter behavior, authentication, TOTP, sessions, lockouts, database configuration, settings, scaffolding, CLI behavior, and autoreload helpers.

## 14. Recommended Rust migration order

1. Build and package `mangoo_native` with Maturin.
2. Move URL matching and path normalization into Rust.
3. Move JSON serialization and response construction into Rust.
4. Move password/session primitives into audited Rust implementations while keeping the Python API stable.
5. Add Rust database execution adapters and connection pooling.
6. Add a Rust async HTTP server behind the existing Python-facing API.
7. Benchmark each change against the Python fallback before making it the default.

## 15. Production checklist

- Use PostgreSQL or another externally managed production database.
- Set a unique, secret `MANGOO_SECRET_KEY`.
- Set `MANGOO_DEBUG=false`.
- Use HTTPS and a reverse proxy.
- Store database credentials and TOTP encryption material in a secret manager.
- Add CSRF protection, secure cookies, CORS rules, audit logs, password reset, email verification, and rate limiting at the edge before public deployment.
- Perform database-specific integration tests and a security review.

## 16. Current limitations

Mangoo is an early framework prototype, not a production replacement for Django yet. Its GraphQL executor is intentionally minimal, the ORM/migrations/admin are not yet implemented, and the Rust extension source is not yet packaged as a native wheel. Keep the public Python API stable while incrementally replacing internals with benchmarked Rust implementations.
