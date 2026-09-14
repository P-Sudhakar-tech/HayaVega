# Mangoo upcoming improvement roadmap

## Status: active development (August 2026)

This roadmap converts the current prototype into a safer, more deployable framework. Every feature below must include unit tests, regression tests for the identified risk, documentation, and a full-suite pass before it is marked complete.

## Priority order

| Priority | Workstream | Outcome |
|---|---|---|
| P0 | Data and migrations | **Delivered:** transactional, checksummed migrations, initial entity migration generation, and schema-diff detection (type/nullability/uniqueness alterations on existing columns are rejected with an explicit-migration error instead of silently ignored) all shipped |
| P0 | Production validation | **Delivered:** production settings validation, DB readiness/liveness, deployment guide, and CI-run PostgreSQL and MySQL/MariaDB integration tests all shipped |
| P1 | Runtime and static assets | **Delivered:** binary files, MIME types, cache-control, ETags, conditional requests, and opt-in request metrics (counts, latency histogram, error rate, Prometheus export) all shipped |
| P1 | Authentication lifecycle | **Delivered:** password reset, email verification, session listing/rotation/revoke-all, audit events for every step, and full OpenAPI descriptions for all of it all shipped |
| P1 | API documentation | **Delivered:** `api_route` accepts `secured`/`request_schema`/`responses`; the generated OpenAPI document gets a `bearerAuth` security scheme and a shared `Error` schema; `create_application(api_title=..., api_version=...)` names and versions it; all 10 built-in auth-lifecycle endpoints fully describe themselves; the error envelope (`{"detail": string}`) and a path-prefix versioning convention are documented |
| P2 | Developer experience | **In progress:** CI (GitHub Actions, coverage gate, ruff lint/format, mypy type checks), GraphQL maturity, template inheritance/loops/conditionals/caching/diagnostics, and admin model registration/pagination/validation/permissions/change history all delivered; dependency scanning and release automation remain |
| P2 | Native runtime | **In progress:** pre-existing pyo3 API mismatches that blocked every native build fixed; a `hatchling` build hook now builds and bundles `mangoo_native` automatically as part of a normal `pip install`/`pip install -e .`, with no separate manual step and no project-name collision risk; Signals' dispatcher is the first concurrency primitive on it. Request routing, JSON serialization, authentication operations, and database execution remain Python-only |
| P2 | Mangoo Signals | **Delivered:** opt-in in-process event bus with always-async delivery (no sync/async choice for callers), a Rust-native worker-pool dispatcher (`mangoo_native.SignalDispatcher`) with a behavior-compatible Python `ThreadPoolExecutor` fallback, sensitive-key rejection, and framework lifecycle wiring (request, auth, admin, migrations) all shipped; only remaining gap is that no built-in app wires a bus in by default. Shipped as "Pulse" originally -- `mangoo.addons.pulse`/`pulse=` still work as deprecated aliases (see Week 12) |
| P2 | Fullstack UI framework | **Delivered (Week 9/11) -> reverted (Week 14) -> rebuilt, re-scoped (Week 15):** the original Stage 1 (hypermedia) + Stage 2 (client-side signals) were removed entirely in favor of pure server-rendered HTML, then Stage 1 alone was rebuilt with a leaner `mangoo-ui.js` and a benchmarked Rust option for fragment rendering -- deliberately without Stage 2's signals layer this time, keeping the framework to one hypermedia model rather than two rendering approaches |
| P2 | Documentation website | **Planned (Week 16), not started:** a real, versioned documentation website (tutorial, topic guides, generated API reference, full-text search, a Django-style version switcher tied to release tags) to replace root-level Markdown as the framework's primary documentation surface |
| P2 | gRPC support | **Planned (Week 17), not started:** a third request surface alongside REST and GraphQL, via `grpcio`/`grpclib` running as its own HTTP/2 server (not a retrofit of the custom Rust runtime, which deliberately left HTTP/2 out of scope), reusing existing auth and Signals wiring |

## Future-readiness estimate (planning baseline, refreshed August 20, 2026)

These estimates assume one focused developer, the current test-driven pace, and
no delay obtaining database or email-provider infrastructure. Re-estimate after
each milestone; external security-review or release feedback is excluded.
Items are sequenced by priority and dependency (each assumes the ones above it
are done first); a team working several in parallel would finish sooner.

| Order | Workstream | Estimated effort | Target completion |
|---|---|---:|---|
| 1 | ~~MySQL/MariaDB integration test (closes out Production validation)~~ | Delivered Aug 19, 2026 | -- |
| 2 | ~~Authentication lifecycle: password reset, email verification, session rotation/revoke-all~~ | Delivered Aug 19, 2026 | -- |
| 3 | ~~API documentation: full OpenAPI schemas, error envelope, Bearer auth, versioning~~ | Delivered Aug 19, 2026 | -- |
| 4 | ~~Runtime observability remainder: request metrics beyond logging/audit~~ | Delivered Aug 20, 2026 | -- |
| 5 | ~~Native runtime packaging: resolve the `mangoo`/`mangoo-python` project-name collision so `mangoo_native` ships as part of normal installs, not a manual dev step~~ | Delivered Aug 20, 2026 | -- |
| 6a | ~~GraphQL: arguments, variables, nested selections, mutations, depth/field limits, introspection policy~~ | Delivered Aug 20, 2026 | -- |
| 6b | ~~Template engine: inheritance, loops, conditionals, caching, developer-mode diagnostics~~ | Delivered Aug 20, 2026 | -- |
| 6c | ~~Admin: model registration, pagination, validation, permissions, change history~~ | Delivered Aug 20, 2026 | -- |
| 7 | ~~Fullstack UI framework, Stage 1: server-rendered components and a first-party hypermedia runtime~~ | Delivered Aug 20, 2026 | -- |
| 8a | ~~Linting and formatting: `ruff check`/`ruff format` as a required CI job~~ | Delivered Aug 21, 2026 | -- |
| 8b | ~~Type checks: `py.typed` + mypy as a required CI job~~ | Delivered Aug 26, 2026 | -- |
| 8c | Developer experience remainder: dependency scanning, release automation | 1–3 working days | -- |
| 9 | ~~Fullstack UI framework, Stage 2: client-side signals and `mgo-boost` routing, no Node.js/npm~~ | Delivered Aug 21, 2026 | -- |
| -- | A Node.js/npm-based SPA framework (JSX, virtual DOM, bundler) | Not yet scoped | The originally-imagined shape of Stage 2, re-scoped to avoid introducing Node.js/npm tooling to this repo (see Week 11); revisit only with its own dedicated planning and explicit sign-off if a concrete need for JSX/VDOM/a bundler emerges |
| -- | Run Python (not JavaScript) directly in the browser via Pyodide/PyScript (WASM) | Explicitly declined for now | Considered and rejected Aug 20, 2026: `mangoo-ui.js` already means application authors never write JavaScript -- every button/form/fragment is defined in Python, the same as a view. Pyodide/PyScript would only replace a fixed ~3.5KB generic dispatcher with a ~6-20MB WASM Python runtime and multi-second cold-start init, for no reduction in how much JavaScript an application author writes (already zero). Revisit only if a concrete need for genuine client-side Python execution (not just "no hand-written JS") emerges. |
| -- | Further Rust migrations (routing, JSON, auth, DB execution) | Not yet scoped | Benchmark each candidate first; schedule only once a measured win justifies it |
| -- | ~~Replace the WSGI dev server with an async/Rust runtime~~ | Delivered Sep 2026 | Delivered as a synchronous (not async), fixed-thread-pool native WSGI server, `SERVER['ENGINE'] = 'rust'` in settings -- a disclosed scope-down from "async" (CPython's GIL means async I/O wouldn't let more Python view code run concurrently than a thread pool already does); see `INDUSTRY_LEADERSHIP_ROADMAP.md` and `benchmarks/README.md#the-rust-runtime-vs-the-python-runtime-waitress` for the full write-up, including two real bugs (`TCP_NODELAY`, HTTP keep-alive/Windows port exhaustion) found and fixed while benchmarking it |
| 12 | ~~Rebrand Pulse as Signals: `mangoo.addons.signals`, `signals=`, deprecated `pulse=`/`mangoo.addons.pulse` aliases~~ | Delivered Aug 21, 2026 | -- |

Note on pace: this table assumes solo, calendar-day effort. Several of the
items already marked delivered above (Signals' Rust-backed dispatcher, the CI
coverage gate, migration schema-diff detection) were originally estimated in
days and were instead completed within single agent-assisted sessions on
August 18, 2026 -- so these dates are a conservative ceiling, not a floor, if
work continues at that pace.

## Week 1: data safety and test foundations

### Deliverables

- [x] Define migration file format, migration history table, and migration command interface.
- [x] Implement SQLite migration apply, status, and rollback-safe failure behavior.
- [x] Add explicit transaction helpers with commit, rollback, and nested-operation rules.
- [x] Add test database fixtures and migration test helpers.
- [x] Establish coverage reporting and a required test command for CI. `pytest` runs with an 85% coverage gate (`pyproject.toml`); GitHub Actions (`.github/workflows/ci.yml`) runs it on Python 3.10-3.12 for every push and pull request.

### Acceptance criteria

- A new project can apply ordered migrations to an empty database.
- Re-running applied migrations is safe and does not change schema state.
- A failed migration records no partial schema/data changes where the database supports transactions.
- Migration history detects altered or missing migration files.
- Tests cover success, duplicate apply, failure, rollback, and malformed migration cases.

## Week 2: production validation and deployment safety

### Deliverables

- [x] Add PostgreSQL and MySQL/MariaDB integration tests. Both are opt-in and run in CI against `postgres:16` and `mysql:8` service containers. Adding real MySQL coverage also surfaced and fixed a latent bug: `Connection.atomic()` called `.execute()` directly on the raw driver connection, which pymysql's `Connection` does not implement (only sqlite3's and psycopg3's do) -- every transactional migration would have failed against MySQL/MariaDB. Fixed by routing through the class's own `execute()` wrapper, which works across all three drivers.
- [x] Add environment validation for production: secret key, debug state, allowed hosts, HTTPS, and secure cookies.
- [x] Add health and readiness endpoints with database connectivity checks.
- [x] Publish a reverse-proxy, HTTPS, backup, restore, and rollback deployment guide. See `DEPLOYMENT.md`.
- [x] Begin a security-review checklist covering sessions, CSRF, CORS, proxy trust, headers, and rate limits. See `SECURITY_REVIEW_CHECKLIST.md`.

### Acceptance criteria

- CI can run SQLite tests plus opt-in PostgreSQL integration tests.
- Production validation fails with actionable errors for unsafe configuration.
- Readiness is unhealthy when the configured database cannot be reached.
- Deployment documentation is reproducible from a clean environment.
- Security checklist items each link to a test or documented operational control.

## Week 3: runtime, static files, and observability

### Deliverables

- [x] Upgrade static serving to support binary files, cache-control, ETag, conditional requests, and safe MIME handling.
- [x] Bound and instrument rate-limiter storage; document Redis/edge replacement for multi-instance use.
- [x] Add structured request IDs and configurable JSON logging.
- [x] Add persistent audit-event hooks for authentication and administration actions.
- [x] Preserve query strings during HTTPS redirects and improve trusted-proxy configuration.

### Acceptance criteria

- CSS, JavaScript, images, fonts, and SVG files are served correctly in development.
- Static traversal, unknown MIME, cache, and conditional-response behaviors are tested.
- Logs include request ID, method, path, status, and duration without leaking secrets.
- Rate limiting has bounded memory behavior and regression tests.

## Week 4: authentication and API maturity

### Deliverables

- [x] Add password-reset tokens with expiry, single use, secure storage, and pluggable mail delivery. Delivered Aug 19, 2026: `create_password_reset_token`/`reset_password` in `auth.py`, `POST /api/auth/password-reset/{request,confirm}` in `auth_api.py`. Tokens expire after 1 hour, are single-use, are stored only as a sha256 hash, and completing a reset revokes every existing session. Mail delivery is pluggable via a `notify(kind, user, token)` callable passed to `auth_routes`; the default only logs the token so the flow works without a mail provider configured.
- [x] Add email-verification tokens and verified-account policy hooks. Delivered Aug 19, 2026: `create_email_verification_token`/`verify_email` (24-hour, single-use tokens), `User.email_verified`, and the `email_verified_required(database_path)` permission helper for gating a view on verification.
- [x] Add session rotation, active-session listing, and revoke-all-sessions support. Delivered Aug 19, 2026: `list_sessions` (id/timestamps only, never a token), `revoke_all_sessions(..., except_token=...)`, and `rotate_session`; `GET /api/auth/sessions` and `POST /api/auth/sessions/revoke-others` expose the latter two over the API.
- [x] Extend OpenAPI output with request/response schemas, error responses, and Bearer authentication. Delivered Aug 19, 2026: `api_route(..., secured=, request_schema=, responses=)`, a `bearerAuth` security scheme, and a shared `Error` schema (`mangoo.ERROR_SCHEMA`) added to every generated document. All 10 built-in auth-lifecycle endpoints now describe themselves fully.
- [x] Add API versioning and error-envelope conventions. Delivered Aug 19, 2026: `create_application(api_title=, api_version=)` names and versions the generated document; the `{"detail": string}` error envelope and a path-prefix (`api/v1/...`) versioning convention are documented in `README.md`.

### Acceptance criteria

- [x] Reset and verification tokens cannot be reused or used after expiry. See `tests/test_auth_lifecycle.py`.
- [x] Session creation rotates identifiers at authentication boundaries -- `create_session` already issued a fresh token per login; `rotate_session` now also lets an existing session get a fresh identifier without a full re-login.
- [x] Authentication lifecycle events produce audit records. `auth.password_reset_requested`/`completed`, `auth.email_verification_requested`, `auth.email_verified`, `auth.session_rotated`, and `auth.sessions_revoked_all`.
- [x] Swagger documents authentication and all built-in error responses. See `tests/test_swagger.py::AuthRoutesDocumentationTests`.
- [x] All new flows have unit, integration, and negative-path tests. See `tests/test_auth_lifecycle.py`, `tests/test_auth_lifecycle_api.py` (23 tests: expiry, single-use, invalid tokens, weak-password rejection, session listing/revocation, and the `email_verified_required` permission hook), and `tests/test_swagger.py` (8 tests covering the new OpenAPI schema/security/versioning surface).

## Week 5: native packaging and observability

### Deliverables

- [x] Resolve the `mangoo` (root `pyproject.toml`) vs. `mangoo-python` (`crates/mangoo-python/Cargo.toml`) project-name collision so `mangoo_native` can be built as part of a normal packaged install instead of a manual, documented dev-only step (`maturin develop --manifest-path crates/mangoo-python/Cargo.toml`). Delivered Aug 20, 2026: `hatch_build.py` is a `hatchling` custom build hook that runs `cargo build --release` against `crates/mangoo-python/Cargo.toml` during any `pip install .`/`pip install -e .` and bundles the resulting extension into that same install when `cargo` is on PATH -- there is now exactly one build definition for the `mangoo` distribution, not two independently-invokable ones that happen to share a name. Verified against a real `python -m build` wheel, an editable install, and `MANGOO_SKIP_NATIVE_BUILD=1` forcing the pure-Python fallback; the extension is staged in a temp directory and never touches the source tree. See `tests/test_hatch_build.py` and the simplified `native-extension` CI job.
- [x] Add request-level metrics (counts, latency distribution, error rate) as an opt-in hook alongside the existing structured logs and audit events, following the same "no behavior change unless an application opts in" pattern Mangoo Signals uses. Delivered Aug 20, 2026: `MetricsRegistry` and `metrics_routes` in `src/mangoo/metrics.py`; pass `metrics=` to `create_application` to record per-request counts and a latency histogram labeled by matched route pattern and status, and mount `metrics_routes(registry)` to expose them at `/metrics` in Prometheus text format. See `tests/test_metrics.py`.
- [x] Add a MySQL/MariaDB opt-in integration test mirroring the existing PostgreSQL one, plus a CI job with a `mysql:8` service container. Delivered Aug 19, 2026 -- see `tests/test_mysql_integration.py`.

### Acceptance criteria

- [x] A packaged install (not just a source checkout) can build or ship `mangoo_native` without breaking the `mangoo` package it accompanies. Verified with a real `python -m build --wheel` and an editable install in a clean virtualenv: both installed `mangoo` from `src/` and `mangoo_native` from site-packages with no conflict.
- [x] Metrics are opt-in, add no overhead when unused, and are documented with a supported scrape or export path. `metrics=None` is the `create_application` default and skips every metrics call; `/metrics` renders standard Prometheus exposition format.
- [x] CI runs SQLite, opt-in PostgreSQL, and opt-in MySQL/MariaDB integration tests.

## Week 6: GraphQL maturity

### Deliverables

- [x] Add field arguments (string/int/float/boolean/null/enum/list/object literals) passed to resolvers as keyword arguments.
- [x] Add variables, with declared defaults and required-ness (`$id: ID!` vs. `$active: Boolean = true`) resolved from the request's `variables` object.
- [x] Add nested selections: a resolver returning a mapping (or list of mappings) has its sub-selection projected recursively; unknown sub-fields are per-field errors, not a crash.
- [x] Add mutations: `Schema(query=..., mutation={...})`, executed via `mutation { field(...) { ... } }` documents.
- [x] Add query limits: configurable `max_depth`/`max_fields` (defaults 10/200) checked before execution, plus a hard structural parse-depth cap independent of configuration, so a maliciously deep query can't overflow the parser itself.
- [x] Add an introspection policy: off by default (no declared type system to expose a real `__schema` from); `__typename` always available since it reveals no schema shape; `Schema(introspection=True)` opts into a minimal `__schema` listing root field names.

Delivered Aug 20, 2026: `src/mangoo/graphql.py` is a from-scratch tokenizer, recursive-descent parser, and executor (previously a single-field regex match). Fragments and directives are explicitly rejected with a clear error rather than a confusing parse failure -- not silently mis-parsed. See `tests/test_graphql.py` (40 tests) and `README.md#rest-and-graphql`.

### Acceptance criteria

- [x] All previously-passing GraphQL tests and example-site usage continue to work unmodified (`Schema(query={...})`, `schema.execute(query, request)`, single anonymous-field queries).
- [x] A resolver exception becomes a per-field GraphQL error (`{"data": {...: null}, "errors": [...]}`), not an unhandled exception reaching the WSGI layer.
- [x] A query exceeding `max_depth` or `max_fields` is rejected before any resolver runs.
- [x] Introspection is unavailable unless explicitly enabled, and `__typename` works either way.

## Week 7: template engine maturity

### Deliverables

- [x] Add `{% if/elif/else/endif %}` conditionals with `==`, `!=`, `in`, `and`, `or`, `not`, and string/number/`true`/`false`/`none` literals -- not arbitrary Python `eval`.
- [x] Add `{% for x in expr %}...{% endfor %}` loops, with `loop.index`/`loop.index0`/`loop.first`/`loop.last` available inside the body.
- [x] Add `{% extends "base.html" %}` + `{% block name %}...{% endblock %}` single-chain inheritance; a child overrides named blocks, unoverridden blocks keep the parent's default content, and multi-level chains (grandparent/parent/child) are supported.
- [x] Add per-file template caching, invalidated by mtime, so an edited template is picked up on the next render while an unchanged one skips re-parsing.
- [x] Add developer-mode diagnostics: `render_template(..., debug=True)` raises `TemplateRenderError`/`TemplateSyntaxError` (with template name and line number) on an undefined variable, a non-iterable loop target, or a block a child defines that the base template never declares -- instead of silently rendering nothing, which is what happens by default (`debug=False`) so a template typo degrades gracefully in production rather than 500ing.

Delivered Aug 20, 2026: `src/mangoo/templates.py` is now a real tokenizer, recursive-descent parser (both for tags and for expressions), and renderer, replacing the previous single-regex `{{ name }}`-only substitution. Still no arbitrary code execution, no fragments/filters/macros. See `tests/test_templates.py` (36 tests) and `README.md#templates`.

### Acceptance criteria

- [x] The previously-passing template test and its exact escaping/traversal-rejection behavior continue to work unmodified.
- [x] An undefined variable renders as empty text by default and raises in `debug=True`; an undefined path compares equal to `none` in a condition without ever displaying as the literal text "None".
- [x] A three-level `{% extends %}` chain (grandparent defines blocks, parent overrides one, child overrides another) renders correctly, and a circular `extends` chain is rejected with a clear error instead of infinite-looping.
- [x] Editing a template file on disk is reflected on the next render without restarting the process.

## Week 8: admin expansion

### Deliverables

- [x] Add model-admin registration: `admin_routes(database_path, models=[ModelAdmin(YourEntity)])` adds paginated list/add/edit/delete pages for any `mangoo.models.Entity` subclass, alongside the existing built-in `/admin` and `/admin/users` pages (unchanged).
- [x] Add pagination: `?page=N` on the list view, sized by `ModelAdmin(..., page_size=25)`.
- [x] Add validation: a blank required field or a value that fails a column's type coercion is a field-level form error (400, form re-rendered with the message), not an unhandled exception or a raw database constraint violation.
- [x] Add permissions: `ModelAdmin(..., can_access=predicate)` gates a model on any `predicate(user) -> bool`, defaulting to `user.is_superuser` -- the same mechanism `superuser_required` uses, so custom roles compose with existing auth.
- [x] Add change history: the edit page lists every create/update/delete recorded against that record, sourced from the existing audit log (`mangoo_audit_events`); updates record which field names changed, never the values, matching the audit log's existing no-sensitive-values convention.
- [x] Add CSRF protection to every add/edit/delete submission. This surfaced a real architecture gap: `csrf_protect` only checked an `X-Csrf-Token` header, which a plain HTML `<form>` cannot set -- fixed by teaching it to also accept a `csrf_token` form field, so both JSON/API clients and traditional server-rendered forms are covered by the same decorator.

Delivered Aug 20, 2026: `src/mangoo/admin.py` gained `ModelAdmin` and the routes it generates; `src/mangoo/security.py`'s `csrf_protect` gained the form-field fallback; `src/mangoo/request.py` gained `Request.form()`; `src/mangoo/testing.py`'s `TestClient` gained `form_data=` support. Writing real end-to-end tests for this (not just unit tests) surfaced and fixed two additional pre-existing bugs: `TestClient` never split a `?query` string out of the `path` argument into `QUERY_STRING`, so any URL with query parameters -- pagination included -- 404'd through it; and the CSRF check itself needed verifying against an actual cookie-authenticated session, not just a Bearer token, since Bearer requests correctly skip CSRF and would have silently hidden a broken check. See `tests/test_admin_models.py` (27 tests).

### Acceptance criteria

- [x] The previously-passing admin test (`admin_routes(database)` returns the built-in `/admin` and `/admin/users` routes, gated and audited exactly as before) continues to pass unmodified.
- [x] Creating, editing, and deleting a registered model's record works end-to-end through the real WSGI stack, including a cookie-authenticated session with a correct CSRF token, and is rejected with 403 when the token is missing or wrong.
- [x] A validation failure re-renders the form with a field-level error and a 400 status, not a 500.
- [x] A non-default `can_access` predicate is honored instead of the `is_superuser` default.
- [x] Pagination is stable: an out-of-range page clamps to the last page, and a non-numeric page falls back to page 1, rather than erroring.

## Week 9: fullstack UI framework, Stage 1

Two-stage plan, chosen deliberately: Stage 1 (this week) is a server-rendered
component system plus a first-party hypermedia runtime, built entirely
inside this Python package with no new tooling. Stage 2 was originally
expected to require Node.js/npm/a bundler for a JSX/virtual-DOM SPA
framework, deliberately unscoped and not attempted alongside Stage 1; it
was later re-scoped and delivered without that tooling -- see Week 11.

### Deliverables

- [x] Add a component system: `mangoo.ui.elements.tag()` builds any HTML5 element as a Python callable (`div`, `form`, `table`, ...); a component is simply a function returning the resulting `Markup`, with no base class or decorator to learn.
- [x] Add automatic escaping with an explicit opt-out: every child is HTML-escaped unless it is already `Markup` (another element's or component's output, or an explicit `raw(...)` call) -- composing components can't reintroduce an XSS hole by under- or double-escaping.
- [x] Add Pythonic attribute conversion: `class_` -> `class`, `for_` -> `for`, and any other underscore becomes a hyphen, so `hx_post="/save"` renders `hx-post="/save"` -- exactly what the hypermedia attributes below need.
- [x] Add a small built-in component library: `Page`, `Button`, `Alert`, `Card`, `Form`, `FormField`, `NavBar`, `Table`.
- [x] Add a first-party hypermedia JS runtime (`mangoo-ui.js`, one file, no build step): `hx-get`/`hx-post`/`hx-put`/`hx-delete` trigger on click (buttons/links) or submit (forms), fetch the URL, and swap the returned HTML fragment into `hx-target` using `hx-swap` (`innerHTML`/`outerHTML`/`beforeend`/`afterbegin`).
- [x] Integrate with the existing CSRF mechanism: the runtime reads the `mangoo_csrf` cookie and sends `X-Csrf-Token` automatically, so an `hx-post` to a `csrf_protect`-guarded view needs no extra client-side wiring.
- [x] Add `ui_routes()` to serve the JS/CSS by reusing the existing `static_routes` helper -- no new static-file-serving mechanism.
- [x] Add a small default stylesheet (`mangoo-ui.css`) so the built-in components look clean without any configuration.
- [x] Wire a real, browsable demo into `example_site` (`/ui-demo`) and a separate, focused end-to-end test proving components + hypermedia POST + CSRF genuinely work together against the real WSGI stack, not just in isolated unit tests.

Delivered Aug 20, 2026: `src/mangoo/ui/` (`elements.py`, `components.py`, `routes.py`, `static/mangoo-ui.{js,css}`). See `tests/test_ui_elements.py`, `tests/test_ui_components.py`, `tests/test_ui_routes.py`, and `tests/test_ui_hypermedia.py` (54 tests total across the UI layer). The JS runtime has no Python-side test runner in this repo (no Node.js test tooling introduced), so it was syntax-checked with `node --check` and verified against a real HTTP socket (not just the in-process `TestClient`) serving `example_site`'s `/ui-demo` page -- a real, disclosed limitation rather than an implied guarantee of JS-side test coverage.

### Acceptance criteria

- [x] A component composed of other components' output never double-escapes or under-escapes: `div(SomeComponent())` renders exactly what `SomeComponent()` produced, and `div(user_supplied_string)` is always escaped.
- [x] `ui_routes()` serves `mangoo-ui.js` and `mangoo-ui.css` with correct content types, ETags, and cache headers -- the same guarantees `static_routes` already provides elsewhere.
- [x] A component-rendered form with `hx_post` set, submitted through the real WSGI stack against a cookie-authenticated session, is rejected with 403 when the CSRF token is missing or wrong and succeeds (returning the expected fragment) when it's correct.
- [x] The packaged wheel actually contains `mangoo/ui/` and its static assets, verified with a real `python -m build --wheel`, not assumed from directory layout alone.
- [x] The demo page and the automated end-to-end test hit the JS runtime's exact request shape (form-encoded body, CSRF header/field, fragment response) rather than a simplified stand-in.

## Week 10: linting and formatting

### Deliverables

- [x] Add [ruff](https://docs.astral.sh/ruff/) for both linting (`E`, `F`, `I`, `UP`, `B` rule groups: pycodestyle, pyflakes, import sorting, pyupgrade, bugbear) and formatting, configured in `pyproject.toml` under `[tool.ruff]`.
- [x] Set `line-length = 320` to match this codebase's existing dense one-liner style rather than forcing a wholesale rewrite; `ruff format` still reformats everything else (blank lines around docstrings/classes, import order, collapsing multi-line expressions that now fit) at that width.
- [x] Explicitly exclude `E501` (line-too-long) from the enforced rule set rather than silently permitting long lines everywhere: three specific lines (two generated-project template strings in `scaffold.py`, one embedded Swagger UI HTML/JS string in `swagger.py`) are each already one deliberate unit -- a whole file's or page's contents as one string literal -- that wrapping would only make harder to read. Everything else, including the package's own `__all__` list and a status-text dict, already fits within the 320-character limit once `ruff format` collapsed what it could.
- [x] Fix every other real finding by hand rather than blanket-suppressing the rule: an unused `sys` import (`autoreload.py`), an unused `input_` import (a test file), a missing `raise ... from exc` losing exception context (`templates.py`), three PEP 8 compound-statement one-liners (`testing.py`, a test file), with two narrow, explained `# noqa` comments for the two cases where the rule's general advice doesn't apply to this specific line (an intentionally length-mismatched `zip()` in `metrics.py`; a deliberately broad `assertRaises(Exception)` for a frozen-dataclass mutation test whose exact exception type is a `dataclasses` implementation detail).
- [x] Add a dedicated `lint` job to `.github/workflows/ci.yml` (`ruff check .` then `ruff format --check .`), separate from and faster than the test matrix, required on every push and pull request.
- [x] Run both across the entire repository, not just `src/mangoo/` -- `tests/`, `example_site/`, and `hatch_build.py` are all lint- and format-clean too, including fixing a real un-sorted-imports issue in `example_site` and adding one more explained `# noqa` (`example_site/manage.py`'s `sys.path` fix-up must precede its import, the standard shape of that pattern).

Delivered Aug 21, 2026. See `.github/workflows/ci.yml`'s `lint` job and `pyproject.toml`'s `[tool.ruff]`/`[tool.ruff.lint]` sections.

### Acceptance criteria

- [x] `ruff check .` and `ruff format --check .` both exit 0 against the entire repository (not just the package under test).
- [x] The full test suite still passes after every auto-fix and manual fix (333 tests, unchanged pass count from before this work started).
- [x] Every `# noqa` in the codebase carries an inline explanation of why that specific line is an exception, not a bare suppression.
- [x] No line exceeds the configured 320-character limit except the three pre-identified, individually-justified exceptions.

## Week 11: fullstack UI framework, Stage 2 (no Node.js/npm)

Stage 1 shipped server-rendered components and a hypermedia runtime. This
was originally going to require Node.js/npm for a JSX/virtual-DOM SPA
framework; re-scoped, on request, to deliver genuine client-side reactivity
and routing as hand-written vanilla JS instead -- no build step, same as
Stage 1. A real Node.js/npm-based SPA framework remains a distinct,
separate, still-unscoped possibility (see the future-readiness table above).

### Deliverables

- [x] Add a signals core to a new `mangoo-reactive.js` file: `signal(v)`, `computed(fn)`, `effect(fn)`, SolidJS/Preact-signals style (no virtual DOM) -- reading `.value` inside a running effect subscribes it, writing re-runs exactly the subscribers that read it, with correct dependency cleanup (an effect that stops reading a signal on one branch of a conditional stops being notified by it), verified with a standalone Node smoke test before it was ever wired into the DOM-facing code.
- [x] Add declarative `mgo-*` directives on top of the signals core: `mgo-data` (scope), `mgo-text`, `mgo-show`, `mgo-model`, `mgo-on-<event>`, `mgo-bind-<attr>` -- a distinct prefix from Alpine.js's `x-`, since this is a smaller, non-compatible reimplementation, not a drop-in replacement. Expressions evaluate through a `Proxy` inside a `with`-scoped dynamic function, so plain identifiers read and write the underlying signal (`count`, `count = count + 1`, `count++`) -- verified with a standalone Node smoke test of the exact mechanism (reads, writes, increments, `$event` passthrough, and fallthrough to real globals like `Math`) before wiring it to the DOM.
- [x] Add `mgo-boost` client-side routing to the existing `mangoo-ui.js` (not a new file, since it reuses that file's `swap()`/`dispatch()` helpers): a boosted link navigates via `fetch` + `DOMParser` + `history.pushState`, swapping the element both pages mark `mgo-boost-target` (falling back to `document.body`); `popstate` re-fetches and swaps for back/forward. Forms are not boosted in this version -- `hx-get` already covers swap-instead-of-navigate for forms.
- [x] Wire `mangoo-reactive.js` to mangoo-ui.js's existing `mangoo:swapped` event so directives inside hypermedia- or boost-swapped content work with no extra setup. Found and fixed a real bug while building this: the event's `event.target` is whichever element *triggered* the request (a button, a form, or `document` for a boost), never the swapped container itself -- fixed by adding the actual swapped element to `event.detail.target` at both dispatch sites, which the reactive runtime now reads instead.
- [x] Add `mangoo.ui.reactive.data(**kwargs)`: JSON-encodes keyword arguments for safe embedding as an `mgo-data` value, reusing the existing HTML-attribute escaping in `mangoo.ui.elements` rather than adding new escaping code. No other new Python API was needed -- directive attributes like `mgo_on_click=` already convert to `mgo-on-click=` through the attribute-name conversion Stage 1 already built.
- [x] Wire a real, browsable demo into `example_site`: `/ui-demo` gained a client-side counter (`mgo-data`/`mgo-text`/`mgo-on-click`), and a new `/ui-demo/two` page is reachable via an `mgo-boost` nav -- proving both pieces against two real routes, not a simplified stand-in.

Delivered Aug 21, 2026: `src/mangoo/ui/static/mangoo-reactive.js` (new), `mangoo-ui.js` (extended), `src/mangoo/ui/reactive.py` (new). See `tests/test_ui_reactive.py`, `tests/test_ui_boost.py`, and the extended `tests/test_ui_routes.py`. JS correctness was verified two ways: `node --check` for syntax (no Node test runner or `package.json` was introduced -- this is local developer tooling only, same as Stage 1's precedent), and standalone Node scripts exercising the actual signals/proxy algorithm logic outside the DOM before it was wired in -- a stronger check than Stage 1 had, since Stage 1's logic was simple attribute-driven fetch/swap while Stage 2's `with`/`Proxy` mechanism was novel enough in this codebase to warrant direct verification.

### Acceptance criteria

- [x] An effect that conditionally reads one of two signals depending on a branch correctly stops being notified by the signal it no longer reads, and correctly starts being notified by the one it newly reads -- proven by a standalone smoke test, not assumed.
- [x] `mgo-data=data(...)`'s JSON round-trips correctly through HTML-attribute escaping: `tests/test_ui_reactive.py` decodes the rendered attribute the same way a browser would and confirms it parses back to the original mapping.
- [x] Both `/ui-demo` and `/ui-demo/two` render 200 with the `mgo-boost`/`mgo-boost-target`/`mgo-data` markers mangoo-reactive.js looks for, proven against the real WSGI stack via `TestClient`, not a hand-built minimal app only.
- [x] `ui_routes()` serves `mangoo-reactive.js` with the correct content type, the same guarantee already proven for `mangoo-ui.js`/`mangoo-ui.css`.
- [x] Documentation (`README.md`, `BUILD_FROM_SCRATCH.md`) states this version's real limitations plainly -- no nested `mgo-data` scopes, no update batching, no `.prevent`/`.stop` modifiers, forms not boosted -- rather than implying parity with Alpine.js/SolidJS.

## Week 12: rebrand Pulse as Signals

`mangoo.addons.pulse` was forensically found (see `INDUSTRY_LEADERSHIP_ROADMAP.md`
Pillar A) to be the more industry-familiar term "Signals" hiding under an
earlier prototype name. This week is a rename, not a rebuild: the design
(deterministic ordering, error isolation, sensitive-key rejection,
always-async delivery) is unchanged.

### Deliverables

- [x] Move the implementation to `mangoo.addons.signals`: `SignalBus` (was `PulseBus`), `SignalEvent` (was `PulseEvent`), `SignalSubscriber` (was `PulseSubscriber`). Event names themselves (`request.started`, `auth.login_succeeded`, ...) are unchanged.
- [x] Rename the Rust dispatcher: `mangoo_native.PulseDispatcher` -> `mangoo_native.SignalDispatcher` (`crates/mangoo-python/src/lib.rs`), rebuilt and verified end to end -- both the native path and the pure-Python `ThreadPoolExecutor` fallback were individually confirmed still correct after the rename.
- [x] Turn `mangoo.addons.pulse` into a deprecated shim: `PulseBus`/`PulseEvent`/`PulseSubscriber` are the exact same objects as their `Signal*` replacements (`PulseBus is SignalBus`), not copies, so isinstance checks and behavior are unaffected; importing the shim raises one `DeprecationWarning`.
- [x] Add a `signals=` keyword argument to `create_application`, `auth_routes`, `admin_routes`, and `migrations.apply`; keep `pulse=` working on all four as a deprecated alias (warns once per call, and defers to `signals=` if both are given) rather than a hard break.
- [x] Fix `tests/test_signals_addon.py`'s import (it already had the right filename, pointing at the wrong, soon-to-be-deprecated module) and delete the orphaned `src/mangoo/addons/signals/__pycache__/` bytecode that predated any real source at that path.
- [x] Rename `tests/test_pulse_integration.py` -> `tests/test_signals_integration.py`, updated to the new primary API, and add a dedicated `tests/test_signals_deprecation.py` proving every one of the four `pulse=` call sites -- not just one representative case -- still functions and still warns.

Delivered Aug 21, 2026. See `src/mangoo/addons/signals/`, the deprecated shim at `src/mangoo/addons/pulse/__init__.py`, and `tests/test_signals_addon.py`, `tests/test_signals_integration.py`, `tests/test_signals_deprecation.py` (17 tests across the three).

### Acceptance criteria

- [x] `PulseBus is SignalBus` (and the same for `PulseEvent`/`PulseSubscriber`) -- the alias is identity-equal, not a re-implementation that could drift.
- [x] Every one of the four `pulse=` call sites (`create_application`, `auth_routes`, `admin_routes`, `migrations.apply`) still delivers events correctly end-to-end and raises exactly one `DeprecationWarning` per call.
- [x] Passing both `signals=` and `pulse=` to the same call uses `signals=` and ignores `pulse=` (still warning), rather than silently picking one unpredictably.
- [x] `mangoo_native.SignalDispatcher` is used when the native extension is built, and the pure-Python fallback is used when it isn't -- both paths verified directly, not just one assumed from the other.
- [x] No orphaned bytecode or stale filenames remain anywhere under `src/mangoo/addons/`.

## Week 13: type checks -- `py.typed` and mypy as a required CI job

The framework's public API had type hints throughout but nothing verified
they were internally consistent, and nothing told a downstream project's
own type checker that Mangoo's annotations were trustworthy to check
against rather than treated as implicit `Any`. This closes both gaps.

### Deliverables

- [x] Add `src/mangoo/py.typed` (PEP 561); confirmed present in the built wheel (`mangoo/py.typed`) via a real `pip wheel` build, not just assumed from the file existing in source.
- [x] Add `[tool.mypy]` to `pyproject.toml`, scoped to `src/mangoo`, with narrow `[[tool.mypy.overrides]]` for `mangoo_native` (optional, self-built Rust extension with no stubs) and `psycopg`/`pymysql` (optional DB drivers not installed in the default dev environment) -- everything else checked at mypy's default strictness, no blanket `ignore_missing_imports`.
- [x] Fix all 44 real mypy errors this surfaced across 12 files rather than suppressing them: added `ClassVar` declarations on `Entity` for the metaclass-assigned `__table__`/`_columns` attributes (`models.py`), typed several untyped-empty-list/dict locals that mypy couldn't infer (`templates.py`, `graphql.py`, `swagger.py`, `testing.py`, `management/commands.py`), fixed a real (if narrow) null-safety gap where `os.path.dirname()` could receive `None` from an unguarded `module.__file__` (`management/commands.py`), and gave `collections.mango_list()` an honest `Any` return type since it genuinely returns one of two unrelated classes depending on whether the native extension is built.
- [x] Add a separate, required `typecheck` CI job (`.github/workflows/ci.yml`) running `mypy` against the pure-Python fallback build -- kept separate from `lint` (ruff) and `test` (pytest) rather than folded into either, matching this repo's existing one-job-per-concern CI pattern.
- [x] Document type checking in `README.md`'s "Testing and CI" section.

Delivered Aug 26, 2026. Full test suite (353 tests) and `ruff check`/`ruff format --check` stay clean after every fix; `mypy` reports zero errors across all 42 source files.

### Acceptance criteria

- [x] `mypy` (using `pyproject.toml`'s config) reports zero errors against `src/mangoo`.
- [x] `mangoo/py.typed` is present in a real built wheel, not just the source tree.
- [x] No fix suppresses a genuine issue with a bare `# type: ignore` -- the one `# type: ignore[misc]` in `ui/elements.py` is a documented, deliberate same-name conditional-definition pattern (a void-element builder and a children-accepting builder returned from the same factory based on a runtime flag), not a masked error.
- [x] The full pytest suite and ruff lint/format remain green after every type-annotation change.

## Week 14: revert the UI framework (`mangoo.ui`) -- pure server-rendered HTML only

Product direction changed: Mangoo should ship strictly server-rendered
HTML with zero client-side JavaScript, rather than maintain a hypermedia
runtime and a signals/reactivity runtime alongside the plain template
renderer. This reverts Week 9 and Week 11's `mangoo.ui` package in full --
not a defect in what was built or a performance fix (the htmx runtime had
no effect on server-side render throughput; client-side JavaScript runs in
the browser and is invisible to `oha`'s request-timing measurements), a
scope decision to keep the framework to one rendering model.

### Removed

- [x] `src/mangoo/ui/` in full: `elements.py`, `components.py`, `reactive.py`,
      `routes.py`, `static/mangoo-ui.js`, `static/mangoo-ui.css`,
      `static/mangoo-reactive.js`.
- [x] `tests/test_ui_elements.py`, `test_ui_components.py`, `test_ui_routes.py`,
      `test_ui_hypermedia.py`, `test_ui_boost.py`, `test_ui_reactive.py` (54 tests).
- [x] `example_site/project/urls.py`'s `/ui-demo`, `/ui-demo/items`,
      `/ui-demo/two` routes and their views; `tests/test_example_site.py`'s
      corresponding test.
- [x] `benchmarks/mangoo_app`'s htmx-specific additions from the same
      session: the `/posts/live` demo page (`page_htmx.html`), the
      `/posts/fragment` endpoint (`fragment.html`) and its
      `benchmarks/hypermedia_fragment.py` benchmark, and
      `benchmarks/verify_htmx_wiring.py` -- all existed solely to
      demonstrate/verify the now-removed htmx capability.
- [x] All "UI framework" documentation: `README.md`'s "UI framework"
      section, `BUILD_FROM_SCRATCH.md`'s section 10 (replaced with a short
      note on the removal and why), and `PROJECT_MINDMAP.md` references,
      if any.

### What stays

`mangoo.templates` (server-rendered HTML via `render_template()`) is
unaffected and remains the only supported way to produce HTML. Nothing
else in the framework depended on `mangoo.ui` -- it was never imported
from `src/mangoo/__init__.py` or any other core module, so this removal
has no ripple effect on routing, auth, admin, GraphQL, or REST.

Delivered Aug 27, 2026. Full test suite (306 tests, down from 373 -- the
67 removed tests are exactly the deleted UI test files), `ruff check`/
`ruff format --check`, and `mypy` all stay clean after the removal.

### Acceptance criteria

- [x] No file under `src/mangoo/` imports anything from a `mangoo.ui`
      module (there is no such module anymore).
- [x] No test, example, or benchmark references `hx-*`/`mgo-*` attributes,
      `mangoo-ui.js`/`mangoo-ui.css`/`mangoo-reactive.js`, or `ui_routes()`.
- [x] `benchmarks/run.py`'s published cross-framework numbers are
      unaffected -- they were already isolated from the UI framework
      before this removal (the htmx demo page was always a separate route
      from the benchmarked `/posts` page, specifically to prevent this
      kind of cross-contamination).

## Week 15: rebuild the UI framework -- Stage 1 only, leaner JS, benchmarked Rust fragment rendering

Product direction changed again: bring the hypermedia capability back,
narrower in scope than before (Stage 1 only -- no client-side signals
layer this time), with two explicit new requirements: benchmark Rust for
the specific shape of rendering work htmx fragments do before deciding
whether to use it, and make the JS runtime itself leaner. None of Week
14's removed files existed in git history (they were created and deleted
within the same uncommitted working tree), so this is a fresh
implementation, not a restore.

### Delivered

- [x] Rebuilt `src/mangoo/ui/`: `elements.py` (`tag()` builder, attribute
      conversion, `Markup`), `components.py` (`Page`, `Button`, `Alert`,
      `Card`, `Form`, `FormField`, `NavBar` -- no `Table` this time, not
      needed by anything currently using this package), `routes.py`
      (`ui_routes()`), and `static/mangoo-ui.{js,css}`. No `reactive.py`
      and no `mangoo-reactive.js` -- Stage 2 is deliberately not part of
      this rebuild.
- [x] **Leaner `mangoo-ui.js`:** the two separate `click` listeners the
      original had (one for `hx-*` triggers, one for `mgo-boost` links)
      are now a single delegated listener -- one ancestor-chain walk per
      click instead of two, and one fewer listener registered on
      `document`. File size is essentially unchanged (~6.1 KB vs ~6.07 KB
      before); the improvement is fewer runtime operations per
      interaction, not a smaller download, and is stated that way rather
      than oversold as "smaller."
- [x] **Rust benchmarked for fragment rendering specifically, per the new
      requirement -- and unlike full-page templates, it won.**
      `crates/mangoo-core` gained `render_fragment_items`, a hardcoded
      renderer for the exact compact `<ul><li>...</li>...</ul>` markup
      `mangoo.ui`'s `ul(*(li(...) for ...))` builders produce.
      `benchmarks/native_htmx_fragment.py` compared it against the Python
      component builders for two sizes: the benchmark app's 20-item list,
      and a single-item fragment (a more realistic "add one item"
      interaction). **Result (two runs): native wins 8.33x-8.44x on the
      20-item fragment and 4.30x-4.32x on the single item** -- a real
      reversal from every general-purpose Rust template candidate this
      project tried, because a fragment is naturally narrow (one specific,
      small, fixed piece of markup) in a way a full page with arbitrary
      control flow is not, so a hardcoded renderer's lack of generality is
      much less of a real limitation here.
  - **Checked whether this shows up at the HTTP level, rather than assuming
    either way:** added `posts_fragment_native` (identical DB query,
    Rust-rendered output) alongside the existing Python `posts_fragment`,
    and extended `benchmarks/hypermedia_fragment.py` to a three-way
    comparison. **Result: a real but modest 1.05x-1.08x HTTP-level win**
    (two runs) -- smaller than the isolated 4x-8x figure since the
    per-request SQLite connection cost (see the `template_render`
    investigation above) still dominates total request time, but *not*
    zero this time, unlike the compiled-template-engine case. The
    difference: a fragment endpoint's baseline latency is somewhat lower
    than a full page's, so the rendering slice is proportionally larger.
  - **Not wired into `mangoo.ui`'s shipped API.** `render_fragment_items`
    only ever handles this one exact fragment shape; generalizing it would
    reintroduce precisely the problem that sank the general Rust template
    engine. Documented instead as a worked example (`posts_fragment_native`)
    for a project author who wants to do this for their own hot,
    narrow, high-traffic fragment.
- [x] Restored `example_site/project/urls.py`'s `/ui-demo` (Stage 1 only:
      a component-rendered page with an `hx-post` "add item" button, no
      client-side counter/boosted-navigation demo, since those were
      Stage 2 features not part of this rebuild) and
      `tests/test_example_site.py`'s corresponding test.
- [x] Restored `benchmarks/mangoo_app`'s `/posts/live` (htmx demo, built
      with `mangoo.ui` components rather than a template file this time),
      `/posts/fragment`, `/posts/fragment-native`, and
      `benchmarks/verify_htmx_wiring.py` (updated for the new markup).
- [x] New test suite: `tests/test_ui_elements.py`, `test_ui_components.py`,
      `test_ui_routes.py`, `test_ui_hypermedia.py` (real hx-post + CSRF
      round trip against the WSGI stack), `test_ui_boost.py` -- 28 tests,
      matching Stage 1's original test coverage shape minus anything
      Stage-2-specific.

Delivered Aug 28, 2026. Full test suite (339 tests), `ruff check`/
`ruff format --check`, and `mypy` all stay clean. `cargo test -p
mangoo-core` (14 tests, including two rendering-output assertions) passes;
the packaged `pip install -e .` build (not just a manual DLL copy) was
verified to produce a working `mangoo_native.render_fragment_items`.

### Acceptance criteria

- [x] `mangoo.ui`'s public API (`Page`, `Button`, `Alert`, `Card`, `Form`,
      `FormField`, `NavBar`, element builders, `ui_routes()`) works
      end-to-end against the real WSGI stack, not just in isolated unit
      tests -- proven by `test_ui_hypermedia.py`'s hx-post + CSRF round
      trip and `verify_htmx_wiring.py`'s fragment-content-matches-swap-target
      check.
- [x] No Stage-2 (signals/reactivity) code, tests, or documentation exists
      -- this rebuild is Stage 1 only, by design.
- [x] The Rust fragment-rendering decision is backed by a real measurement
      at both the isolated-function level and the HTTP level, not asserted
      from the isolated number alone -- consistent with every other
      benchmark claim in this document.
- [x] The published cross-framework `benchmarks/run.py` numbers remain
      unaffected -- `/posts/live`, `/posts/fragment`, and
      `/posts/fragment-native` are all separate routes from the
      benchmarked `/posts` page.

## Week 16: a real documentation website, versioned like Django's

**Status: planned, not started.** Mangoo's documentation today lives entirely
in root-level Markdown files (`README.md`, `DEPLOYMENT.md`,
`BUILD_FROM_SCRATCH.md`, this roadmap) plus generated Swagger/OpenAPI for an
individual application's own API. There is no browsable documentation
*website* for the framework itself, and no way for a reader to view docs for
a specific past release the way
[docs.djangoproject.com](https://docs.djangoproject.com/) or
[fastapi.tiangolo.com](https://fastapi.tiangolo.com/) let a reader pick
"these are the docs for the version I actually have installed."

### Goal

A static documentation site, built from Markdown/reStructuredText source
already living in this repo, with:

- A real information architecture: a getting-started tutorial, topic guides
  (routing, models/migrations, auth, admin, templates, GraphQL, ASGI,
  Signals, testing, deployment), and a generated API reference -- not one
  long README.
- **Version-based documentation**, Django's defining feature in the example
  given: a version switcher lets a reader view the docs that match the
  Mangoo release they have installed (e.g. `/en/1.2/`, `/en/dev/` for the
  unreleased tip), not only the latest tip -- so upgrading the docs site
  never silently invalidates what an older, still-supported release's users
  are reading.
- Full-text search across the current version's pages.
- A style consistent with this project's existing documentation standard --
  every claim links to a real test or a real, runnable example, matching how
  `README.md` and the roadmap documents already operate; no unverifiable
  marketing language.

### Deliverables

- [ ] Pick and stand up a static-site generator that supports versioned
      docs out of the box rather than requiring a hand-rolled version
      switcher: evaluate Sphinx (`sphinx-multiversion`/`sphinx-rtd-theme`,
      the actual tooling Django's own docs site is built with) against
      MkDocs (`mkdocs-material` + `mike` for versioning) before choosing --
      per this project's own standing principle of benchmarking/evaluating
      before committing, not picking one on assumption.
- [ ] Restructure existing content into a proper source tree (e.g. `docs/`)
      with a real navigation hierarchy: quickstart, tutorial, topic guides
      (one per subsystem: routing, models/migrations, auth, admin,
      templates, GraphQL, REST/OpenAPI, ASGI, Signals, testing, CI,
      deployment, security), and a reference section -- migrating
      `README.md`/`DEPLOYMENT.md`/`BUILD_FROM_SCRATCH.md`'s content into it
      rather than duplicating it in two places.
- [ ] Generate API reference pages from the package's existing docstrings
      and type hints (already enforced by the Week 13 mypy/`py.typed` work)
      rather than hand-writing and re-drifting a second copy of the public
      API surface.
- [ ] Wire the version switcher to this project's actual release history
      (git tags once releases exist) so each published version's docs stay
      reachable and frozen after a newer version ships -- the specific
      behavior the Django example demonstrates.
- [ ] Add a CI job that builds the docs site on every push/PR (catching
      broken links/build errors before merge, the same "required CI gate"
      pattern already used for tests/lint/typecheck) and a separate,
      explicit publish step (GitHub Pages or an equivalent static host) --
      publishing itself needs its own sign-off before wiring, since it's a
      user-visible, externally-reachable change, not a local build.
- [ ] Cross-link from `README.md` to the new site once it exists, without
      deleting `README.md`'s own content -- the repo README stays the
      first thing a GitHub visitor sees; the docs site is the deep-reference
      destination it points to, the same relationship Django's own
      `README.rst` has with `docs.djangoproject.com`.

### Acceptance criteria

- A reader can select a specific past Mangoo version and see documentation
  that matches that version's actual behavior, not the latest tip.
- Every topic guide links to the real test(s) or example app
  (`example_site/`, `benchmarks/`) demonstrating the behavior it describes,
  matching this project's existing no-unverifiable-claims standard.
- The docs site builds in CI on every push/PR and fails the build on a
  broken internal link or a build error.
- Full-text search returns relevant pages for a plausible query (e.g.
  "migrations", "CSRF", "ASGI").

### Sequencing note

This is new scope, not yet estimated in the future-readiness table below --
add an effort estimate once the generator choice (first deliverable above)
is made, since Sphinx-with-multiversion and MkDocs-with-mike carry
meaningfully different setup costs.

## Week 17: gRPC support

**Status: planned, not started.** Mangoo currently offers two request
surfaces -- REST (`api_route`, OpenAPI/Swagger) and GraphQL
(`mangoo.graphql.Schema`/`graphql_view`) -- both served over the same
WSGI/ASGI/Rust HTTP/1.1 transports. gRPC would be a third, for the case
those two don't cover well: typed service-to-service calls (an internal
microservice boundary, a mobile/desktop client generated from a `.proto`
contract) where a REST/JSON envelope adds overhead a binary,
strongly-typed RPC contract avoids.

### Why this is a separate transport, not a mode of the existing servers

gRPC requires real HTTP/2 (framed binary messages, not text HTTP/1.1) --
the same feature this project's custom Rust runtime deliberately left out
of scope (see `INDUSTRY_LEADERSHIP_ROADMAP.md`'s "Optional TLS
termination... HTTP/2 stays out of scope, with reasoning"): it is built
around multiplexing many streams over one connection, which does not fit
that server's synchronous thread-per-connection design without
effectively becoming its own scheduler. That reasoning does not change
here -- gRPC support should **not** be built by retrofitting HTTP/2 into
`mangoo_native`'s hand-written server. The realistic path is `grpcio`
(the standard Python gRPC library, itself a mature C-core HTTP/2
implementation) running as its own server process/thread, alongside --
not instead of -- the existing WSGI/ASGI/Rust HTTP servers, the same way
`mangoo.asgi` sits alongside `mangoo.runtime` today as an additive,
opt-in transport rather than a replacement.

### Goal

- Define Mangoo services from `.proto` contracts using the standard
  `grpcio-tools` codegen, not a hand-rolled schema format -- consistent
  with GraphQL and REST both already generating their documentation
  (OpenAPI/introspection) from code rather than a second, hand-maintained
  spec.
- Reuse the framework's existing auth/session primitives (`mangoo.auth`)
  for gRPC's own metadata-based auth (interceptors), rather than a
  parallel authentication system -- the same "one auth system, several
  transports" pattern `mangoo.asgi` already establishes for WSGI vs. ASGI.
- Reuse Signals (`mangoo.addons.signals`) for gRPC request lifecycle
  events, matching how `request.started`/`request.finished` already fire
  identically from `mangoo.runtime` and `mangoo.asgi`.
- A `mango newproject`-scaffolded project should be able to add a gRPC
  service with roughly the same amount of ceremony `api_route`/
  `graphql_view` need today -- not a bolt-on that feels like a separate
  framework.

### Deliverables (not yet scoped in detail -- first pass, to be refined before work starts)

- [ ] Evaluate `grpcio` vs. `grpclib` (an asyncio-native alternative) the
      same way the documentation-website generator choice and the Rust
      runtime's crypto provider were evaluated before committing --
      benchmark and check platform/build requirements (a C extension
      like `grpcio` needs wheels or a build toolchain per platform,
      worth confirming before it becomes a hard dependency) rather than
      picking on assumption.
- [ ] Design how a `.proto`-defined service maps to a Mangoo view function
      (or a new, distinct decorator/registration shape if `api_route`'s
      shape doesn't fit RPC semantics) -- unary calls first; streaming
      (server-streaming, client-streaming, bidirectional) explicitly
      out of scope for a first pass unless a concrete need emerges, since
      streaming semantics are a materially bigger design surface than
      REST/GraphQL's request/response model.
- [ ] Wire authentication via gRPC interceptors reusing `mangoo.auth`'s
      session/token verification, not a parallel implementation.
- [ ] Wire Signals events (`grpc.call_started`/`grpc.call_finished` or
      similar) matching the existing `request.*` event naming convention.
- [ ] Decide the process/port model: a gRPC server needs its own listening
      port (HTTP/2, distinct from the HTTP/1.1 REST/GraphQL port) --
      document how `mango launch` starts it (a separate command, a second
      thread/process alongside the existing dev server, or left as a
      separately-run process like a Celery worker would be) before
      building the developer-facing command.
- [ ] Benchmark against the existing REST `db_list`/`json_echo` scenarios
      for an apples-to-oranges-but-still-informative comparison (gRPC's
      binary protocol vs. JSON), following this project's standing
      practice of publishing real numbers rather than asserting a
      performance claim.

### Acceptance criteria

- A scaffolded project can define a `.proto` service and have Mangoo
  generate and wire the server code with a comparably small amount of
  glue to `api_route`/`graphql_view` today.
- Authentication and Signals events work identically in spirit (though
  necessarily different in mechanism) to how they already work for REST
  and GraphQL.
- A published, reproducible benchmark exists before any performance claim
  is made in documentation, matching every other transport in this
  project.
- Full test coverage (unit tests for service registration/auth/Signals
  wiring, an integration test hitting a real gRPC server) before this is
  marked delivered, matching the `Definition of done` standard below.

### Sequencing note

Not yet estimated -- the `grpcio`-vs-`grpclib` evaluation (first
deliverable above) needs to happen before an effort estimate is
meaningful, the same dependency the documentation website's Sphinx-vs-
MkDocs choice has on its own estimate.

## Later improvements

See [INDUSTRY_LEADERSHIP_ROADMAP.md](INDUSTRY_LEADERSHIP_ROADMAP.md) for the
next, more ambitious chapter once this document's near-term items are done:
a published benchmark suite against FastAPI/Django/Next.js and a higher
developer-experience bar (typed public API, proactive diagnostics).

- Fullstack UI framework: the original Stage 1 (hypermedia, Week 9) + Stage 2 (client-side signals, Week 11) shipped, were fully reverted (Week 14), and Stage 1 alone was rebuilt with a leaner JS runtime and a benchmarked-and-shipped Rust option for fragment rendering (Week 15). Stage 2 (client-side signals/`mgo-data` reactivity) remains deliberately not rebuilt, keeping the framework to one hypermedia model. A genuine Node.js/npm-based SPA framework (JSX, virtual DOM, a bundler) remains a distinct, separate, unscoped possibility.
- Template inheritance, loops, conditionals, caching, and developer-mode diagnostics are delivered (see Week 7). Remaining, if ever needed: fragments, filters, and macros -- not currently planned.
- Model-admin registration, pagination, validation, permissions, and change history are delivered (see Week 8). Remaining, if ever needed: bulk actions, list filtering/search, and inline edits -- not currently planned.
- GraphQL arguments, mutations, nesting, validation, limits, and introspection policy are delivered (see Week 6). Remaining, if ever needed: fragments, directives, and field aliases -- not currently planned, since they add real complexity for a framework this size.
- Packaging `mangoo_native` as part of a normal install is delivered (see Week 5 and `src/mangoo/addons/signals/README.md`). Remaining: benchmark automation and measured Rust migrations for routing, JSON serialization, authentication operations, and database execution -- schedule each only once a measured win justifies it.
- CI matrix across supported Python versions is delivered (`.github/workflows/ci.yml`); linting and formatting are delivered (see Week 10); type checks are delivered (see Week 12); dependency scanning, release notes, and package publishing remain.
- A native Rust WSGI server (`SERVER['ENGINE'] = 'rust'`) is delivered -- see the future-readiness table above and `INDUSTRY_LEADERSHIP_ROADMAP.md`. It is development/load-testing tooling, not a claim of production-readiness; `DEPLOYMENT.md`'s gunicorn/waitress guidance is unchanged as the production recommendation. A production-hardened version (TLS termination, HTTP/2) remains unscoped.
- Mangoo Signals (formerly Pulse): delivered, see Week 12. The opt-in registry provides connect/disconnect, deterministic ordering, error isolation, sensitive-key rejection, and always-async delivery through a Rust-native worker pool (Python `ThreadPoolExecutor` fallback when `mangoo_native` is not built) in `src/mangoo/addons/signals/`; `create_application`, `auth_routes`, `admin_routes`, and `migrations.apply` all accept a `signals=` argument (`pulse=` still works, deprecated) to emit request, authentication, administration, and migration events. Remaining work is optional: a generated project could wire a default bus in automatically instead of requiring each application to pass one explicitly.

## Definition of done

An item is complete only when:

1. The implementation is documented in the build guide and public API documentation.
2. Unit and regression tests cover its normal, invalid, and security-relevant paths.
3. The full test suite passes.
4. Required integration tests pass for supported databases or services.
5. Configuration defaults are safe, and any operational prerequisite is documented.
