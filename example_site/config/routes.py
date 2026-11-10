from hayavega import Response, admin_routes, api_route, path, static_routes
from hayavega.auth_api import auth_routes
from hayavega.conf import load_settings
from hayavega.graphql import Schema, graphql_view
from hayavega.ui import Alert, Button, Card, Page, div, li, ui_routes, ul

from .settings import LANDING_PAGE

_settings = load_settings()


def home(request):
    return Response(LANDING_PAGE.read_text(encoding="utf-8"))


def health(request):
    return {"status": "ok", "service": "Hayavega REST API"}


schema = Schema(query={"greeting": lambda request: "Hello from Hayavega GraphQL"})

# A small anonymous demo of hayavega.ui: server-rendered components and
# hayavega-ui.js hypermedia interactivity (hx-get/hx-target/hx-swap), no
# JavaScript build step. It's anonymous on purpose (no login), so it
# doesn't exercise csrf_protect -- see tests/test_ui_hypermedia.py for the
# CSRF-protected, cookie-authenticated version of the hypermedia pattern.
_ui_demo_items = ["Read the README", "Try the admin at /admin"]


def _items_fragment():
    return ul(*(li(item) for item in _ui_demo_items), id="items")


def ui_demo(request):
    body = Page(
        "Hayavega UI demo",
        Card(
            "Try it",
            Alert("This page is server-rendered hayavega.ui components; the list below updates via hayavega-ui.js without a page reload.", variant="info"),
            Button("Add an item", hx_post="/ui-demo/items", hx_target="#items-wrapper"),
            div(_items_fragment(), id="items-wrapper"),
        ),
        stylesheets=["/hayavega-ui/hayavega-ui.css"],
        scripts=["/hayavega-ui/hayavega-ui.js"],
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
    *auth_routes(_settings.database, secure_cookies=not _settings.debug, secret_key=_settings.secret_key),
    *admin_routes(_settings.database),
    path("ui-demo", ui_demo),
    path("ui-demo/items", ui_demo_add_item, methods=("POST",)),
    *ui_routes(),
]
