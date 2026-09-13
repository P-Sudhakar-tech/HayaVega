"""Mangoo ASGI benchmark app: the same routes and seeded data as
benchmarks/mangoo_app, served over mangoo.asgi instead of the WSGI runtime.

Exists to answer one specific question: once both frameworks run behind the
same kind of server (uvicorn), is Mangoo's ASGI transport competitive with
FastAPI's? Reuses benchmarks.mangoo_app.app's routes, seeded SQLite data,
and templates/static directories verbatim -- the only difference from the
WSGI Mangoo target is create_asgi_application() instead of
create_application(), and uvicorn instead of waitress. Views are unchanged
plain sync functions in both apps, so this isolates the transport, not the
view code.

Run standalone:
    uvicorn benchmarks.mangoo_asgi_app.app:application --port 9004
Served for real benchmark runs by benchmarks/run.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from benchmarks.mangoo_app.app import seed, urlpatterns
from mangoo.asgi import create_asgi_application


def build_application():
    seed()
    return create_asgi_application(urlpatterns, api_title="Benchmark", api_version="1.0.0")


application = build_application()

if __name__ == "__main__":
    import uvicorn

    print("Mangoo ASGI benchmark app (dev server): http://127.0.0.1:9004/")
    uvicorn.run(application, host="127.0.0.1", port=9004)
