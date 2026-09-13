"""Mangoo benchmark app: JSON echo, DB-backed list, template render, static file, GraphQL.

Run standalone for a dev-server smoke test:
    python benchmarks/mangoo_app/app.py

Served for real benchmark runs via waitress by benchmarks/run.py.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from mangoo import Entity, Integer, Text, api_route, path, static_routes
from mangoo.graphql import Schema, graphql_view
from mangoo.response import Response
from mangoo.runtime import create_application
from mangoo.templates import render_template
from mangoo.ui import Button, Page, div, li, ui_routes, ul

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(tempfile.gettempdir()) / "mangoo_benchmark.sqlite3"
SEED_COUNT = 20


class Post(Entity):
    title = Text()
    views = Integer()


def seed() -> None:
    collection = Post.collection(DATABASE)
    if collection.total() >= SEED_COUNT:
        return
    for i in range(SEED_COUNT):
        collection.append(title=f"Post {i}", views=i * 3)


def echo(request):
    return {"status": "ok"}


def posts_json(request):
    return Post.collection(DATABASE).browse().arrange("id").window(SEED_COUNT).values("id", "title", "views")


def posts_page(request):
    posts = Post.collection(DATABASE).browse().arrange("id").window(SEED_COUNT).values("title", "views")
    body = render_template("page.html", {"posts": posts}, directories=(BASE_DIR / "templates",))
    return Response(body)


def _posts_list_markup():
    rows = Post.collection(DATABASE).browse().arrange("id").window(SEED_COUNT).fetch()
    return ul(*(li(f"{row.title} ({row.views} views)") for row in rows))


def posts_fragment(request):
    """The same 20 rows as posts_page, but only the <ul> fragment -- what a
    real hx-get/hx-target update swaps in, instead of a full page load.
    Kept as a separate route/template from posts_page/page.html so the
    published cross-framework template_render numbers (benchmarks/run.py)
    are never affected by anything here."""
    return Response(str(_posts_list_markup()))


def posts_fragment_native(request):
    """Identical to posts_fragment (same DB query, same rows, same output)
    except the markup itself is rendered by mangoo_native's Rust
    render_fragment_items instead of mangoo.ui's Python component builders
    -- isolates the HTTP-level effect of the microsecond-scale rendering
    win benchmarks/native_htmx_fragment.py measured in isolation. See
    benchmarks/hypermedia_fragment.py, which compares this against
    posts_fragment under real load."""
    from mangoo_native import render_fragment_items

    rows = Post.collection(DATABASE).browse().arrange("id").window(SEED_COUNT).fetch()
    return Response(render_fragment_items([(row.title, row.views) for row in rows]))


def posts_page_htmx(request):
    """A real htmx-powered page built with mangoo.ui: a Refresh button with
    hx-get/hx-target/hx-swap pointing at posts_fragment, served alongside
    the actual mangoo-ui.js runtime."""
    body = Page(
        "Posts (htmx demo)",
        Button("Refresh", hx_get="/posts/fragment", hx_target="#posts-list", hx_swap="innerHTML"),
        div(_posts_list_markup(), id="posts-list"),
        scripts=["/mangoo-ui/mangoo-ui.js"],
    )
    return Response(body)


def greeting(request):
    return "Hello from Mangoo GraphQL"


schema = Schema(query={"greeting": greeting})

urlpatterns = [
    api_route("echo", echo, methods=("GET",)),
    api_route("posts.json", posts_json, methods=("GET",)),
    path("posts", posts_page, methods=("GET",)),
    path("posts/fragment", posts_fragment, methods=("GET",)),
    path("posts/fragment-native", posts_fragment_native, methods=("GET",)),
    path("posts/live", posts_page_htmx, methods=("GET",)),
    path("graphql", graphql_view(schema), methods=("POST",)),
    *static_routes(BASE_DIR / "static", "static"),
    *ui_routes(),
]


def build_application():
    seed()
    # pool_connections is deliberately NOT enabled here: measured to be a
    # real ~10-15% throughput *regression* for this WSGI/waitress/SQLite
    # combination specifically (see INDUSTRY_LEADERSHIP_ROADMAP.md), unlike
    # the real win it is for the ASGI target below. Benchmark before
    # enabling it for your own deployment; don't assume the ASGI result
    # transfers.
    return create_application(urlpatterns, api_title="Benchmark", api_version="1.0.0")


application = build_application()

if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    print("Mangoo benchmark app (dev server): http://127.0.0.1:9001/")
    make_server("127.0.0.1", 9001, application).serve_forever()
