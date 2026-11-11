# Quickstart

## Scaffold a project

```bash
hayavega-admin newproject mysite
cd mysite
```

This creates a familiar, conventional layout:

```text
mysite/
├── manage.py
└── config/
    ├── settings.py
    ├── routes.py
    ├── wsgi.py
    └── asgi.py
```

`config/routes.py` already wires up a few working routes out of the box:

```python title="config/routes.py"
from hayavega import Response, api_route, path
from hayavega.auth_api import auth_routes
from hayavega.conf import load_settings
from hayavega.graphql import Schema, graphql_view

_settings = load_settings()


def home(request):
    return Response("<h1>Welcome to Hayavega</h1><p>Your Rust-backed project is running.</p>")


def health(request):
    return {"status": "ok"}


schema = Schema(query={"greeting": lambda request: "Hello from Hayavega GraphQL"})

urlpatterns = [
    path("", home),
    api_route("api/health", health),
    path("graphql", graphql_view(schema), methods=("POST",)),
    *auth_routes(_settings.database, secret_key=_settings.secret_key),
]
```

Notice three things already working with zero extra code:

- `path("", home)` &mdash; a plain HTML view
- `api_route("api/health", health)` &mdash; a JSON endpoint, auto-documented in the generated OpenAPI schema
- `graphql_view(schema)` &mdash; a working GraphQL endpoint
- `*auth_routes(...)` &mdash; login, logout, password reset, email verification, and session management, all pre-wired

## Run it

```bash
python manage.py launch
```

```text
Hayavega development server: http://127.0.0.1:8000/
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) for the welcome page,
or [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)
to see `api/health` already documented.

## Add an app

```bash
python manage.py newapp blog
```

This scaffolds `apps/blog/` with its own `views.py`, `routes.py`, and
`models.py`, ready to wire into `config/routes.py`.

## Add a route

```python title="apps/blog/views.py"
def hello(request):
    return {"message": "Hello from Hayavega"}
```

```python title="apps/blog/routes.py"
from hayavega import api_route
from .views import hello

urlpatterns = [
    api_route("hello", hello, methods=("GET",)),
]
```

Wire it into the project:

```python title="config/routes.py"
from apps.blog.routes import urlpatterns as blog_urlpatterns

urlpatterns = [
    # ... existing routes
    *blog_urlpatterns,
]
```

Restart the dev server (or let `--noreload`'s absence auto-reload it) and
`GET /hello` responds with `{"message": "Hello from Hayavega"}` &mdash; already
listed in `/openapi.json`.

## What next

- [Your first app](tutorial.md) &mdash; a longer walkthrough with a real
  model, a migration, and a test.
- [Routing](../topics/routing.md) &mdash; the full `path()`/`api_route()`
  reference.
- [Models & migrations](../topics/models.md) &mdash; persisting data.
