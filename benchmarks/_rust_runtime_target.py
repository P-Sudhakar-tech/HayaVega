"""Driver used by rust_runtime_vs_wsgi.py to serve benchmarks/mangoo_app/app.py
over mangoo.rust_runtime instead of waitress. Not a benchmark itself."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.mangoo_app.app import seed, urlpatterns  # noqa: E402
from mangoo.rust_runtime import serve  # noqa: E402

if __name__ == "__main__":
    seed()
    serve(urlpatterns, "127.0.0.1", 9006, workers=16)
