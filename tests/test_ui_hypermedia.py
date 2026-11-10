import unittest

from hayavega import path
from hayavega.response import Response
from hayavega.runtime import create_application
from hayavega.security import SESSION_COOKIE_NAME, csrf_protect, csrf_token
from hayavega.testing import TestClient
from hayavega.ui import Button, Page, div, li, ui_routes

_items = ["Read the README"]


def _items_fragment():
    return Response(div(*(li(item) for item in _items), id="items"))


def home(request):
    body = Page(
        "Demo",
        Button("Add", hx_post="/items", hx_target="#items"),
        _items_fragment(),
        scripts=["/hayavega-ui/hayavega-ui.js"],
    )
    return Response(body)


@csrf_protect
def add_item(request):
    item = request.form().get("item", "").strip() or "New item"
    _items.append(item)
    return _items_fragment()


urlpatterns = [
    path("", home),
    path("items", add_item, methods=("POST",)),
    *ui_routes(),
]


class HypermediaEndToEndTests(unittest.TestCase):
    def setUp(self):
        _items[:] = ["Read the README"]
        self.client = TestClient(create_application(urlpatterns))

    def test_home_page_renders_hx_post_button_and_the_hypermedia_runtime_script(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('hx-post="/items"', response.text)
        self.assertIn('hx-target="#items"', response.text)
        self.assertIn('src="/hayavega-ui/hayavega-ui.js"', response.text)

    def test_hx_post_with_matching_csrf_token_succeeds_and_returns_the_updated_fragment(self):
        self.client.cookies[SESSION_COOKIE_NAME] = "fake-session-token"
        token = "a-csrf-token"
        self.client.cookies["hayavega_csrf"] = token
        response = self.client.post("/items", form_data={"item": "Ship it"}, headers={"X-Csrf-Token": token})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ship it", response.text)
        self.assertIn("Read the README", response.text)

    def test_hx_post_without_a_csrf_token_is_rejected(self):
        self.client.cookies[SESSION_COOKIE_NAME] = "fake-session-token"
        self.client.cookies["hayavega_csrf"] = "a-csrf-token"
        response = self.client.post("/items", form_data={"item": "Should not land"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Should not land", response.text)

    def test_csrf_token_helper_reads_existing_cookie(self):
        class FakeRequest:
            cookies = {"hayavega_csrf": "existing-token"}

        self.assertEqual(csrf_token(FakeRequest()), "existing-token")


if __name__ == "__main__":
    unittest.main()
