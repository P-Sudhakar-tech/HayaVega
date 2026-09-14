# Quickstart

## Scaffold a project

```bash
mangoo-admin newproject mysite
cd mysite
```

This creates a familiar, conventional layout:

```text
mysite/
├── manage.py
└── project/
    ├── settings.py
    ├── urls.py
    ├── wsgi.py
    └── asgi.py
```

`project/urls.py` already wires up a few working routes out of the box:

```python title="project/urls.py"
from mangoo import Response, api_route, path
from mangoo.auth_api import auth_routes
from mangoo.graphql import Schema, graphql_view
from .settings import DATABASES


def home(request):
    return Response("<h1>Welcome to Mangoo</h1><p>Your Rust-backed project is running.</p>")


def health(request):
    return {"status": "ok"}


schema = Schema(query={"greeting": lambda request: "Hello from Mangoo GraphQL"})

urlpatterns = [
    path("", home),
    api_route("api/health", health),
    path("graphql", graphql_view(schema), methods=("POST",)),
    *auth_routes(DATABASES["default"]),
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
Mangoo development server: http://127.0.0.1:8000/
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) for the welcome page,
or [http://127.0.0.1:8000/openapi.json](http://127.0.0.1:8000/openapi.json)
to see `api/health` already documented.

## Add an app

```bash
python manage.py newapp blog
```

This scaffolds `apps/blog/` with its own `views.py`, `urls.py`, and
`models.py`, ready to wire into `project/urls.py`.

## Add a route

```python title="apps/blog/views.py"
def hello(request):
    return {"message": "Hello from Mangoo"}
```

```python title="apps/blog/urls.py"
from mangoo import api_route
from .views import hello

urlpatterns = [
    api_route("hello", hello, methods=("GET",)),
]
```

Wire it into the project:

```python title="project/urls.py"
from apps.blog.urls import urlpatterns as blog_urlpatterns

urlpatterns = [
    # ... existing routes
    *blog_urlpatterns,
]
```

Restart the dev server (or let `--noreload`'s absence auto-reload it) and
`GET /hello` responds with `{"message": "Hello from Mangoo"}` &mdash; already
listed in `/openapi.json`.

## What next

- [Your first app](tutorial.md) &mdash; a longer walkthrough with a real
  model, a migration, and a test.
- [Routing](../topics/routing.md) &mdash; the full `path()`/`api_route()`
  reference.
- [Models & migrations](../topics/models.md) &mdash; persisting data.
