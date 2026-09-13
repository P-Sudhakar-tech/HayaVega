from mangoo import Response, admin_routes, api_route, path, static_routes
from mangoo.auth_api import auth_routes
from mangoo.graphql import Schema, graphql_view
from mangoo.ui import Alert, Button, Card, Page, div, li, ui_routes, ul

from .settings import DATABASES, DEBUG, LANDING_PAGE


def home(request):
    return Response(LANDING_PAGE.read_text(encoding="utf-8"))


def health(request):
    return {"status": "ok", "service": "Mangoo REST API"}


schema = Schema(query={"greeting": lambda request: "Hello from Mangoo GraphQL"})

# A small anonymous demo of mangoo.ui: server-rendered components and
# mangoo-ui.js hypermedia interactivity (hx-get/hx-target/hx-swap), no
# JavaScript build step. It's anonymous on purpose (no login), so it
# doesn't exercise csrf_protect -- see tests/test_ui_hypermedia.py for the
# CSRF-protected, cookie-authenticated version of the hypermedia pattern.
_ui_demo_items = ["Read the README", "Try the admin at /admin"]


def _items_fragment():
    return ul(*(li(item) for item in _ui_demo_items), id="items")


def ui_demo(request):
    body = Page(
        "Mangoo UI demo",
        Card(
            "Try it",
            Alert("This page is server-rendered mangoo.ui components; the list below updates via mangoo-ui.js without a page reload.", variant="info"),
            Button("Add an item", hx_post="/ui-demo/items", hx_target="#items-wrapper"),
            div(_items_fragment(), id="items-wrapper"),
        ),
        stylesheets=["/mangoo-ui/mangoo-ui.css"],
        scripts=["/mangoo-ui/mangoo-ui.js"],
    )
    return Response(body)


def ui_demo_add_item(request):
    _ui_demo_items.append(f"Item {len(_ui_demo_items) + 1}")
    return Response(str(_items_fragment()))


urlpatterns = [
    path("", home),
    *static_routes(LANDING_PAGE.parent / "assets", "assets"),
    api_route("api/health", health),
    path("graphql", graphql_view(schema), methods=("POST",)),
    *auth_routes(DATABASES["default"], secure_cookies=not DEBUG),
    *admin_routes(DATABASES["default"]),
    path("ui-demo", ui_demo),
    path("ui-demo/items", ui_demo_add_item, methods=("POST",)),
    *ui_routes(),
]
