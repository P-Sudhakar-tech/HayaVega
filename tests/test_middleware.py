import unittest

from hayavega import path
from hayavega.asgi import create_asgi_application
from hayavega.middleware import build_chain, load_middleware
from hayavega.response import Response
from hayavega.runtime import create_application
from hayavega.testing import AsyncTestClient, TestClient


def _recording_middleware(log: list, label: str):
    """A middleware factory that records entry/exit order around the response."""

    def middleware(get_response):
        def handler(request):
            log.append(f"{label}:before")
            response = get_response(request)
            log.append(f"{label}:after")
            return response

        return handler

    return middleware


def _async_recording_middleware(log: list, label: str):
    def middleware(get_response):
        async def handler(request):
            log.append(f"{label}:before")
            response = await get_response(request)
            log.append(f"{label}:after")
            return response

        return handler

    return middleware


def _blocking_middleware(get_response):
    def handler(request):
        return Response("blocked", status=403)

    return handler


class BuildChainTests(unittest.TestCase):
    def test_first_listed_factory_ends_up_outermost(self):
        log: list = []
        handler = build_chain([_recording_middleware(log, "A"), _recording_middleware(log, "B")], lambda request: "view")
        handler(None)
        self.assertEqual(log, ["A:before", "B:before", "B:after", "A:after"])

    def test_empty_list_returns_the_handler_unchanged(self):
        handler = build_chain([], lambda request: "view")
        self.assertEqual(handler(None), "view")


class LoadMiddlewareTests(unittest.TestCase):
    def test_resolves_a_dotted_path_to_its_factory(self):
        factories = load_middleware(["test_middleware._blocking_middleware"])
        self.assertEqual(factories, [_blocking_middleware])

    def test_rejects_a_bare_name_with_no_module(self):
        with self.assertRaisesRegex(ValueError, "dotted path"):
            load_middleware(["not_a_dotted_path"])


class WsgiMiddlewareIntegrationTests(unittest.TestCase):
    def test_middleware_wraps_route_matching_and_the_view(self):
        log: list = []
        application = create_application(
            [path("", lambda request: Response("ok"))],
            middleware=[_recording_middleware(log, "A"), _recording_middleware(log, "B")],
        )
        response = TestClient(application).get("")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(log, ["A:before", "B:before", "B:after", "A:after"])

    def test_middleware_runs_for_404s_too(self):
        log: list = []
        application = create_application([], middleware=[_recording_middleware(log, "A")])
        TestClient(application).get("missing")
        self.assertEqual(log, ["A:before", "A:after"])

    def test_a_middleware_can_short_circuit_before_the_view(self):
        application = create_application(
            [path("", lambda request: Response("should not run"))],
            middleware=[_blocking_middleware],
        )
        response = TestClient(application).get("")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.text, "blocked")


class AsgiMiddlewareIntegrationTests(unittest.TestCase):
    def test_async_middleware_wraps_an_async_view(self):
        log: list = []

        async def view(request):
            return Response("ok")

        application = create_asgi_application(
            [path("", view)],
            middleware=[_async_recording_middleware(log, "A"), _async_recording_middleware(log, "B")],
        )
        response = AsyncTestClient(application).get("")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(log, ["A:before", "B:before", "B:after", "A:after"])


if __name__ == "__main__":
    unittest.main()
