"""Driver used by rust_runtime_vs_wsgi.py to serve benchmarks/hayavega_app/app.py
over hayavega.rust_runtime instead of waitress. Not a benchmark itself."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.hayavega_app.app import seed, urlpatterns  # noqa: E402
from hayavega.rust_runtime import serve  # noqa: E402

if __name__ == "__main__":
    seed()
    serve(urlpatterns, "127.0.0.1", 9006, workers=16)
