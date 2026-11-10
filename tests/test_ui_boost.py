import unittest

from hayavega import path
from hayavega.response import Response
from hayavega.runtime import create_application
from hayavega.testing import TestClient
from hayavega.ui import NavBar, Page, div


def page_one(request):
    body = Page(
        "Page one",
        div(NavBar("Demo", [("Home", "/"), ("Two", "/two")]), mgo_boost=True),
        div("First page content", mgo_boost_target=True),
        scripts=["/hayavega-ui/hayavega-ui.js"],
    )
    return Response(body)


def page_two(request):
    body = Page(
        "Page two",
        div(NavBar("Demo", [("Home", "/"), ("Two", "/two")]), mgo_boost=True),
        div("Second page content", mgo_boost_target=True),
        scripts=["/hayavega-ui/hayavega-ui.js"],
    )
    return Response(body)


urlpatterns = [path("", page_one), path("two", page_two)]


class BoostRoutingTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_application(urlpatterns))

    def test_both_pages_mark_a_boost_container_and_a_boost_target(self):
        first = self.client.get("/")
        second = self.client.get("/two")
        self.assertIn("mgo-boost", first.text)
        self.assertIn("mgo-boost-target", first.text)
        self.assertIn("First page content", first.text)
        self.assertIn("mgo-boost", second.text)
        self.assertIn("mgo-boost-target", second.text)
        self.assertIn("Second page content", second.text)

    def test_nav_links_point_at_both_real_routes(self):
        response = self.client.get("/")
        self.assertIn('href="/"', response.text)
        self.assertIn('href="/two"', response.text)


if __name__ == "__main__":
    unittest.main()
