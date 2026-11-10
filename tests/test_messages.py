import unittest

from hayavega.messages import INFO, MESSAGES_COOKIE_NAME, SUCCESS, add_message, consume_messages
from hayavega.response import Response


class _FakeRequest:
    def __init__(self, cookies: dict[str, str] | None = None):
        self.cookies = cookies or {}


def _cookie_value(response, name: str) -> str | None:
    for header_name, value in response.headers:
        if header_name == "Set-Cookie" and value.startswith(f"{name}="):
            return value.split(";", 1)[0].split("=", 1)[1]
    return None


class AddMessageTests(unittest.TestCase):
    def test_sets_a_cookie_carrying_the_message(self):
        response = add_message(Response("ok"), SUCCESS, "Saved!")
        self.assertIsNotNone(_cookie_value(response, MESSAGES_COOKIE_NAME))

    def test_does_not_mutate_the_original_response(self):
        original = Response("ok")
        add_message(original, SUCCESS, "Saved!")
        self.assertEqual(original.headers, ())

    def test_chained_calls_accumulate_messages_in_one_cookie(self):
        response = add_message(Response("ok"), SUCCESS, "First")
        response = add_message(response, INFO, "Second")
        cookies = [header for header in response.headers if header[0] == "Set-Cookie" and header[1].startswith(f"{MESSAGES_COOKIE_NAME}=")]
        self.assertEqual(len(cookies), 1)


class ConsumeMessagesTests(unittest.TestCase):
    def test_no_cookie_means_no_messages_and_an_unchanged_response(self):
        draft = Response("ok")
        messages, response = consume_messages(_FakeRequest(), draft)
        self.assertEqual(messages, [])
        self.assertIs(response, draft)

    def test_round_trips_messages_set_by_add_message(self):
        queued = add_message(Response("ok"), SUCCESS, "Saved!")
        queued = add_message(queued, INFO, "Heads up")
        cookie_value = _cookie_value(queued, MESSAGES_COOKIE_NAME)

        messages, response = consume_messages(_FakeRequest({MESSAGES_COOKIE_NAME: cookie_value}), Response("next page"))

        self.assertEqual([(m.level, m.text) for m in messages], [(SUCCESS, "Saved!"), (INFO, "Heads up")])
        cleared = _cookie_value(response, MESSAGES_COOKIE_NAME)
        self.assertEqual(cleared, "")

    def test_a_malformed_cookie_is_treated_as_no_messages(self):
        messages, response = consume_messages(_FakeRequest({MESSAGES_COOKIE_NAME: "not-valid-json"}), Response("ok"))
        self.assertEqual(messages, [])


if __name__ == "__main__":
    unittest.main()
