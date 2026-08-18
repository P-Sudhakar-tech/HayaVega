# Mangoo upcoming improvement roadmap

## Status: active development (August 2026)

This roadmap converts the current prototype into a safer, more deployable framework. Every feature below must include unit tests, regression tests for the identified risk, documentation, and a full-suite pass before it is marked complete.

## Priority order

| Priority | Workstream | Outcome |
|---|---|---|
| P0 | Data and migrations | **In progress:** transactional, checksummed migrations and initial entity migration generation delivered; schema diffs/dependencies remain |
| P0 | Production validation | **In progress:** production settings validation, DB readiness/liveness, deployment guide, and CI-run PostgreSQL integration test delivered; MySQL/MariaDB integration test remains |
| P1 | Runtime and static assets | **In progress:** binary files, MIME types, cache-control, ETags, and conditional requests delivered; observability work remains |
| P1 | Authentication lifecycle | Reset, verification, session management, and persistent audit events |
| P1 | API documentation | Complete OpenAPI schemas and authentication documentation |
| P2 | Developer experience | **In progress:** CI (GitHub Actions, coverage gate) delivered; richer templates, admin expansion, GraphQL work, and release automation remain |
| P2 | Native runtime | Rust packaging, benchmarks, then measured migrations from Python |
| P2 | Mangoo Pulse | **Delivered:** opt-in in-process event bus, optional async delivery, sensitive-key rejection, and framework lifecycle wiring (request, auth, admin, migrations) all shipped; only remaining gap is that no built-in app wires a bus in by default |

## Future-readiness estimate (planning baseline)

These estimates assume one focused developer, the current test-driven pace, and
no delay obtaining database or email-provider infrastructure. Re-estimate after
each milestone; external security-review or release feedback is excluded.

| Workstream | Estimated effort | Expected completion |
|---|---:|---|
| P0 production readiness | 5–7 working days | Aug 10–12, 2026 |
| Data phase remaining | 5–8 working days | Aug 17–20, 2026 |
| Authentication lifecycle | 8–12 working days | Aug 28–Sep 3, 2026 |
| API maturity | 5–7 working days | Sep 10–14, 2026 |
| Developer experience and native-runtime preparation | 10–15 working days | Late September–early October 2026 |

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

- [~] Add PostgreSQL integration tests and document the MySQL/MariaDB test plan. (Opt-in PostgreSQL harness added and now runs in CI against a `postgres:16` service container; MySQL/MariaDB plan remains.)
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

- Add password-reset tokens with expiry, single use, secure storage, and pluggable mail delivery.
- Add email-verification tokens and verified-account policy hooks.
- Add session rotation, active-session listing, and revoke-all-sessions support.
- Extend OpenAPI output with request/response schemas, error responses, and Bearer authentication.
- Add API versioning and error-envelope conventions.

### Acceptance criteria

- Reset and verification tokens cannot be reused or used after expiry.
- Session creation rotates identifiers at authentication boundaries.
- Authentication lifecycle events produce audit records.
- Swagger documents authentication and all built-in error responses.
- All new flows have unit, integration, and negative-path tests.

## Later improvements

- Template inheritance, loops, conditionals, caching, and developer-mode diagnostics.
- Model-admin registration, pagination, validation, permissions, and change history.
- GraphQL arguments, mutations, nesting, validation, limits, and introspection policy.
- Maturin packaging for `mangoo_native`, benchmark automation, and measured Rust migrations.
- CI matrix across supported Python versions is delivered (`.github/workflows/ci.yml`); linting, formatting, type checks, dependency scanning, release notes, and package publishing remain.
- Replace the temporary WSGI server with a production-ready async/Rust runtime behind the stable Python API.
- Mangoo Pulse: delivered. The opt-in registry provides connect/disconnect, deterministic ordering, error isolation, optional asynchronous delivery, and sensitive-key rejection in `src/mangoo/addons/pulse/`; `create_application`, `auth_routes`, `admin_routes`, and `migrations.apply` all accept a `pulse=` argument to emit request, authentication, administration, and migration events. Remaining work is optional: a generated project could wire a default bus in automatically instead of requiring each application to pass one explicitly.

## Definition of done

An item is complete only when:

1. The implementation is documented in the build guide and public API documentation.
2. Unit and regression tests cover its normal, invalid, and security-relevant paths.
3. The full test suite passes.
4. Required integration tests pass for supported databases or services.
5. Configuration defaults are safe, and any operational prerequisite is documented.
