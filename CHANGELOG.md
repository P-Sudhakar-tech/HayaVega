# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- `hayavega-admin newproject` generated an entry point named `hayavega.py`,
  which shadowed the installed `hayavega` package on `sys.path` and made the
  generated project unrunnable as documented (`python hayavega.py launch`
  failed). The scaffold now generates `manage.py`, matching the Django-style
  convention this project's docs already described.
- Scaffolded projects now wire `admin_routes()` into `config/routes.py` by
  default, instead of requiring a manual follow-up step to reach the admin
  UI.
- Fixed a real performance regression in `static_file` serving: `Path.resolve()`
  was called on every request, accounting for over a third of that route's
  CPU time. Resolved paths and file content are now cached (mtime-invalidated,
  same pattern as the template compile cache), turning a 0.41-0.54x loss into
  a 1.44-1.45x win against the reference WSGI server. See `docs/performance.md`.

### Investigated
- Memory usage across the four benchmarked routes (`json_echo`, `db_list`,
  `template_render`, `static_file`) was profiled with `tracemalloc` peak
  values and found already minimal (1.75-5.26 KiB per request, no growth).
  No fix was needed; see `NEXT_RELEASE_ROADMAP.md` Phase 6 for the full
  finding and the follow-ups it left (a benchmark-suite memory metric, and
  documenting the static-file cache's lifetime characteristic for
  large-scale media hosting).

## [0.1.0] - 2026-11-12

Initial public release, published to PyPI via GitHub Actions trusted
publishing.

### Added
- **Core framework**: routing, class-based and function views, middleware,
  a Django-style ORM with migrations (checksummed, transactional, with
  schema-diff detection for column type/nullability/uniqueness changes).
- **Rust-accelerated runtime**: an in-built, settings-driven Rust WSGI
  server (`engine = "rust"`) with TLS termination via `rustls`, hardened
  against known raw-server failure modes; a Rust-backed SQLite connection
  serializer; a hand-written GraphQL lexer/parser; and Signals, an event
  bus with a Rust-native worker pool for always-async delivery.
- **APIs**: `api_route` with automatic OpenAPI/Swagger generation, Bearer
  auth scheme support, and API versioning; a hand-written GraphQL engine
  with depth limits and introspection control; ASGI support for
  `async def` views with zero migration required for existing sync code.
- **Auth & security**: password reset, email verification, session
  rotation/management, CSRF protection, rate limiting (opt-in), CORS and
  WebSocket origin checking (opt-in), a production security-review
  checklist, and a hardened-by-default posture consistent with the
  project's opt-in philosophy.
- **Admin, templates, static files, testing**: a batteries-included admin
  UI, a template engine with mtime-invalidated compile caching, safe
  static-file serving, and `TestClient`/`AsyncTestClient` for in-process
  testing with no real server required.
- **Opt-in connection pooling** for SQLite (`enable_connection_pooling()` /
  `enable_readonly_connection_pooling()`), benchmarked and disclosed
  honestly: a real win for ASGI, a real regression for WSGI.
- **CI**: a GitHub Actions pipeline covering lint, type-checking, the full
  test suite across supported Python versions, PostgreSQL/MySQL/Redis
  integration tests, the native Rust extension build, and a benchmark
  suite comparing against FastAPI, Django, Granian, Robyn, and a reference
  WSGI server, with every result (wins and losses) published in
  `docs/performance.md`.

### Fixed
- `mangoo_migrations` table schema was invalid on real MySQL (found via a
  genuine Linux reproduction, not assumed from the Windows dev environment).
- ASGI's default-executor concurrency ceiling, and a read-only SQLite read
  path, after re-measuring a documented `template_render` performance gap.
