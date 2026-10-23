# Guides

Task-oriented pages: the parts of Mangoo you touch while actually
building an app. Start with [Routing](../topics/routing.md) if you're
new &mdash; everything else builds on how `path()`/`api_route()` map
URLs to Python functions. For the deeper subsystems underneath (ASGI,
the Rust runtime, Signals, security model), see [Concepts](../concepts/index.md).

<div class="grid cards" markdown>

-   [:material-router-wireless: **Routing**](../topics/routing.md)
    URL patterns, `path()` vs `api_route()`, methods, middleware.

-   [:material-database: **Models & migrations**](../topics/models.md)
    `Entity`, columns, chainable selections, transactional migrations.

-   [:material-form-select: **Forms**](../topics/forms.md)
    Declarative fields, validation, widget rendering.

-   [:material-file-upload: **File uploads**](../topics/file-uploads.md)
    Multipart parsing, size limits, saving to disk.

-   [:material-file-code: **Templates**](../topics/templates.md)
    `{% if %}`/`{% for %}`/`{% extends %}`, no arbitrary code execution.

-   [:material-lock: **Authentication**](../topics/auth.md)
    Sessions, password reset, email verification, 2FA, permissions.

-   [:material-view-dashboard: **Admin**](../topics/admin.md)
    Register a model, get list/add/edit/delete for free.

-   [:material-api: **REST & OpenAPI**](../topics/rest-api.md)
    `api_route`, auto-generated Swagger, request/response schemas.

-   [:material-graphql: **GraphQL**](../topics/graphql.md)
    A hand-written engine with depth limits and introspection control.

-   [:material-connection: **WebSockets**](../topics/websockets.md)
    `async def` handlers, `WebSocketDisconnect`, hardening options.

-   [:material-server: **Deployment**](../topics/deployment.md)
    gunicorn/waitress, uvicorn, reverse proxies, backups.

</div>
