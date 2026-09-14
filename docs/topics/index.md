# Topics

Deep-dive guides for each part of Mangoo, one subsystem at a time. Start
with [Routing](routing.md) if you're new &mdash; everything else builds on
how `path()`/`api_route()` map URLs to Python functions.

<div class="grid cards" markdown>

-   [:material-router-wireless: **Routing**](routing.md)
    URL patterns, `path()` vs `api_route()`, methods, middleware.

-   [:material-database: **Models & migrations**](models.md)
    `Entity`, columns, chainable selections, transactional migrations.

-   [:material-lock: **Authentication**](auth.md)
    Sessions, password reset, email verification, 2FA, permissions.

-   [:material-view-dashboard: **Admin**](admin.md)
    Register a model, get list/add/edit/delete for free.

-   [:material-file-code: **Templates**](templates.md)
    `{% if %}`/`{% for %}`/`{% extends %}`, no arbitrary code execution.

-   [:material-api: **REST & OpenAPI**](rest-api.md)
    `api_route`, auto-generated Swagger, request/response schemas.

-   [:material-graphql: **GraphQL**](graphql.md)
    A hand-written engine with depth limits and introspection control.

-   [:material-lightning-bolt: **ASGI**](asgi.md)
    `async def` views, zero migration for existing sync code.

-   [:material-language-rust: **Rust runtime**](rust-runtime.md)
    A native WSGI server, TLS, and why HTTP/2 stayed out of scope.

-   [:material-broadcast: **Signals**](signals.md)
    An opt-in, always-async in-process event bus.

-   [:material-flask: **Testing**](testing.md)
    `TestClient`/`AsyncTestClient`, no real server required.

-   [:material-shield-lock: **Security**](security.md)
    CSRF, rate limiting, trusted proxies, the error-disclosure standard.

-   [:material-server: **Deployment**](deployment.md)
    gunicorn/waitress, uvicorn, reverse proxies, backups.

</div>
