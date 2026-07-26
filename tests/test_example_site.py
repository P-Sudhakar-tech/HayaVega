import importlib
import sys
import unittest
from pathlib import Path


class ExampleSiteTests(unittest.TestCase):
    def test_root_route_returns_project_landing_page(self):
        example_root = Path(__file__).resolve().parents[1] / "example_site"
        sys.path.insert(0, str(example_root))
        try:
            urls = importlib.import_module("project.urls")
            home_route = next(route for route in urls.urlpatterns if route.pattern == "")
            response = home_route.view(None)
            self.assertIn("Web development, made clear", response.body)
            self.assertIn("<title>Mangoo", response.body)
        finally:
            sys.path.remove(str(example_root))
            sys.modules.pop("project.urls", None)
            sys.modules.pop("project.settings", None)
            sys.modules.pop("project", None)
