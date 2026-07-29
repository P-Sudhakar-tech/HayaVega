# Mangoo upcoming improvement roadmap

## Start: next week

This roadmap converts the current prototype into a safer, more deployable framework. Every feature below must include unit tests, regression tests for the identified risk, documentation, and a full-suite pass before it is marked complete.

## Priority order

| Priority | Workstream | Outcome |
|---|---|---|
| P0 | Data and migrations | Reliable schema changes and transaction semantics |
| P0 | Production validation | Real database integration tests, security review preparation, and deployment checks |
| P1 | Runtime and static assets | Production-safe asset delivery and runtime boundaries |
| P1 | Authentication lifecycle | Reset, verification, session management, and persistent audit events |
| P1 | API documentation | Complete OpenAPI schemas and authentication documentation |
| P2 | Developer experience | Richer templates, admin expansion, GraphQL work, and CI/release automation |
| P2 | Native runtime | Rust packaging, benchmarks, then measured migrations from Python |

## Week 1: data safety and test foundations

### Deliverables

- Define migration file format, migration history table, and migration command interface.
- Implement SQLite migration apply, status, and rollback-safe failure behavior.
- Add explicit transaction helpers with commit, rollback, and nested-operation rules.
- Add test database fixtures and migration test helpers.
- Establish coverage reporting and a required test command for CI.

### Acceptance criteria

- A new project can apply ordered migrations to an empty database.
- Re-running applied migrations is safe and does not change schema state.
- A failed migration records no partial schema/data changes where the database supports transactions.
- Migration history detects altered or missing migration files.
- Tests cover success, duplicate apply, failure, rollback, and malformed migration cases.

## Week 2: production validation and deployment safety

### Deliverables

- Add PostgreSQL integration tests and document the MySQL/MariaDB test plan.
- Add environment validation for production: secret key, debug state, allowed hosts, HTTPS, and secure cookies.
- Add health and readiness endpoints with database connectivity checks.
- Publish a reverse-proxy, HTTPS, backup, restore, and rollback deployment guide.
- Begin a security-review checklist covering sessions, CSRF, CORS, proxy trust, headers, and rate limits.

### Acceptance criteria

- CI can run SQLite tests plus opt-in PostgreSQL integration tests.
- Production validation fails with actionable errors for unsafe configuration.
- Readiness is unhealthy when the configured database cannot be reached.
- Deployment documentation is reproducible from a clean environment.
- Security checklist items each link to a test or documented operational control.

## Week 3: runtime, static files, and observability

### Deliverables

- Upgrade static serving to support binary files, cache-control, ETag, conditional requests, and safe MIME handling.
- Bound and instrument rate-limiter storage; document Redis/edge replacement for multi-instance use.
- Add structured request IDs and configurable JSON logging.
- Add persistent audit-event hooks for authentication and administration actions.
- Preserve query strings during HTTPS redirects and improve trusted-proxy configuration.

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
- CI matrix, linting, formatting, type checks, dependency scanning, release notes, and package publishing.
- Replace the temporary WSGI server with a production-ready async/Rust runtime behind the stable Python API.

## Definition of done

An item is complete only when:

1. The implementation is documented in the build guide and public API documentation.
2. Unit and regression tests cover its normal, invalid, and security-relevant paths.
3. The full test suite passes.
4. Required integration tests pass for supported databases or services.
5. Configuration defaults are safe, and any operational prerequisite is documented.
