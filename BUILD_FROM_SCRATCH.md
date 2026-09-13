# Mangoo: build-from-scratch guide

Mangoo is a Django-inspired web framework experiment. Its application-facing syntax is Python-like, while its intended high-throughput runtime is Rust through PyO3 native bindings.

This guide describes the project as it exists today, how to run it from a clean machine, and how to extend it safely.

## 1. What is implemented

| Area | Current state |
|---|---|
| Project and app scaffolding | Implemented: `newproject`, `newapp`, `manage.py` layout (`startproject`/`startapp` still work as deprecated aliases) |
| Development server | Implemented: WSGI server, route dispatch, autoreload, `Ctrl+C` shutdown |
| Settings | Implemented: Django-like `settings.py`, typed loader, structured database/server settings |
| REST | Implemented: JSON routes, method checks, example health endpoint |
| GraphQL | Implemented: arguments, variables, nested selections, mutations, depth/field limits, off-by-default introspection |
| UI framework | Implemented: `mangoo.ui` server-rendered components and a first-party `mangoo-ui.js` hypermedia runtime (`hx-*` swaps, `mgo-boost` routing), no build step; no client-side reactive/signals layer in this version; a Node.js/npm-based SPA framework (JSX, virtual DOM, bundler) is not yet scoped |
| Production features | Implemented baseline: scrypt passwords, lockouts, Bearer and cookie sessions, CSRF, permissions, TOTP, and admin base |
| Databases | SQLite works now; PostgreSQL/MySQL/MariaDB adapters activate when drivers are installed |
| Rust native layer | Implemented: `mangoo_native` builds and bundles automatically as part of a normal `pip install` when a Rust toolchain is present (see "Compile the Rust workspace" below); a pure-Python fallback is used otherwise |

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
|  |- graphql.py                  # Dependency-free GraphQL executor
|  |- rest.py                     # JSON REST routes
|  |- runtime.py                  # WSGI dispatcher
|  |- scaffold.py                 # Project/app generator
|  |- ui/                         # Components + hypermedia runtime (mangoo-ui.js/.css)
|  `- management/commands.py      # launch and auth commands
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
mango newproject mysite
cd mysite
python manage.py newapp blog
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

The GraphQL module is a small, dependency-free executor -- not a full
spec-compliant engine -- supporting arguments, variables, nested selections,
mutations, depth/field limits, and an off-by-default introspection policy.
It deliberately does not support fragments or directives (rejected with a
clear error) since there is no declared type system to validate a selection
against; nested values are projected dynamically from whatever a resolver
returns.

```python
from mangoo import path
from mangoo.graphql import Schema, graphql_view


def users(request, active=None):
    all_users = [{"id": 1, "name": "Ada", "team": {"name": "Core"}}]
    return [u for u in all_users if active is None or u.get("active") == active]


def create_user(request, name):
    return {"id": 99, "name": name}


schema = Schema(query={"users": users}, mutation={"createUser": create_user})
urlpatterns = [path("graphql", graphql_view(schema), methods=("POST",))]
```

Send a nested query with a variable:

```json
{"query": "query($active: Boolean) { users(active: $active) { id name team { name } } }", "variables": {"active": true}}
```

Or a mutation:

```json
{"query": "mutation { createUser(name: \"Linus\") { id name } }"}
```

See [README.md](README.md#rest-and-graphql) for the full list of what is and
isn't supported, including the introspection policy and query limits.

## 10. UI framework

`mangoo.ui` is a server-rendered component system plus a first-party
htmx-style hypermedia runtime, both living inside this package -- no npm,
no Node.js, no bundler, no separate frontend project. A component is a
plain Python function returning `Markup`:

```python
from mangoo import path
from mangoo.response import Response
from mangoo.ui import Alert, Button, Page, ui_routes


def home(request):
    body = Page(
        "Home",
        Alert("Welcome back!", variant="success"),
        Button("Refresh", hx_get="/fragment", hx_target="#content"),
        stylesheets=["/mangoo-ui/mangoo-ui.css"],
        scripts=["/mangoo-ui/mangoo-ui.js"],
    )
    return Response(body)


def fragment(request):
    return Response("<div id='content'>Refreshed.</div>")


urlpatterns = [
    path("", home),
    path("fragment", fragment),
    *ui_routes(),  # mounts /mangoo-ui/mangoo-ui.js and /mangoo-ui/mangoo-ui.css
]
```

`hx-get`/`hx-post`/`hx-put`/`hx-delete` fetch a URL (on click for
buttons/links, on submit for forms) and swap the returned HTML fragment
into `hx-target` using `hx-swap` -- the HTMX pattern, implemented in one
small dependency-free JS file. It reads the `mangoo_csrf` cookie and sends
it as `X-Csrf-Token` automatically, so an `hx-post` to a
`@csrf_protect`-guarded view is protected without extra client-side
wiring. Run the example site (`./mango.cmd launch 8001` from
`example_site/`) and visit `/ui-demo` for a runnable version of this
pattern, or see `tests/test_ui_hypermedia.py` for the full round trip
proven against the real WSGI stack. `mgo-boost` adds client-side routing
on top of the same runtime (`tests/test_ui_boost.py`).

A prior version of this package also had a client-side signals/reactivity
runtime (`mangoo-reactive.js`, `mgo-data`/`mgo-text`/`mgo-on-*`
directives); it was removed along with the rest of `mangoo.ui` at one
point and, on rebuilding, was deliberately not brought back, keeping this
version to one simpler hypermedia model rather than two rendering
approaches. A genuine Node.js/npm-based SPA framework (JSX, a virtual DOM,
a bundler) remains a distinct, separate, unscoped possibility; see
`UPCOMING_IMPROVEMENT_ROADMAP.md`.

Rust acceleration was benchmarked specifically for fragment rendering (the
small, fixed-shape markup an `hx-get` swap renders) and found to be a real
win there (4x-8x faster in isolation) -- unlike full-page template
rendering, where every general-purpose Rust candidate this project tried
lost to Python. It isn't wired into `mangoo.ui`'s shipped API, since a
hardcoded native renderer only ever covers one exact fragment shape;
see `benchmarks/native_htmx_fragment.py` and
`benchmarks/mangoo_app/app.py`'s `posts_fragment_native` for the
measurement and a worked example.

## 11. Authentication and authorization

### Security controls

- Passwords use salted, memory-hard scrypt hashes.
- Passwords require 12+ characters with lower/upper/numeric/symbol characters.
- Five failed attempts lock an account for 15 minutes.
- Session tokens are random Bearer values; only SHA-256 token hashes are stored.
- TOTP authenticator-app two-factor authentication is available.

### Create a superuser

```powershell
python manage.py createadmin
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

### Cookie sessions and CSRF

Successful login also sets a host-only, `HttpOnly`, `SameSite=Lax` `mangoo_session` cookie. Browser clients should fetch `GET /api/auth/csrf`, which returns a token and sets the `mangoo_csrf` cookie. Send that token in the `X-CSRF-Token` header for unsafe requests made with the session cookie (including logout). Bearer-token clients are not subject to CSRF checks because browsers do not attach Bearer headers automatically. Pass `secure_cookies=not DEBUG` to `auth_routes` (as the example site does) so production cookies also receive the `Secure` attribute.

### Permissions and admin base

Use `login_required`, `superuser_required`, or `permission_required` to protect a view. The built-in admin base can be enabled with `*admin_routes(DATABASES['default'])`; it always provides administrator-only `/admin` and `/admin/users` pages, and can additionally manage your own entities. Authenticate the browser session first by calling the login endpoint.

```python
from mangoo import admin_routes, login_required, path
from mangoo.admin import ModelAdmin
from mangoo.models import Entity, Integer, Text


class Product(Entity):
    name = Text()
    price = Integer()


@login_required(DATABASES["default"])
def account(request):
    return {"detail": "private"}


urlpatterns = [
    path("account", account),
    *admin_routes(DATABASES["default"], models=[ModelAdmin(Product)]),
]
```

This adds paginated list/add/edit/delete pages at `/admin/product`. Each
registered model gets:

- **Pagination**: `?page=N` on the list view; `ModelAdmin(Product, page_size=25)` to change the page size.
- **Validation**: a required-but-blank or malformed field (e.g. text in an `Integer` column) is a form error, not a 500.
- **Permissions**: `ModelAdmin(Product, can_access=lambda user: user.is_staff)` to gate a model on something other than `is_superuser`.
- **Change history**: the edit page lists every create/update (with the changed field names, not values)/delete recorded against that record, built on the existing audit log.

CSRF protection applies to every add/edit/delete submission the same way it
does for `auth_routes`: a hidden `csrf_token` form field (plain HTML forms
can't set a custom header) is checked against the `mangoo_csrf` cookie set
when the form was rendered. Bearer-token requests skip the check, same as
elsewhere.

## 12. Databases

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
uv add pymysql cryptography
```

`cryptography` is required by `pymysql` to authenticate against MySQL 8's
default `caching_sha2_password` plugin -- every connection fails without it.

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

## 13. Rust-native features

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

The Python package still uses Hatchling, not Maturin: a custom build hook
(`hatch_build.py` at the repository root) invokes `cargo build --release`
against `crates/mangoo-python/Cargo.toml` during a normal `pip install .` or
`pip install -e .`, and bundles the resulting `mangoo_native` extension into
that same install when `cargo` is on PATH. There is no separate manual build
step, and no risk of a second, independently-invokable build overwriting the
`mangoo` package, since only one build definition produces it.

## 14. Test the project

```powershell
cd D:\separate_code\mangoo
$env:PYTHONPATH = "$PWD\src"
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The test suite covers routing, REST, GraphQL starter behavior, authentication, TOTP, sessions, lockouts, database configuration, settings, scaffolding, CLI behavior, and autoreload helpers.

## 15. Recommended Rust migration order

1. ~~Build and package `mangoo_native` as part of a normal packaged install.~~ Done: see "Compile the Rust workspace" above.
2. Move URL matching and path normalization into Rust.
3. Move JSON serialization and response construction into Rust.
4. Move password/session primitives into audited Rust implementations while keeping the Python API stable.
5. Add Rust database execution adapters and connection pooling.
6. Add a Rust async HTTP server behind the existing Python-facing API.
7. Benchmark each change against the Python fallback before making it the default.

## 16. Production checklist

- Configure a `SECURITY` section in `settings.py`. Mangoo applies trusted-host validation, security headers, structured audit logs, an explicit CORS allow-list, and an in-memory per-client/per-path rate limit through this configuration.
- Keep `ALLOWED_ORIGINS` empty unless a browser application must call the API from another origin. Never use a wildcard for authenticated browser APIs.
- The bundled rate limiter is process-local. Replace or complement it with a shared edge or Redis-backed limiter when running more than one server process.
- Use PostgreSQL or another externally managed production database.
- Set a unique, secret `MANGOO_SECRET_KEY`.
- Set `MANGOO_DEBUG=false`.
- Use HTTPS and a reverse proxy.
- Store database credentials and TOTP encryption material in a secret manager.
- Enable `secure_cookies=True` for session and CSRF cookies when serving HTTPS. Add CORS rules, audit logs, password reset, email verification, and rate limiting at the edge.
- Perform database-specific integration tests and a security review.

Example hardening configuration:

```python
SECURITY = {
    "ALLOWED_ORIGINS": ["https://app.example.com"],
    "RATE_LIMIT": 120,
    "RATE_WINDOW_SECONDS": 60,
    "SECURITY_HEADERS": True,
    "FORCE_HTTPS": True,
    "TRUST_PROXY_HEADERS": True,  # only behind your trusted reverse proxy
    "MAX_REQUEST_BYTES": 1_048_576,
}
```

The runtime rejects requests whose `Host` is not in `ALLOWED_HOSTS`, rejects oversized request bodies before reading them, responds to trusted CORS preflight requests, and adds CSP, frame, MIME-sniffing, referrer-policy, permissions-policy, and cross-origin isolation headers. `FORCE_HTTPS` redirects plain HTTP and enables HSTS on secure requests. Set `TRUST_PROXY_HEADERS` only when a trusted reverse proxy removes client-supplied forwarding headers. Audit records are emitted to the `mangoo.audit` logger without request bodies, credentials, or query strings.

## 17. Current limitations

Mangoo is an early framework prototype, not a production replacement for Django yet. Templates support inheritance, loops, and conditionals but no fragments, filters, or macros; the admin's registered-model CRUD has no bulk actions, list filtering, or search; GraphQL has no fragments or directives and only a minimal, off-by-default introspection; `mangoo.ui` has no client-side reactive/signals layer in this version (see section 10) and there is still no Node.js/npm-based SPA option; and most of the runtime (routing, JSON serialization, authentication, database execution) is still Python, with Rust migrations for those pending a measured benchmark -- fragment rendering is the one exception with a proven, benchmarked win (section 10), not yet wired into the shipped API since it only covers one exact fragment shape per hardcoded renderer. Keep the public Python API stable while incrementally replacing internals with benchmarked Rust implementations.
