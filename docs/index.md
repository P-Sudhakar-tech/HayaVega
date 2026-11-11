---
title: Hayavega
description: A structured Python web framework with automatic, interactive API documentation and a Rust-accelerated core.
hide:
  - navigation
  - toc
---

<div class="hayavega-hero" markdown>

Open-source Python framework
{: .hayavega-eyebrow }

# Hayavega

Welcome to Hayavega &mdash; a Python web framework that leads FastAPI and
Django by a wide margin on published, reproducible benchmarks, and holds
its own against Rust-core alternatives.
{: .hayavega-welcome }

A conventional, batteries-included project structure. Automatic, interactive API documentation, generated from your code. A Rust core where it actually wins the benchmark, and honestly disclosed where it doesn't.

[Get started :material-arrow-right:](getting-started/index.md){ .md-button .md-button--primary }

</div>

## Why Hayavega

<div class="grid cards" markdown>

-   :material-shield-check:{ .lg .middle } **Batteries included, honestly documented**

    ---

    Auth (password reset, email verification, session rotation), admin, migrations, CSRF, rate limiting, and a security-review checklist all ship in the box. Every claim in these docs links to a real test.

-   :material-rocket-launch-outline:{ .lg .middle } **Wins on published, reproducible benchmarks**

    ---

    Real `oha` load tests, reproducible methodology, losses disclosed alongside wins. See the [performance page](performance.md) for the numbers, not adjectives.

-   :material-language-rust:{ .lg .middle } **Rust where it measurably helps**

    ---

    A native Rust WSGI server (1.4&ndash;2.7&times; faster than a reference pure-Python WSGI server), TLS via `rustls`, and Signals' worker-pool dispatcher &mdash; each one benchmarked before shipping, including the ideas that *didn't* pan out.

-   :material-api:{ .lg .middle } **REST, GraphQL, and ASGI &mdash; one auth system**

    ---

    `api_route` generates OpenAPI/Swagger automatically. A hand-written GraphQL engine with depth limits and introspection control. `async def` views under ASGI with zero migration for existing sync code.

-   :material-database-outline:{ .lg .middle } **A real migration system**

    ---

    Transactional, checksummed migrations with schema-diff detection, not a toy. SQLite, PostgreSQL, and MySQL/MariaDB all covered by CI.

-   :material-flask-outline:{ .lg .middle } **Built to be tested**

    ---

    `TestClient` and `AsyncTestClient` drive your app in-process with no real server needed &mdash; the same tooling this project's own 415+ tests run on.

</div>

## See it in thirty seconds

```bash
pip install hayavega
hayavega-admin newproject mysite
cd mysite
python hayavega.py newapp blog
python hayavega.py launch
```

```python title="config/routes.py"
from hayavega import api_route, path


def hello(request):
    return {"message": "Hello from Hayavega"}


urlpatterns = [
    api_route("api/hello", hello, methods=("GET",)),
]
```

Open `http://127.0.0.1:8000/openapi.json` and the endpoint is already documented &mdash; no extra decorator, no separate spec file.

[Read the full quickstart :material-arrow-right:](getting-started/quickstart.md){ .md-button }

## What's actually in here

| | |
|---|---|
| **Routing & views** | [Routing](topics/routing.md) |
| **Data** | [Models & migrations](topics/models.md) |
| **Auth & access** | [Authentication](topics/auth.md) &middot; [Admin](topics/admin.md) |
| **Rendering** | [Templates](topics/templates.md) |
| **APIs** | [REST & OpenAPI](topics/rest-api.md) &middot; [GraphQL](topics/graphql.md) |
| **Concurrency** | [ASGI](topics/asgi.md) &middot; [Rust runtime](topics/rust-runtime.md) |
| **Events** | [Signals](topics/signals.md) |
| **Quality** | [Testing](topics/testing.md) &middot; [Security](topics/security.md) |
| **Shipping it** | [Deployment](topics/deployment.md) |

!!! tip "This site is new"
    This documentation site is a fresh build, not a reformatted copy of the project's existing `README.md`/`DEPLOYMENT.md`. Those files still exist at the repository root and stay accurate &mdash; this site is the deeper, browsable reference they now point to.
