# Your first app: a tiny blog API

This walks through a complete, small feature: a `Post` model, a migration,
a list endpoint, and a test &mdash; the shape of most real work in Hayavega.

## 1. Define a model

```python title="apps/blog/models.py"
from hayavega import Entity, Integer, Text


class Post(Entity):
    title = Text()
    body = Text()
    views = Integer(default=0)
```

`Entity` subclasses map to a database table (`post_entries` by default,
overridable via `__table__`). See [Models & migrations](../topics/models.md)
for the full column-type reference.

## 2. Generate and apply a migration

```bash
python manage.py newmigration blog
python manage.py applymigrations
```

This generates a checksummed migration file from `Post`'s columns and
applies it &mdash; transactionally, so a failed migration never leaves a
half-applied schema. `python manage.py migrationstatus` shows what's
pending.

## 3. Write a view

```python title="apps/blog/views.py"
from hayavega import api_route
from .models import Post
from .settings_helper import DATABASE  # however your app resolves its DB config


def list_posts(request):
    return Post.collection(DATABASE).browse().arrange("-id").window(20).values("title", "views")


def create_post(request):
    data = request.json()
    post = Post.collection(DATABASE).append(title=data["title"], body=data["body"])
    return {"id": post.id, "title": post.title}
```

`.browse()` starts a chainable selection; `.arrange("-id")` orders newest
first; `.window(20)` limits to 20 rows; `.values(...)` returns plain dicts
for the columns you actually need (skipping full `Entity` hydration for a
read-only list &mdash; see [Models & migrations](../topics/models.md) for
when to reach for `.fetch()` instead).

## 4. Wire the routes

```python title="apps/blog/routes.py"
from hayavega import api_route
from .views import create_post, list_posts

urlpatterns = [
    api_route("posts", list_posts, methods=("GET",)),
    api_route("posts", create_post, methods=("POST",)),
]
```

Both are automatically documented at `/openapi.json` &mdash; no separate
schema to maintain.

## 5. Test it

```python title="apps/blog/tests.py"
import unittest
from hayavega.runtime import create_application
from hayavega.testing import TestClient
from .routes import urlpatterns


class PostTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_application(urlpatterns))

    def test_create_and_list(self):
        created = self.client.post("posts", json_data={"title": "Hello", "body": "First post"})
        self.assertEqual(created.status_code, 200)

        listed = self.client.get("posts")
        self.assertEqual(listed.status_code, 200)
        titles = [post["title"] for post in listed.json()]
        self.assertIn("Hello", titles)
```

`TestClient` drives the real WSGI application in-process &mdash; no server,
no sockets, no network flakiness. See [Testing](../topics/testing.md) for
`AsyncTestClient` (ASGI) and how this project tests itself the same way.

## What you just built

- A persisted model, migrated safely
- A JSON API, auto-documented
- A test that exercises the real request/response cycle

From here:

- [Authentication](../topics/auth.md) to gate `create_post` behind a login
- [Admin](../topics/admin.md) to get a CRUD UI for `Post` with three lines of registration
- [Deployment](../topics/deployment.md) to put this behind a real server
