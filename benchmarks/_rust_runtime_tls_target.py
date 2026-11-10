"""Driver used to benchmark hayavega.rust_runtime's TLS overhead: serves
benchmarks/hayavega_app/app.py over HTTPS using a throwaway self-signed
certificate generated on the fly via the openssl CLI. Not a benchmark
itself -- see benchmarks/README.md for the numbers this produced."""

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.hayavega_app.app import seed, urlpatterns  # noqa: E402
from hayavega.rust_runtime import serve  # noqa: E402

if __name__ == "__main__":
    directory = tempfile.mkdtemp()
    cert_path = Path(directory) / "cert.pem"
    key_path = Path(directory) / "key.pem"
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-keyout", str(key_path), "-out", str(cert_path), "-days", "1", "-nodes", "-subj", "/CN=localhost"],
        check=True,
        capture_output=True,
    )
    seed()
    serve(urlpatterns, "127.0.0.1", 9008, workers=16, tls_cert=str(cert_path), tls_key=str(key_path))
