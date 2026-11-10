import unittest

from hayavega import Response, path


class RoutingTests(unittest.TestCase):
    def test_path_normalizes_leading_and_trailing_slashes(self):
        route = path("/posts/", lambda request: Response("ok"))
        self.assertEqual(route.pattern, "posts")

    def test_path_preserves_root_as_empty_pattern(self):
        route = path("/", lambda request: Response("ok"))
        self.assertEqual(route.pattern, "")

    def test_response_defaults_are_html_success(self):
        response = Response("Hello")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "text/html; charset=utf-8")
        self.assertEqual(response.body, "Hello")
