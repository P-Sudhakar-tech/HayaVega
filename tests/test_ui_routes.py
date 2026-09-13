import unittest

from mangoo.runtime import create_application
from mangoo.testing import TestClient
from mangoo.ui.routes import ui_routes


class UiRoutesTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_application(list(ui_routes())))

    def test_serves_mangoo_ui_js_with_correct_content_type(self):
        """text/javascript, not the obsolete application/javascript -- the
        current standard (RFC 9239), and fixed regardless of what the host
        OS's own mimetypes database happens to say (see static.py's
        _CONTENT_TYPE_OVERRIDES)."""
        response = self.client.get("/mangoo-ui/mangoo-ui.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/javascript")
        self.assertIn("hx-get", response.text)
        self.assertIn("mgo-boost", response.text)

    def test_serves_mangoo_ui_css_with_correct_content_type(self):
        response = self.client.get("/mangoo-ui/mangoo-ui.css")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/css")
        self.assertIn("mgo-btn", response.text)

    def test_unknown_asset_returns_404(self):
        response = self.client.get("/mangoo-ui/does-not-exist.js")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
