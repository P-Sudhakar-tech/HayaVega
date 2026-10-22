"""Driver used by serialized_connection_vs_python.py to serve
benchmarks/mangoo_app's /posts.json route through
mangoo_native.SerializedConnection instead of Selection.values() --
everything else (routing, create_application(), Response/JSON encoding
via api_route's own wrapping) is the real, unmodified framework code;
only the DB-access path inside this one view differs, isolating exactly
the thing being tested.

Not a benchmark itself -- see serialized_connection_vs_python.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mangoo_native import SerializedConnection  # noqa: E402

from benchmarks.mangoo_app.app import DATABASE, SEED_COUNT, seed, urlpatterns  # noqa: E402
from mangoo.rest import api_route  # noqa: E402
from mangoo.runtime import create_application  # noqa: E402

_connection: SerializedConnection | None = None

# Matches Selection._sql("id, title, views")'s exact generated query for
# Post.collection(DATABASE).browse().arrange("id").window(SEED_COUNT) --
# including the table name Entity auto-derives for this class
# ("post_entries", not "posts" -- verified directly, not assumed) --
# checked to match in this module's own smoke test before any benchmark
# numbers are trusted (see RUST_CORE_REWRITE_ROADMAP.md's Phase 3 verification).
_SQL = "SELECT id, title, views FROM post_entries ORDER BY id ASC LIMIT ? OFFSET ?"


def posts_json_native(request):
    global _connection
    if _connection is None:
        _connection = SerializedConnection(str(DATABASE))
    return _connection.execute(_SQL, (SEED_COUNT, 0))


def build_application():
    seed()
    native_posts_route = api_route("posts.json", posts_json_native, methods=("GET",))
    modified_routes = [native_posts_route if route.pattern == "posts.json" else route for route in urlpatterns]
    return create_application(modified_routes, api_title="Benchmark (serialized connection)", api_version="1.0.0")


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    print("Serialized-connection benchmark app (dev server): http://127.0.0.1:9011/")
    make_server("127.0.0.1", 9011, build_application()).serve_forever()
