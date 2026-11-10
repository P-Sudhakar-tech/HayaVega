# Hayavega security review checklist

| Control | Operational requirement | Verification |
|---|---|---|
| Sessions | HTTPS-only cookies in production; session hashes only | `tests/test_auth.py`, `tests/test_production_features.py` |
| CSRF | Require matching token for cookie-authenticated unsafe methods | `tests/test_production_features.py::test_csrf_protect_requires_matching_header_for_cookie_session` |
| CORS | Configure exact allowed origins; never reflect arbitrary origins | `tests/test_hardening.py::test_sets_security_headers_and_allowed_cors_origin` |
| Proxy trust | Trust forwarded headers only from configured proxy networks | `tests/test_hardening.py::test_forwarded_headers_require_an_explicit_trusted_proxy` |
| Headers | Enable default security headers and HSTS on HTTPS | `tests/test_hardening.py::test_https_policy_redirects_and_sets_hsts` |
| Rate limits | Bound memory and use shared infrastructure for multiple instances | `tests/test_hardening.py::test_rate_limiter_bounds_client_state_and_evicts_least_recent_client` |
| Audit | Review events without storing credentials or tokens | `tests/test_auth.py::test_authentication_events_are_persisted_without_credentials` |
| Login telemetry | Every login/session records its IP and user agent for review, without ever recording a credential; `X-Forwarded-For` is honored only from a configured trusted proxy | `tests/test_auth_login_telemetry.py` |
| SQL injection | Every query parameterizes values; every identifier (table/column name) is allowlist-validated against the entity's own declared schema before reaching a SQL string | `tests/test_sql_injection.py` |
| Migrations | Back up before applying; reject altered migration history | `tests/test_migrations.py::test_altered_or_missing_applied_migration_is_rejected` |
| Error disclosure | An unhandled exception (view, resolver, connection failure) never exposes its message or a traceback to the client -- only its type name (e.g. `"ValueError"`), which carries no confidential content by itself; the full exception is always logged server-side instead | `tests/test_mvp_polish.py::test_runtime_shows_error_type_but_hides_message_and_traceback`, `tests/test_asgi.py::test_unhandled_exception_shows_error_type_but_hides_message_and_traceback`, `tests/test_graphql.py::test_resolver_exception_message_is_hidden_by_default` |

Review this checklist before each production release and record any accepted
exceptions with an owner and expiry date.
