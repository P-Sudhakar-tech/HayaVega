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
            self.assertIn("from mangoo.management", (root / "manage.py").read_text(encoding="utf-8"))
            self.assertIn("Welcome to Mangoo", (root / "project" / "urls.py").read_text(encoding="utf-8"))

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
