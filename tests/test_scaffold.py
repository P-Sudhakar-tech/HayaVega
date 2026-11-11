import asyncio
import importlib
import io
import os
import runpy
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from hayavega.management.commands import HELP_TEXT
from hayavega.scaffold import startapp, startproject


class ScaffoldTests(unittest.TestCase):
    def test_startproject_creates_runnable_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = startproject("demo", Path(directory))
            self.assertTrue((root / "manage.py").is_file())
            self.assertTrue((root / "config" / "routes.py").is_file())
            self.assertTrue((root / "config" / "wsgi.py").is_file())
            self.assertTrue((root / "config" / "asgi.py").is_file())
            self.assertIn("from hayavega.management", (root / "manage.py").read_text(encoding="utf-8"))
            self.assertIn("Welcome to Hayavega", (root / "config" / "routes.py").read_text(encoding="utf-8"))

    def test_generated_entry_point_can_run_hayavega_the_documented_way(self):
        """Running the generated entry file the documented way (`python <file>.py ...`)
        makes Python insert that file's own directory at sys.path[0] --
        `runpy.run_path` reproduces that exact sys.path mutation without a real
        subprocess (so coverage instrumentation still sees the exercised code).
        If the entry file were named the same as the installed `hayavega`
        package, `from hayavega.management import ...` inside it would resolve
        to the file itself instead of the real package, and fail."""
        with tempfile.TemporaryDirectory() as directory:
            root = startproject("demo_entry", Path(directory))
            entry_point = root / "manage.py"
            original_cwd = os.getcwd()
            os.chdir(root)
            output = io.StringIO()
            try:
                with patch.object(sys, "argv", [str(entry_point)]), redirect_stdout(output):
                    runpy.run_path(str(entry_point), run_name="__main__")
            finally:
                os.chdir(original_cwd)
                for name in ("config", "config.settings", "config.routes", "config.wsgi", "config.asgi"):
                    sys.modules.pop(name, None)
        self.assertIn(HELP_TEXT, output.getvalue())

    def test_generated_wsgi_module_exposes_a_working_wsgi_callable(self):
        """The wsgi.py a scaffolded project ships is what a real production
        server (gunicorn/waitress) is pointed at -- not just `hayavega
        launch`'s dev path -- so this exercises it as a real WSGI app,
        not just checking the file exists."""
        with tempfile.TemporaryDirectory() as directory:
            root = startproject("demo_wsgi", Path(directory))
            sys.path.insert(0, str(root))
            try:
                wsgi = importlib.import_module("config.wsgi")
                self.assertTrue(callable(wsgi.application))

                captured = {}

                def start_response(status, headers):
                    captured["status"] = status

                environ = {"PATH_INFO": "/", "REQUEST_METHOD": "GET", "HTTP_HOST": "localhost"}
                body = b"".join(wsgi.application(environ, start_response))
                self.assertEqual(captured["status"], "200 OK")
                self.assertIn(b"Welcome to Hayavega", body)
            finally:
                sys.path.remove(str(root))
                for name in ("config.wsgi", "config.routes", "config.settings", "config"):
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
                asgi = importlib.import_module("config.asgi")
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
                self.assertIn(b"Welcome to Hayavega", body)
            finally:
                sys.path.remove(str(root))
                for name in ("config.asgi", "config.wsgi", "config.routes", "config.settings", "config"):
                    sys.modules.pop(name, None)

    def test_startapp_creates_conventional_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = startapp("blog", Path(directory))
            self.assertTrue((root / "views.py").is_file())
            self.assertTrue((root / "routes.py").is_file())
            self.assertTrue((root / "migrations" / "__init__.py").is_file())
            self.assertIn("from hayavega import Response", (root / "views.py").read_text(encoding="utf-8"))

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
