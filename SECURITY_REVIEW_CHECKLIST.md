# Mangoo security review checklist

| Control | Operational requirement | Verification |
|---|---|---|
| Sessions | HTTPS-only cookies in production; session hashes only | `tests/test_auth.py`, `tests/test_production_features.py` |
| CSRF | Require matching token for cookie-authenticated unsafe methods | `tests/test_production_features.py::test_csrf_protect_requires_matching_header_for_cookie_session` |
| CORS | Configure exact allowed origins; never reflect arbitrary origins | `tests/test_hardening.py::test_sets_security_headers_and_allowed_cors_origin` |
| Proxy trust | Trust forwarded headers only from configured proxy networks | `tests/test_hardening.py::test_forwarded_headers_require_an_explicit_trusted_proxy` |
| Headers | Enable default security headers and HSTS on HTTPS | `tests/test_hardening.py::test_https_policy_redirects_and_sets_hsts` |
| Rate limits | Bound memory and use shared infrastructure for multiple instances | `tests/test_hardening.py::test_rate_limiter_bounds_client_state_and_evicts_least_recent_client` |
| Audit | Review events without storing credentials or tokens | `tests/test_auth.py::test_authentication_events_are_persisted_without_credentials` |
| Migrations | Back up before applying; reject altered migration history | `tests/test_migrations.py::test_altered_or_missing_applied_migration_is_rejected` |

Review this checklist before each production release and record any accepted
exceptions with an owner and expiry date.
