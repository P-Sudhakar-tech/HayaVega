"""Hayavega ASGI benchmark app: the same routes and seeded data as
benchmarks/hayavega_app, served over hayavega.asgi instead of the WSGI runtime.

Exists to answer one specific question: once both frameworks run behind the
same kind of server (uvicorn), is Hayavega's ASGI transport competitive with
FastAPI's? Reuses benchmarks.hayavega_app.app's routes, seeded SQLite data,
and templates/static directories verbatim -- the only difference from the
WSGI Hayavega target is create_asgi_application() instead of
create_application(), and uvicorn instead of waitress. Every route inherited
from benchmarks.hayavega_app is an unchanged plain sync function, so that part
isolates the transport, not the view code.

This app adds one route of its own, `remote_io` (`GET /remote`), that
`hayavega_app` cannot: a real `async def` view awaiting a network call, only
meaningful under ASGI. It exists to answer the question
`benchmarks/README.md`'s "ASGI vs FastAPI" section explicitly left open:
none of the shared scenarios exercise the concurrent I/O that is ASGI's
actual reason to exist over WSGI. See benchmarks/asgi_concurrent_io.py,
which benchmarks this route against FastAPI's equivalent and against this
same app's own blocking version of the same call (`remote_io_blocking`,
inherited from hayavega_app, dispatched through the 16-worker sync pool
instead). The upstream call itself goes through
benchmarks/_upstream_client.py's minimal raw-socket client, not
`httpx.AsyncClient` -- see that module's docstring for the disclosed
reason (httpx was found not to overlap concurrent requests on this dev
environment).

Run standalone:
    uvicorn benchmarks.hayavega_asgi_app.app:application --port 9004
Served for real benchmark runs by benchmarks/run.py and
benchmarks/asgi_concurrent_io.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from benchmarks._upstream_client import fetch_upstream
from benchmarks.hayavega_app.app import seed, urlpatterns
from hayavega import api_route
from hayavega.asgi import create_asgi_application


async def remote_io(request):
    return await fetch_upstream()


def build_application():
    seed()
    all_patterns = [*urlpatterns, api_route("remote", remote_io, methods=("GET",))]
    return create_asgi_application(all_patterns, api_title="Benchmark", api_version="1.0.0", pool_connections=True)


application = build_application()

if __name__ == "__main__":
    import uvicorn

    print("Hayavega ASGI benchmark app (dev server): http://127.0.0.1:9004/")
    uvicorn.run(application, host="127.0.0.1", port=9004)
