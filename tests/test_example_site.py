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
            self.assertIn("Django's shape.", response.body)
            self.assertIn("<title>Mangoo", response.body)
        finally:
            sys.path.remove(str(example_root))
            sys.modules.pop("project.urls", None)
            sys.modules.pop("project.settings", None)
            sys.modules.pop("project", None)

    def test_logo_asset_is_available_to_the_landing_page(self):
        example_root = Path(__file__).resolve().parents[1] / "example_site"
        sys.path.insert(0, str(example_root))
        try:
            urls = importlib.import_module("project.urls")
            logo_route = next(route for route in urls.urlpatterns if route.pattern == "assets/<path>")
            response = logo_route.view(type("Request", (), {"path": "/assets/mangoo-mark.svg"})())
            self.assertEqual("image/svg+xml", response.content_type)
            self.assertIn("<svg", response.body)
        finally:
            sys.path.remove(str(example_root))
            sys.modules.pop("project.urls", None)
            sys.modules.pop("project.settings", None)
            sys.modules.pop("project", None)

    def test_ui_demo_page_renders_components_and_the_add_item_fragment_endpoint_works(self):
        example_root = Path(__file__).resolve().parents[1] / "example_site"
        sys.path.insert(0, str(example_root))
        try:
            urls = importlib.import_module("project.urls")
            page_route = next(route for route in urls.urlpatterns if route.pattern == "ui-demo")
            page = page_route.view(None)
            self.assertIn("mgo-btn", page.body)
            self.assertIn('hx-post="/ui-demo/items"', page.body)

            add_route = next(route for route in urls.urlpatterns if route.pattern == "ui-demo/items")
            fragment = add_route.view(None)
            self.assertIn("Item 3", fragment.body)
        finally:
            sys.path.remove(str(example_root))
            sys.modules.pop("project.urls", None)
            sys.modules.pop("project.settings", None)
            sys.modules.pop("project", None)
