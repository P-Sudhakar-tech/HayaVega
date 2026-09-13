import asyncio
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from mangoo.scaffold import startapp, startproject


class ScaffoldTests(unittest.TestCase):
    def test_startproject_creates_runnable_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = startproject("demo", Path(directory))
            self.assertTrue((root / "manage.py").is_file())
            self.assertTrue((root / "project" / "urls.py").is_file())
            self.assertTrue((root / "project" / "wsgi.py").is_file())
            self.assertTrue((root / "project" / "asgi.py").is_file())
            self.assertIn("from mangoo.management", (root / "manage.py").read_text(encoding="utf-8"))
            self.assertIn("Welcome to Mangoo", (root / "project" / "urls.py").read_text(encoding="utf-8"))

    def test_generated_wsgi_module_exposes_a_working_wsgi_callable(self):
        """The wsgi.py a scaffolded project ships is what a real production
        server (gunicorn/waitress) is pointed at -- not just `mango
        launch`'s dev path -- so this exercises it as a real WSGI app,
        not just checking the file exists."""
        with tempfile.TemporaryDirectory() as directory:
            root = startproject("demo_wsgi", Path(directory))
            sys.path.insert(0, str(root))
            try:
                wsgi = importlib.import_module("project.wsgi")
                self.assertTrue(callable(wsgi.application))

                captured = {}

                def start_response(status, headers):
                    captured["status"] = status

                environ = {"PATH_INFO": "/", "REQUEST_METHOD": "GET", "HTTP_HOST": "localhost"}
                body = b"".join(wsgi.application(environ, start_response))
                self.assertEqual(captured["status"], "200 OK")
                self.assertIn(b"Welcome to Mangoo", body)
            finally:
                sys.path.remove(str(root))
                for name in ("project.wsgi", "project.urls", "project.settings", "project"):
                    sys.modules.pop(name, None)

    def test_generated_asgi_module_exposes_a_working_asgi_callable(self):
        """The asgi.py a scaffolded project ships is what an ASGI server
        (uvicorn/hypercorn) is pointed at when a project opts into async
        views -- exercised here as a real ASGI 3.0 callable, not just
        checked for existing."""
        with tempfile.TemporaryDirectory() as directory:
            root = startproject("demo_asgi", Path(directory))
            sys.path.insert(0, str(root))
            try:
                asgi = importlib.import_module("project.asgi")
                self.assertTrue(callable(asgi.application))

                messages = []

                async def receive():
                    return {"type": "http.request", "body": b"", "more_body": False}

                async def send(message):
                    messages.append(message)

                scope = {"type": "http", "method": "GET", "path": "/", "query_string": b"", "headers": [(b"host", b"localhost")], "client": ("127.0.0.1", 0), "scheme": "http"}
                asyncio.run(asgi.application(scope, receive, send))
                start = next(message for message in messages if message["type"] == "http.response.start")
                body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
                self.assertEqual(start["status"], 200)
                self.assertIn(b"Welcome to Mangoo", body)
            finally:
                sys.path.remove(str(root))
                for name in ("project.asgi", "project.wsgi", "project.urls", "project.settings", "project"):
                    sys.modules.pop(name, None)

    def test_startapp_creates_conventional_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = startapp("blog", Path(directory))
            self.assertTrue((root / "views.py").is_file())
            self.assertTrue((root / "migrations" / "__init__.py").is_file())
            self.assertIn("from mangoo import Response", (root / "views.py").read_text(encoding="utf-8"))

    def test_scaffold_rejects_existing_project_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            startproject("demo", root)
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                startproject("demo", root)

    def test_scaffold_rejects_existing_app_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            startapp("blog", root)
            with self.assertRaisesRegex(FileExistsError, "already exists"):
                startapp("blog", root)
