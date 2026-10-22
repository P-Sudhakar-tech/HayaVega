"""Driver used by serialized_connection_vs_python.py to serve
benchmarks/mangoo_app's exact app with db.enable_readonly_connection_pooling()
turned on first -- Python's own existing best option for the read-only
connection-reuse question (see db.py's own docstring: already measured at
HTTP level against this exact db_list scenario and found to be within normal
run-to-run variance, neither a real win nor a real loss). The comparison
baseline this session's new mangoo_native.SerializedConnection experiment
needs to beat, not just the unpooled default.

Not a benchmark itself -- see serialized_connection_vs_python.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.mangoo_app.app import build_application as _build_application  # noqa: E402
from mangoo.db import enable_readonly_connection_pooling  # noqa: E402


def build_application():
    enable_readonly_connection_pooling()
    return _build_application()


if __name__ == "__main__":
    from wsgiref.simple_server import make_server

    print("Readonly-pooling benchmark app (dev server): http://127.0.0.1:9012/")
    make_server("127.0.0.1", 9012, build_application()).serve_forever()
