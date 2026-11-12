import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hayavega import Response, TestClient, path, render_template, static_routes
from hayavega.runtime import create_application


class MvpPolishTests(unittest.TestCase):
    def test_template_escapes_context_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "hello.html").write_text("<h1>Hello {{ name }}</h1>", encoding="utf-8")
            self.assertEqual(render_template("hello.html", {"name": "<script>"}, directories=(directory,)), "<h1>Hello &lt;script&gt;</h1>")
            with self.assertRaises(ValueError):
                render_template("../secret.html", directories=(directory,))

    def test_static_routes_serve_svg_and_block_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "mark.svg").write_text("<svg/>", encoding="utf-8")
            app = create_application(static_routes(directory, "assets"))
            self.assertEqual(TestClient(app).get("/assets/mark.svg").text, "<svg/>")
            self.assertEqual(TestClient(app).get("/assets/../../secret").status_code, 404)

    def test_static_routes_serve_common_web_assets_with_a_fixed_content_type(self):
        """These extensions get a fixed, standards-current content type
        regardless of what the host OS's own mimetypes database says --
        .js in particular resolves differently across machines depending on
        OS-provided mime data (the Windows Registry, /etc/mime.types),
        which previously made this framework's own behavior nondeterministic
        across platforms for the exact same file."""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "app.js").write_text("console.log(1);", encoding="utf-8")
            Path(directory, "app.css").write_text("body{}", encoding="utf-8")
            Path(directory, "data.json").write_text("{}", encoding="utf-8")
            app = create_application(static_routes(directory, "assets"))
            client = TestClient(app)
            self.assertEqual(client.get("/assets/app.js").headers["Content-Type"], "text/javascript")
            self.assertEqual(client.get("/assets/app.css").headers["Content-Type"], "text/css")
            self.assertEqual(client.get("/assets/data.json").headers["Content-Type"], "application/json")

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

    def test_static_routes_caches_resolved_paths_across_requests(self):
        """`Path.resolve()` is a real filesystem syscall -- profiling the
        static_file benchmark scenario found it dominates request time
        (over a third of all CPU time in that route). A second request for
        a file already resolved once must not call `Path.resolve()` again."""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "mark.svg").write_text("<svg/>", encoding="utf-8")
            app = create_application(static_routes(directory, "assets"))
            client = TestClient(app)
            client.get("/assets/mark.svg")  # first request: resolves and caches
            with patch("hayavega.static.Path.resolve", side_effect=AssertionError("resolve() must not be called again for an already-cached path")):
                response = client.get("/assets/mark.svg")
            self.assertEqual(response.text, "<svg/>")

    def test_static_routes_caches_file_content_until_mtime_changes(self):
        """Mirrors templates.py's own mtime-invalidated caching (`_compile_cached`):
        re-reading an unchanged static file's bytes on every single request is
        wasted work -- a second request for the same, unmodified file must not
        call `Path.read_bytes()` again."""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "mark.svg").write_text("<svg/>", encoding="utf-8")
            app = create_application(static_routes(directory, "assets"))
            client = TestClient(app)
            client.get("/assets/mark.svg")  # first request: reads and caches
            with patch("hayavega.static.Path.read_bytes", side_effect=AssertionError("read_bytes() must not be called again for an unmodified cached file")):
                response = client.get("/assets/mark.svg")
            self.assertEqual(response.text, "<svg/>")

    def test_static_routes_serves_fresh_content_after_the_file_is_modified(self):
        """The cache must not mean serving stale content forever -- editing
        the file (and therefore its mtime) must be picked up on the next
        request, the same guarantee templates.py's own cache already makes."""
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory, "mark.svg")
            file_path.write_text("<svg/>", encoding="utf-8")
            app = create_application(static_routes(directory, "assets"))
            client = TestClient(app)
            first = client.get("/assets/mark.svg")
            original_mtime = file_path.stat().st_mtime
            file_path.write_text("<svg class='updated'/>", encoding="utf-8")
            # `write_text()` sets mtime to "now", which can collide with the
            # original mtime on a coarse filesystem clock if both writes land
            # in the same tick -- set it explicitly, after the content write,
            # to a value guaranteed different from what was cached.
            new_mtime = original_mtime + 10
            os.utime(file_path, (new_mtime, new_mtime))
            second = client.get("/assets/mark.svg")
            self.assertEqual(first.text, "<svg/>")
            self.assertEqual(second.text, "<svg class='updated'/>")
            self.assertNotEqual(first.headers["ETag"], second.headers["ETag"])

    def test_static_routes_404s_for_a_directory_instead_of_a_file(self):
        """A path that resolves to a real, in-bounds filesystem entry that
        isn't a regular file (a directory) must still 404, not error or
        leak directory-listing behavior."""
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "subdir").mkdir()
            app = create_application(static_routes(directory, "assets"))
            self.assertEqual(TestClient(app).get("/assets/subdir").status_code, 404)

    def test_static_routes_still_404s_if_a_cached_file_is_removed_afterward(self):
        """Caching the resolved path must not mean serving stale content or
        crashing once the underlying file is gone -- existence is still
        checked fresh on every request; only the expensive path-resolution
        step is skipped for an already-seen path."""
        with tempfile.TemporaryDirectory() as directory:
            file_path = Path(directory, "mark.svg")
            file_path.write_text("<svg/>", encoding="utf-8")
            app = create_application(static_routes(directory, "assets"))
            client = TestClient(app)
            self.assertEqual(client.get("/assets/mark.svg").status_code, 200)
            file_path.unlink()
            self.assertEqual(client.get("/assets/mark.svg").status_code, 404)

    def test_static_routes_rejects_a_negative_max_age(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                static_routes(directory, "assets", max_age=-1)

    def test_test_client_posts_json_and_reads_response(self):
        app = create_application([path("echo", lambda request: Response(request.body.decode(), content_type="application/json"), methods=("POST",))])
        response = TestClient(app).post("/echo", json_data={"ok": True})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True})

    def test_runtime_shows_error_type_but_hides_message_and_traceback(self):
        """The error type name alone (e.g. "ZeroDivisionError") carries no
        confidential content and is shown to help a caller triage; the
        exception's message and any traceback/file/line detail never
        reach the client regardless of debug -- a connection error's
        message routinely embeds a database host, port, and username, and
        a settings error can embed a secret's name. The full exception,
        with traceback, is always logged server-side instead."""

        def broken(request):
            raise ValueError("db password is hunter2 at host 10.0.0.5")

        app = create_application([path("broken", broken)])
        with self.assertLogs("hayavega.runtime", "ERROR"):
            response = TestClient(app).get("/broken")
        self.assertEqual(response.status_code, 500)
        self.assertIn("ValueError", response.text)
        self.assertNotIn("hunter2", response.text)
        self.assertNotIn("10.0.0.5", response.text)
        self.assertNotIn("Traceback", response.text)
        self.assertNotIn(__file__, response.text)

    def test_api_route_error_returns_a_json_envelope_not_html(self):
        from hayavega import api_route

        def broken(request):
            raise ValueError("db password is hunter2 at host 10.0.0.5")

        app = create_application([api_route("broken", broken)])
        with self.assertLogs("hayavega.runtime", "ERROR"):
            response = TestClient(app).get("/broken")
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["error_type"], "ValueError")
        self.assertNotIn("hunter2", body["detail"])
        self.assertNotIn("10.0.0.5", body["detail"])
