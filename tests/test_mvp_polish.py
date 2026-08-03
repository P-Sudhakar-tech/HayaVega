import tempfile
import unittest
from pathlib import Path

from mangoo import Response, TestClient, path, render_template, static_routes
from mangoo.runtime import create_application


class MvpPolishTests(unittest.TestCase):
    def test_template_escapes_context_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "hello.html").write_text("<h1>Hello {{ name }}</h1>", encoding="utf-8")
            self.assertEqual(render_template("hello.html", {"name": "<script>"}, directories=(directory,)), "<h1>Hello &lt;script&gt;</h1>")
            with self.assertRaises(ValueError): render_template("../secret.html", directories=(directory,))

    def test_static_routes_serve_svg_and_block_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "mark.svg").write_text("<svg/>", encoding="utf-8")
            app = create_application(static_routes(directory, "assets"))
            self.assertEqual(TestClient(app).get("/assets/mark.svg").text, "<svg/>")
            self.assertEqual(TestClient(app).get("/assets/../../secret").status_code, 404)

    def test_static_routes_serve_binary_assets_with_etag_and_cache_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            app = create_application(static_routes(directory, "assets", max_age=600))
            client = TestClient(app)
            initial = client.get("/assets/icon.png")
            cached = client.get("/assets/icon.png", headers={"If-None-Match": initial.headers["ETag"]})
            self.assertEqual(initial.content, b"\x89PNG\r\n\x1a\n")
            self.assertEqual(initial.headers["Content-Type"], "image/png")
            self.assertEqual(initial.headers["Cache-Control"], "public, max-age=600")
            self.assertEqual(cached.status_code, 304)
            self.assertEqual(cached.content, b"")

    def test_test_client_posts_json_and_reads_response(self):
        app = create_application([path("echo", lambda request: Response(request.body.decode(), content_type="application/json"), methods=("POST",))])
        response = TestClient(app).post("/echo", json_data={"ok": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_runtime_hides_traceback_and_logs_server_error(self):
        app = create_application([path("broken", lambda request: 1 / 0)])
        with self.assertLogs("mangoo.runtime", "ERROR"):
            response = TestClient(app).get("/broken")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("ZeroDivisionError", response.text)
