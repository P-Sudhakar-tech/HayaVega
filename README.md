# Mangoo

Mangoo is an early, runnable experiment in a Django-like developer experience backed by a Rust core.

## What works now

- `startproject` and `startapp` scaffolding
- Django-style `manage.py`, `settings.py`, and `urls.py`
- A development server and Python views
- A small PyO3 native module (`mangoo_native`) for the Rust/Python boundary

## Quick start

```powershell
cd D:\separate_code\mangoo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
mangoo-admin startproject mysite
cd mysite
python manage.py startapp blog
python manage.py runserver
```

Open `http://127.0.0.1:8000/`.

## Settings

Each project has a Django-familiar `project/settings.py`, but Mangoo uses its own explicit runtime sections:

- `BASE_DIR`, `SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS` for project identity and environment behavior.
- `INSTALLED_APPS`, `MIDDLEWARE`, and `ROOT_URLCONF` for application composition.
- `DATABASES['default']` for the SQLite database location.
- `SERVER` for the development host and port.

For example, change the default development port in `SERVER['PORT']`, or override it temporarily with `python manage.py runserver 8001`.

## Databases

Mangoo's database layer accepts SQLite, PostgreSQL, MySQL, and MariaDB through Django-style `DATABASES['default']` configuration. SQLite works with Python alone. For other engines, add the driver with UV before starting the project:

```powershell
uv add "psycopg[binary]"   # PostgreSQL
uv add pymysql              # MySQL or MariaDB
```

Example PostgreSQL configuration:

```python
DATABASES = {
    "default": {
        "ENGINE": "postgresql",
        "NAME": "mangoo",
        "HOST": "localhost",
        "PORT": 5432,
        "USER": "mangoo",
        "PASSWORD": os.getenv("MANGOO_DATABASE_PASSWORD"),
    },
}
```

## Migrations and transactions

Mangoo uses small, ordered SQL migrations. Add a numbered file such as
`blog/migrations/0001_products.py` to an installed app:

```python
operations = [
    "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
]
```

Run `python manage.py migrate` to apply pending migrations, or
`python manage.py showmigrations` to inspect their state. Each migration runs
in its own transaction; if any operation fails, neither its schema changes nor
its history row are committed. Applied files are checksummed, so changed or
missing migration files are rejected instead of silently proceeding.

For an app with declared `Entity` classes, create the reviewed initial table
migration with `python manage.py makemigrations blog` (after adding `"blog"`
to `INSTALLED_APPS`). Later invocations generate only nullable `ADD COLUMN`
operations and record a dependency on the preceding migration. New tables,
non-nullable fields, field alterations, and removals must remain explicit,
reviewed SQL migrations.

## Production validation and probes

Before starting with `DEBUG = False`, Mangoo requires a non-default
`SECRET_KEY`, explicit `ALLOWED_HOSTS`, and `SECURITY` settings for
`FORCE_HTTPS` and `SECURE_COOKIES`. Add `*health_routes(DATABASES['default'])`
to your URL patterns to expose `GET /healthz` (liveness) and `GET /readyz`
(database readiness) for your deployment platform.

## Rate limiting

The built-in limiter is process-local and intended for development or a small
single-instance deployment. Set `SECURITY['RATE_LIMIT_MAX_CLIENTS']` to bound
tracked client identities (default: 10,000); idle clients are pruned and the
least-recently-used identity is evicted when full. Use an edge proxy or Redis
rate limiter for coordinated limits across multiple application instances.

## Observability

Every response carries an `X-Request-ID`; a valid incoming ID is preserved,
otherwise Mangoo generates one. Audit logs contain the ID, method, path,
status, and duration without request headers. Enable JSON output in an
application entrypoint with `configure_logging(json_output=True)`.

## Reverse proxies

Set `SECURITY['TRUST_PROXY_HEADERS'] = True` only with an explicit
`SECURITY['TRUSTED_PROXY_IPS']` list (IP addresses or CIDR networks). Mangoo
then honors forwarded client and HTTPS headers only from those proxies. HTTPS
redirects retain the original query string.

## Audit events

Mangoo persists privacy-safe authentication and administration events in
`mangoo_audit_events`. Use `audit_events(DATABASES['default'])` for operational
review. Events contain an actor ID, event type, timestamp, and small metadata;
passwords, session tokens, and request headers are never stored.

See [DEPLOYMENT.md](DEPLOYMENT.md) for reverse-proxy, HTTPS, backup, restore,
and rollback procedures, and [SECURITY_REVIEW_CHECKLIST.md](SECURITY_REVIEW_CHECKLIST.md)
for release-review controls. PostgreSQL integration tests are opt-in: set
`MANGOO_TEST_POSTGRES_NAME` plus optional host, port, user, and password
variables before running the test suite. MySQL/MariaDB coverage remains planned.

## Mangoo Pulse

The opt-in in-process event bus is available under `mangoo.addons.pulse`.
Applications explicitly create a `PulseBus`, subscribe to named events, and
publish events. Delivery is deterministic and isolates subscriber failures.
Pass the same `PulseBus` as `pulse=` to `create_application`, `auth_routes`,
`admin_routes`, or `migrations.apply` to receive `request.*`, `auth.*`,
`admin.action`, and `migration.applied` events from those components; nothing
changes for applications that do not pass one. See
`src/mangoo/addons/pulse/README.md` for its full API and delivery semantics.

## REST and GraphQL

Mangoo includes a small dependency-free API layer. The bundled example exposes:

- `GET /api/health` - JSON REST health response.
- `POST /graphql` - GraphQL JSON endpoint. Send `{"query":"{ greeting }"}`.

Use `api_route('api/items', view, methods=('GET', 'POST'))` for REST endpoints. Define GraphQL fields with `Schema(query={'field': resolver})` and mount them with `path('graphql', graphql_view(schema), methods=('POST',))`.

## Built-in Swagger documentation

Every Mangoo application automatically exposes an OpenAPI 3 document at `/openapi.json` and an interactive Swagger UI at `/docs`. REST endpoints declared with `api_route` are added to the document automatically. The documentation routes are reserved only when an application has not defined routes with those paths itself.

## Development autoreload

Mangoo can launch directly from this source checkout without pip. From the repository root, run `./mango.cmd launch 8001`. It watches Python files in the project and automatically restarts the development server after code changes. Use `./mango.cmd launch 8001 --noreload` to disable this behavior.

If you later install Mangoo as a package, `mango launch 8001` is also available as a terminal command.

Press `Ctrl+C` in the same terminal to stop the server cleanly, including when autoreload is enabled.

## MVP polish

Render escaped templates with `render_template("page.html", context, directories=(BASE_DIR / "templates",))`, and expose development assets with `*static_routes(BASE_DIR / "static")`. The included `TestClient` exercises a WSGI application without a network socket. Unhandled view errors are logged to `mangoo.runtime` and return a safe `500` response without a traceback when debug mode is disabled.

## Rust-native Mangoo types

Use Mangoo-specific types where performance-sensitive code benefits from a Rust implementation:

```python
from mangoo import mango_list

items = mango_list(["first", "second"])
items.append("third")
```

`mango_list` remains usable without a Rust toolchain through a compatible fallback. When the `mangoo_native` PyO3 extension is built, it automatically becomes a Rust-owned collection. The next Rust migration targets are request routing, JSON serialization, authentication operations, and database execution.

## Declarative data entities

Mangoo uses modern entity definitions and distinct query names rather than copying Django's ORM API:

```python
from mangoo import Decimal, Entity, Text

class Product(Entity):
    name = Text(unique=True)
    price = Decimal()

products = Product.collection(DATABASES["default"])
products.append(name="Notebook", price=9.5)
items = products.seek(price__above=5).arrange("-price").window(20).fetch()
```

`collection`, `seek`, `arrange`, `window`, `fetch`, `only`, `revise`, and `erase` provide the query workflow. The Python implementation works now; its selection-plan compiler also exists in the Rust core for the native path.

## API serializers

Use a schema-driven `Serializer` for external payload validation or an `EntitySerializer` for model output:

```python
from mangoo import EntitySerializer

product_view = EntitySerializer(Product)
payload = product_view.json(product)
```

The serializer normalizes entities, mappings, lists, and dates, then encodes them with `msgspec` for consistent high-performance JSON output.

## Superuser

Create the first administrator for a project with:

```powershell
python manage.py createsuperuser
```

Mangoo prompts for a username, email address, and password confirmation. It stores users in the project's local SQLite database (`mangoo.sqlite3` by default), with salted scrypt password hashes. Application code can validate credentials with `mangoo.authenticate(database_path, username, password)`.

## Authentication security

Mangoo's built-in authentication uses salted, memory-hard scrypt password hashes; requires passwords of at least 12 characters with lowercase, uppercase, numeric, and symbol characters; locks an account for 15 minutes after five failed attempts; and stores only hashes of session tokens. Enable authenticator-app two-factor authentication with:

```powershell
python manage.py enable2fa admin
```

The command prints a secret to add to a TOTP-compatible authenticator app. Keep it private: it is shown only when two-factor authentication is enabled.

The generated project also exposes JSON authentication endpoints: `POST /api/auth/login`, `GET /api/auth/me`, and `POST /api/auth/logout`. Login accepts `username`, `password`, and optional `totp_code`; it returns a Bearer session token. Use HTTPS before exposing these endpoints outside local development.

The Rust extension is optional for this first scaffold. To build it, install Rust and run `maturin develop` from the repository root.

See [ROADMAP.md](ROADMAP.md) for the planned delivery path and boundaries.
