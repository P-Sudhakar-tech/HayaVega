# Mangoo

Mangoo is an early, runnable experiment in a Django-like project structure and FastAPI-style automatic API docs, backed by a Rust core.

See [UPCOMING_IMPROVEMENT_ROADMAP.md](UPCOMING_IMPROVEMENT_ROADMAP.md) for
the near-term plan and [INDUSTRY_LEADERSHIP_ROADMAP.md](INDUSTRY_LEADERSHIP_ROADMAP.md)
for the longer-range one: performance benchmarks against FastAPI/Django/
Next.js and a higher developer-experience bar (the Signals rebrand there is
delivered -- see [Mangoo Signals](#mangoo-signals) below). Published
benchmark results against FastAPI and Django live in
[`benchmarks/results/RESULTS.md`](benchmarks/results/RESULTS.md), with the
full methodology, scope, and honest caveats in
[`benchmarks/README.md`](benchmarks/README.md).

## What works now

- `newproject` and `newapp` scaffolding (`startproject`/`startapp` still work as deprecated aliases)
- Django-style `manage.py`, `settings.py`, and `urls.py`
- A development server and Python views
- Both WSGI (default) and ASGI deployment, the latter for projects that need `async def` views
- A small PyO3 native module (`mangoo_native`) for the Rust/Python boundary
- Server-rendered HTML via `mangoo.templates`, plus `mangoo.ui`: server-rendered components and a first-party htmx-style hypermedia runtime (`mangoo-ui.js`), no JS build step

## Quick start

```powershell
cd D:\separate_code\mangoo
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
mangoo-admin newproject mysite
cd mysite
python manage.py newapp blog
python manage.py launch
```

Open `http://127.0.0.1:8000/`.

## ASGI support

WSGI (above) is the default for every scaffolded project and needs nothing
extra. Reach for ASGI only when a view needs `async def` for concurrent I/O
(calling another service, an async database driver) that WSGI's
one-thread-per-request model can't overlap as cheaply. `mango newproject`
also scaffolds `project/asgi.py`, built with
`mangoo.asgi.create_asgi_application()` instead of
`mangoo.runtime.create_application()` but otherwise exposing the same kind
of standard `application` object:

```bash
uvicorn project.asgi:application --host 0.0.0.0 --port 8000
```

Views may be `async def` (awaited directly) or ordinary synchronous
functions (run via a dedicated worker-thread pool, `max_sync_workers=` on
`create_asgi_application`, default 16), so every existing sync view --
including ones wrapped in `api_route`, `csrf_protect`, or
`login_required`/`permission_required` -- keeps working unmodified under
either transport with no migration required. `mangoo.testing.AsyncTestClient`
tests ASGI apps in-process the same way `TestClient` tests WSGI apps. See
[DEPLOYMENT.md](DEPLOYMENT.md#asgi-only-if-a-view-needs-async-def) for
worker guidance and
[`benchmarks/README.md`](benchmarks/README.md#asgi-vs-fastapi) for how it
measures against FastAPI on the same server.

## Settings

Each project has a Django-familiar `project/settings.py`, but Mangoo uses its own explicit runtime sections:

- `BASE_DIR`, `SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS` for project identity and environment behavior.
- `INSTALLED_APPS`, `MIDDLEWARE`, and `ROOT_URLCONF` for application composition.
- `DATABASES['default']` for the SQLite database location.
- `SERVER` for the development host and port.

For example, change the default development port in `SERVER['PORT']`, or override it temporarily with `python manage.py launch 8001`.

## Databases

Mangoo's database layer accepts SQLite, PostgreSQL, MySQL, and MariaDB through Django-style `DATABASES['default']` configuration. SQLite works with Python alone. For other engines, add the driver with UV before starting the project:

```powershell
uv add "psycopg[binary]"   # PostgreSQL
uv add pymysql cryptography # MySQL or MariaDB -- cryptography is required by
                             # pymysql to authenticate against MySQL 8's
                             # default caching_sha2_password plugin; every
                             # connection fails without it
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

### Connection pooling

By default, every database call opens a new connection and closes it when done. `create_application(pool_connections=True)` (or `mangoo.db.enable_connection_pooling()` directly) reuses one connection per worker thread instead -- no other code changes needed, since it only changes what `connect()`/`Connection.close()` do internally.

**Benchmark this for your own deployment before enabling it -- it is not a universal win.** It's a real, measured improvement under ASGI/uvicorn, but a real, measured ~10-15% *regression* under WSGI/waitress with SQLite specifically (many threads holding a persistent SQLite connection open under contention is itself slower than each opening and closing one quickly -- confirmed with a framework-free reproduction, not a guess). `benchmarks/mangoo_asgi_app/app.py` enables it; `benchmarks/mangoo_app/app.py` (WSGI) deliberately does not. See `INDUSTRY_LEADERSHIP_ROADMAP.md`'s "Connection pooling, attempted" section for the full numbers.

Also unresolved: a pooled MySQL/PostgreSQL connection that the server drops after sitting idle is not detected or reconnected automatically -- restart worker processes periodically if this matters for your deployment.

## Migrations and transactions

Mangoo uses small, ordered SQL migrations. Add a numbered file such as
`blog/migrations/0001_products.py` to an installed app:

```python
operations = [
    "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL)",
]
```

Run `python manage.py applymigrations` to apply pending migrations, or
`python manage.py migrationstatus` to inspect their state. Each migration runs
in its own transaction; if any operation fails, neither its schema changes nor
its history row are committed. Applied files are checksummed, so changed or
missing migration files are rejected instead of silently proceeding.

For an app with declared `Entity` classes, create the reviewed initial table
migration with `python manage.py newmigration blog` (after adding `"blog"`
to `INSTALLED_APPS`). Later invocations generate only nullable `ADD COLUMN`
operations and record a dependency on the preceding migration. New tables,
non-nullable fields, field alterations, and removals must remain explicit,
reviewed SQL migrations; `newmigration` detects a type, nullability, or
uniqueness change on an already-migrated column and raises an error naming
the column, rather than silently leaving the schema out of sync with the
declared entity.

## Production validation and probes

Before starting with `DEBUG = False`, Mangoo requires a non-default
`SECRET_KEY`, explicit `ALLOWED_HOSTS`, and `SECURITY` settings for
`FORCE_HTTPS` and `SECURE_COOKIES`. Add `*health_routes(DATABASES['default'])`
to your URL patterns to expose `GET /healthz` (liveness) and `GET /readyz`
(database readiness) for your deployment platform.

Run `python manage.py diagnose` (or `mango diagnose`) to catch the same
class of issue *before* a deploy attempt fails on it: unsafe settings
(`DEBUG` left on, a placeholder `SECRET_KEY`, a wildcard `ALLOWED_HOSTS`),
deprecated Pulse-era API usage anywhere in your own project files, and
pending migrations -- each finding names exactly where the problem is and
what to do about it, e.g. "Set SECRET_KEY ... in project.settings."

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

Request-level metrics are opt-in: pass a `MetricsRegistry` as `metrics=` to
`create_application` to record a count and a duration observation for every
completed request, labeled by the matched route pattern (not the raw path,
so cardinality stays bounded) and status code. Nothing changes for
applications that do not pass one. Add `metrics_routes(registry)` to your
URL patterns to expose the counters at `/metrics` in Prometheus text
exposition format, ready for a standard Prometheus scrape config:

```python
from mangoo import MetricsRegistry, metrics_routes

metrics = MetricsRegistry()
urlpatterns = [..., *metrics_routes(metrics)]
application = create_application(urlpatterns, metrics=metrics)
```

Call `metrics.error_rate()` for the fraction of recorded requests that
returned a 5xx status, or `metrics.snapshot()` for the raw counters.

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
for release-review controls. PostgreSQL and MySQL/MariaDB integration tests
are opt-in: set `MANGOO_TEST_POSTGRES_NAME` or `MANGOO_TEST_MYSQL_NAME` plus
optional host, port, user, and password variables before running the test
suite.

## Testing and CI

Install the development extra and run the suite with `pip install -e ".[dev]"`
then `pytest`. This enforces an 85% coverage gate by default (see
`pyproject.toml`); pass `--no-cov` to skip it locally. GitHub Actions
(`.github/workflows/ci.yml`) runs the suite on Python 3.10, 3.11, and 3.12 for
every push and pull request, plus a separate job that builds the
`mangoo_native` Rust extension and runs the tests it backs, and a separate job
that runs the opt-in PostgreSQL integration test against a `postgres:16`
service container.

Lint and format with [ruff](https://docs.astral.sh/ruff/): `ruff check .` and
`ruff format .` (the latter without `--check` applies fixes). Both run as a
required, separate `lint` CI job. Configuration lives in `pyproject.toml`
under `[tool.ruff]`; line length is set generously (320) to match this
codebase's existing dense style rather than forcing a rewrite, and `E501`
(line-too-long) is intentionally not a lint-enforced rule for the handful of
lines -- an `__all__` list, embedded HTML/JS template strings -- that are
already one deliberate unit and would only get harder to read if wrapped.

Type-check with [mypy](https://mypy-lang.org/): `mypy` (config lives in
`pyproject.toml` under `[tool.mypy]`, scoped to `src/mangoo`). This runs as
its own required `typecheck` CI job, separate from `lint` and `test`, on the
pure-Python fallback build. `src/mangoo/py.typed` (PEP 561) ships in the
built wheel, so downstream projects that import Mangoo get real type
checking against its actual annotations rather than an implicit `Any`.
Two narrow `[[tool.mypy.overrides]]` blocks turn off missing-import errors
for `mangoo_native` (the optional, self-built Rust extension) and for
`psycopg`/`pymysql` (optional database drivers not installed in the default
dev environment) -- everything else is checked at mypy's default strictness.

## Mangoo Signals

The opt-in in-process event bus is available under `mangoo.addons.signals`.
Applications explicitly create a `SignalBus`, subscribe to named events, and
publish events. Delivery is always concurrent and off the caller's thread --
there is no sync/async choice to make -- through a Rust-native worker pool
(`mangoo_native.SignalDispatcher`) when the optional extension is built, or a
behavior-compatible Python `ThreadPoolExecutor` otherwise, and isolates
subscriber failures either way. Pass the same `SignalBus` as `signals=` to
`create_application`, `auth_routes`, `admin_routes`, or `migrations.apply` to
receive `request.*`, `auth.*`, `admin.action`, and `migration.applied` events
from those components; nothing changes for applications that do not pass one.
See `src/mangoo/addons/signals/README.md` for its full API, delivery
semantics, and how to build the Rust-backed dispatcher.

This add-on originally shipped as "Pulse." `mangoo.addons.pulse` and every
`pulse=` keyword argument still work exactly as before (`PulseBus` is
`SignalBus`, `PulseEvent` is `SignalEvent`) but raise a `DeprecationWarning`
and will be removed in a future release -- update to `mangoo.addons.signals`
and `signals=` when convenient. Event names themselves never changed.

## REST and GraphQL

Mangoo includes a small dependency-free API layer. The bundled example exposes:

- `GET /api/health` - JSON REST health response.
- `POST /graphql` - GraphQL JSON endpoint. Send `{"query":"{ greeting }"}`.

Use `api_route('api/items', view, methods=('GET', 'POST'))` for REST endpoints. Define GraphQL fields with `Schema(query={'field': resolver}, mutation={'field': resolver})` and mount them with `path('graphql', graphql_view(schema), methods=('POST',))`.

Mangoo's GraphQL layer is a small, dependency-free executor, not a full
spec-compliant engine -- it supports what most resolvers actually need:

- **Arguments**: `field(name: "Ada", limit: 5, active: true)` are parsed and
  passed to the resolver as keyword arguments: `def field(request, name,
  limit, active): ...`.
- **Variables**: send `{"query": "query($id: ID!) { user(id: $id) { name } }",
  "variables": {"id": "42"}}`. Declared defaults (`$active: Boolean = true`)
  and required variables (`$id: ID!` with no default) are honored.
- **Nesting**: when a resolver returns a mapping (or a list of mappings), a
  sub-selection like `{ user { id team { name } } }` is projected from it
  recursively; requesting a field the mapping doesn't have is a per-field
  error, not a crash.
- **Mutations**: pass `Schema(query=..., mutation={'createUser': resolver})`
  and call them with `mutation { createUser(name: "Ada") { id } }`.
- **Limits**: `Schema(..., max_depth=10, max_fields=200)` (the defaults)
  reject queries that nest or fan out further than that, and a hard
  structural limit rejects pathologically nested query *text* during
  parsing, before any resolver runs.
- **Introspection**: off by default, since there's no declared type system
  to expose a real `__schema` from. `__typename` always works (it doesn't
  reveal schema shape). Pass `Schema(..., introspection=True)` to allow
  `__schema { queryType { fields { name } } mutationType { fields { name } } }`,
  which lists root field names only -- not a spec-complete introspection
  schema.

Fragments and directives are not supported and are rejected with a clear
error rather than a confusing parse failure.

Every JSON error response uses the same envelope: `{"detail": "<message>"}`.
Return it with `json_response({"detail": "..."}, status)` so clients only ever
parse one error shape; `mangoo.swagger.ERROR_SCHEMA` is that shape as a JSON
Schema, reused automatically for the errors Mangoo documents for you (see
below) and importable for your own routes' `responses`.

There's no framework-enforced versioning scheme; the common convention is a
version segment in the path, such as `api_route('api/v1/items', view)`. Give
each version's application its own `api_version` (see below) so `/openapi.json`
reports the version actually being served.

## Built-in Swagger documentation

Every Mangoo application automatically exposes an OpenAPI 3 document at `/openapi.json` and an interactive Swagger UI at `/docs`. REST endpoints declared with `api_route` are added to the document automatically, along with a `bearerAuth` security scheme and a shared `Error` schema (`{"detail": string}`) that any endpoint's error responses can reference. The documentation routes are reserved only when an application has not defined routes with those paths itself.

Describe an endpoint precisely instead of relying on the generic fallback:

```python
from mangoo import ERROR_SCHEMA, api_route

api_route(
    "api/items",
    view,
    methods=("POST",),
    secured=True,  # requires a Bearer token; adds `security` + a 401 response
    request_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    responses={
        201: {"description": "Created.", "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}},
        400: {"description": "Invalid input.", "schema": ERROR_SCHEMA},
    },
)
```

`create_application(urlpatterns, api_title='Bookstore API', api_version='2.1.0')` names and versions the generated document. All of the built-in `auth_routes` endpoints (login, password reset, email verification, sessions) already describe themselves this way.

## Development autoreload

Mangoo can launch directly from this source checkout without pip. From the repository root, run `./mango.cmd launch 8001`. It watches Python files in the project and automatically restarts the development server after code changes. Use `./mango.cmd launch 8001 --noreload` to disable this behavior.

If you later install Mangoo as a package, `mango launch 8001` is also available as a terminal command.

Press `Ctrl+C` in the same terminal to stop the server cleanly, including when autoreload is enabled.

## MVP polish

Render templates with `render_template("page.html", context, directories=(BASE_DIR / "templates",))`, and expose development assets with `*static_routes(BASE_DIR / "static")`. The included `TestClient` exercises a WSGI application without a network socket. Unhandled view errors are logged to `mangoo.runtime` and return a safe `500` response without a traceback when debug mode is disabled.

### Templates

`mangoo.templates` is a small, dependency-free engine -- every interpolated
value is HTML-escaped and no template ever executes arbitrary Python:

- `{{ user.name }}` -- dotted-path lookups into dicts or objects.
- `{% if role == "admin" %}...{% elif a and not b %}...{% else %}...{% endif %}` --
  `==`, `!=`, `in`, `and`, `or`, `not`, and string/number/`true`/`false`/`none` literals.
- `{% for item in items %}{{ loop.index }}: {{ item }}{% endfor %}` -- with
  `loop.index`/`loop.index0`/`loop.first`/`loop.last` inside the body.
- `{% extends "base.html" %}` + `{% block name %}...{% endblock %}` -- a
  child overrides a parent's named blocks; unoverridden blocks keep the
  parent's default content. `extends` must be the template's first tag.

Compiled templates are cached per file, invalidated by mtime, so an edited
template is picked up on the next render without a restart, and an
unchanged one skips re-parsing. Fragments, directives, and filters are not
supported, and undefined variables render as empty text by default; pass
`render_template(..., debug=True)` in development to raise
`TemplateRenderError`/`TemplateSyntaxError` on an undefined variable,
non-iterable loop target, or a block a child defines that the base template
never declares, instead of silently rendering nothing.

## UI framework

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
small dependency-free JS file with a single delegated click listener for
both hx-* triggers and `mgo-boost` client-side link routing. It reads the
`mangoo_csrf` cookie and sends it as `X-Csrf-Token` automatically, so an
`hx-post` to a `@csrf_protect`-guarded view is protected without extra
client-side wiring. Run the example site (`./mango.cmd launch 8001` from
`example_site/`) and visit `/ui-demo` for a runnable version of this
pattern, or see `tests/test_ui_hypermedia.py` for the full round trip
proven against the real WSGI stack.

`mgo-boost` adds client-side routing on top of the same runtime: a link
inside (or carrying) an `mgo-boost` attribute navigates via `fetch` +
`history.pushState` instead of a full page load, swapping the element
both pages mark `mgo-boost-target`; `popstate` re-fetches and swaps for
back/forward. See `tests/test_ui_boost.py`. There is no client-side
reactive/signals layer in this version (a prior one existed and was
removed in favor of keeping this to a single, simpler hypermedia model);
a genuine Node.js/npm-based SPA framework (JSX, a virtual DOM, a bundler)
remains a distinct, separate, unscoped possibility.

Rust acceleration for fragment rendering was benchmarked and found to be a
genuine win for the small, fixed-shape markup a fragment swap renders
(4x-8x faster in isolation) -- unlike full-page template rendering, where
every general-purpose Rust candidate lost to Python. It is not wired into
`mangoo.ui`'s shipped API (a hardcoded native renderer only ever handles
one exact fragment shape, so generalizing it would reintroduce the same
problem full-page Rust templating already failed at); see
`benchmarks/native_htmx_fragment.py` for the measurement and
`benchmarks/mangoo_app/app.py`'s `posts_fragment_native` for a worked
example of writing your own for a hot fragment in your own project.

## Rust-native Mangoo types

Use Mangoo-specific types where performance-sensitive code benefits from a Rust implementation:

```python
from mangoo import mango_list

items = mango_list(["first", "second"])
items.append("third")
```

`mango_list` remains usable without a Rust toolchain through a compatible fallback. When the `mangoo_native` PyO3 extension is built, it automatically becomes a Rust-owned collection. Mangoo Signals' event dispatcher follows the same pattern: see [Mangoo Signals](#mangoo-signals) above. The next Rust migration targets are request routing, JSON serialization, authentication operations, and database execution.

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

For read-only rendering (a list page, a JSON API response) where entity behavior isn't needed, `values()` returns plain `dict` rows instead of `Entity` objects, skipping per-column coercion entirely -- a measured 1.28-1.36x faster than `fetch()` plus manual dict extraction for the same rows:

```python
posts = Post.collection(DATABASES["default"]).browse().arrange("id").window(20).values("title", "views")
# [{"title": "First post", "views": 12}, ...]
```

Pass no field names to get every column. It composes with `seek`/`arrange`/`window` exactly like `fetch()` does.

Every value in a generated query is a parameterized placeholder, never
interpolated into SQL text -- `seek(name__contains=user_input)` cannot
become a SQL injection no matter what `user_input` contains. Column and
table names (which SQL can't parameterize) are checked against a strict
identifier allowlist and the entity's own declared columns before they ever
reach a query string, so `seek(**{"unknown_field": 1})` or a crafted
`__table__` raises `EntityError` instead of running. This applies
everywhere the ORM is used, including the admin's generated forms. See
`tests/test_sql_injection.py`.

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
python manage.py createadmin
```

Mangoo prompts for a username, email address, and password confirmation. It stores users in the project's local SQLite database (`mangoo.sqlite3` by default), with salted scrypt password hashes. Application code can validate credentials with `mangoo.authenticate(database_path, username, password)`.

## Authentication security

Mangoo's built-in authentication uses salted, memory-hard scrypt password hashes; requires passwords of at least 12 characters with lowercase, uppercase, numeric, and symbol characters; locks an account for 15 minutes after five failed attempts; and stores only hashes of session tokens. Enable authenticator-app two-factor authentication with:

```powershell
python manage.py enable2fa admin
```

The command prints a secret to add to a TOTP-compatible authenticator app. Keep it private: it is shown only when two-factor authentication is enabled.

The generated project also exposes JSON authentication endpoints: `POST /api/auth/login`, `GET /api/auth/me`, and `POST /api/auth/logout`. Login accepts `username`, `password`, and optional `totp_code`; it returns a Bearer session token. Use HTTPS before exposing these endpoints outside local development.

## Admin

`admin_routes(DATABASES['default'])` always provides administrator-only
`/admin` and `/admin/users` pages. Pass `models=[ModelAdmin(YourEntity)]` to
add paginated list/add/edit/delete pages for your own `mangoo.models.Entity`
subclasses:

```python
from mangoo import admin_routes
from mangoo.admin import ModelAdmin
from mangoo.models import Entity, Integer, Text


class Product(Entity):
    name = Text()
    price = Integer()


urlpatterns = [*admin_routes(DATABASES["default"], models=[ModelAdmin(Product)])]
```

Each registered model gets:

- **Pagination**: `?page=N` on the list view, sized by `ModelAdmin(..., page_size=25)`.
- **Validation**: a blank required field or a value that fails a column's type coercion (e.g. text in an `Integer` field) is a form error, not a crash.
- **Permissions**: `ModelAdmin(..., can_access=lambda user: ...)` gates a model on any predicate; the default requires `user.is_superuser`, same as `superuser_required`.
- **Change history**: the edit page lists every create/update/delete recorded against that record, sourced from the same audit log `audit_events()` reads -- updates record which field names changed, not their values.

Every add/edit/delete submission is CSRF-protected the same way as
`auth_routes`: the rendered form carries a hidden `csrf_token` field (plain
HTML forms can't set a custom header), checked against the `mangoo_csrf`
cookie set when the form was shown. Bearer-token requests skip the check,
since they aren't vulnerable to cross-site forgery the way cookies are.

## Authentication lifecycle

`auth_routes` also exposes password reset, email verification, and session
management. Reset and verification tokens are single-use, expire (1 hour and
24 hours respectively), and are never stored or logged in plain text:

- `POST /api/auth/password-reset/request` and `POST /api/auth/password-reset/confirm` -
  requesting a reset always returns `202`, whether or not the username exists,
  to avoid revealing valid accounts. Confirming one revokes every existing
  session for that user.
- `POST /api/auth/email/verify/request` (authenticated) and
  `POST /api/auth/email/verify/confirm` - `GET /api/auth/me` reports
  `email_verified`; gate a view on it with `email_verified_required(database_path)`.
- `GET /api/auth/sessions` lists the current user's active sessions (id,
  timestamps, IP address, and user agent -- never a token);
  `POST /api/auth/sessions/revoke-others` revokes every session except the
  caller's.

By default, reset and verification tokens are logged rather than emailed, so
the flow works without a mail provider configured. Pass your own
`notify(kind, user, token)` callable as `auth_routes(..., notify=...)` to send
real email. Every step -- request, completion, rotation, and bulk revocation
-- is recorded through the audit log without ever storing the token itself.

### Login and session telemetry

Every login attempt and every session records the client's IP address and
`User-Agent`, both in the `mangoo_sessions` table and in the audit log
(`auth.login_succeeded`, `auth.login_failed`, and `auth.session_created`),
so you can review where and from what an account signed in -- without
storing anything sensitive: no credential, token, or cookie is ever
recorded, matching the audit log's existing rule. This is IP-address-level
context, not resolved geolocation -- Mangoo stays dependency-free and does
not ship or call a GeoIP service; add one yourself and log its result as
audit metadata if you need city/country-level location.

`X-Forwarded-For` is only trusted from a proxy you've explicitly configured.
Pass the same `HardeningOptions` you gave `create_application` to
`auth_routes(..., hardening=...)` so login recovers the real client address
behind a trusted reverse proxy instead of recording the proxy's own IP;
without it, the immediate connection's address is used, exactly like the
runtime's own rate limiter and `TRUSTED_PROXY_IPS` handling.

The Rust extension is optional. Install a Rust toolchain (`cargo` on PATH) before running `pip install -e .` (or a plain `pip install .`) and it builds and bundles automatically -- no separate command. Without Rust, the same install command gives you the pure-Python fallback.

See [ROADMAP.md](ROADMAP.md) for the planned delivery path and boundaries.
