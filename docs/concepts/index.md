# Concepts

The subsystems underneath the pages in [Guides](../guides/index.md):
cross-cutting pieces (middleware, caching, i18n), and the deeper
architecture (ASGI's concurrency model, the Rust runtime, Signals'
event bus) worth understanding on its own terms, not just as a
how-to.

<div class="grid cards" markdown>

-   [:material-layers-outline: **Middleware**](../topics/middleware.md)
    The request/response pipeline, and how it composes with routing.

-   [:material-email-outline: **Email**](../topics/email.md)
    `send_mail`, backends, console/file backends for development.

-   [:material-message-text-outline: **Messages**](../topics/messages.md)
    One-time flash messages across a redirect.

-   [:material-memory: **Caching**](../topics/caching.md)
    In-memory, SQLite, and Redis backends behind one interface.

-   [:material-translate: **i18n**](../topics/i18n.md)
    Catalogs, `Accept-Language` negotiation, pluralization.

-   [:material-lightning-bolt: **ASGI**](../topics/asgi.md)
    `async def` views, zero migration for existing sync code.

-   [:material-language-rust: **Rust runtime**](../topics/rust-runtime.md)
    A native WSGI server, TLS, and why HTTP/2 stayed out of scope.

-   [:material-broadcast: **Signals**](../topics/signals.md)
    An opt-in, always-async in-process event bus.

-   [:material-shield-lock: **Security**](../topics/security.md)
    CSRF, rate limiting, trusted proxies, the error-disclosure standard.

-   [:material-flask: **Testing**](../topics/testing.md)
    `TestClient`/`AsyncTestClient`, no real server required.

</div>
