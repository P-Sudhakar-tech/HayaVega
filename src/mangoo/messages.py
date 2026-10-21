"""A small flash-messages store -- cookie-based, no server-side session table.

Django's messages framework rides on its generic session storage;
Mangoo's sessions are auth-specific (server-side, keyed by an opaque
token issued only at login), so there's no equivalent foundation to
build messages on here. Instead, messages live in a small JSON-encoded
`mangoo_messages` cookie: `add_message()` appends to it on an outgoing
`Response`; `consume_messages()` reads it off an incoming `Request` and
clears it on the next outgoing `Response` -- one-read-then-gone, the
same semantics Django's messages have.

`Response` is a frozen dataclass, so neither function mutates one --
both return a (new) `Response` instead. Keep messages short: cookies
have a practical ~4KB limit, so this is a notification channel, not a
general data-passing one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from urllib.parse import quote, unquote

from .security import cookie

MESSAGES_COOKIE_NAME = "mangoo_messages"

DEBUG = "debug"
INFO = "info"
SUCCESS = "success"
WARNING = "warning"
ERROR = "error"


@dataclass(frozen=True)
class Message:
    level: str
    text: str


def _read_cookie_messages(raw: str) -> list[Message]:
    """`raw` is the cookie value exactly as stored -- percent-encoded
    JSON, since a cookie value can't safely hold JSON's own `"`/`:`/`,`
    characters unencoded (RFC 6265)."""
    if not raw:
        return []
    try:
        decoded = json.loads(unquote(raw))
    except ValueError:
        return []
    return [Message(item.get("level", INFO), item.get("text", "")) for item in decoded if isinstance(item, dict)]


def _set_messages_cookie(response, messages: list[Message]):
    encoded = quote(json.dumps([{"level": message.level, "text": message.text} for message in messages]))
    return replace(response, headers=(*response.headers, cookie(MESSAGES_COOKIE_NAME, encoded, same_site="Lax")))


def add_message(response, level: str, text: str):
    """Return a copy of `response` with one more message queued for
    the *next* request (not this one -- the message rides in a cookie
    set on this response, read back on the request that follows)."""
    existing = [(name, value) for name, value in response.headers if name != "Set-Cookie" or not value.startswith(f"{MESSAGES_COOKIE_NAME}=")]
    current = next((value.split(";", 1)[0].split("=", 1)[1] for name, value in response.headers if name == "Set-Cookie" and value.startswith(f"{MESSAGES_COOKIE_NAME}=")), "")
    messages = [*_read_cookie_messages(current), Message(level, text)]
    return _set_messages_cookie(replace(response, headers=tuple(existing)), messages)


def consume_messages(request, response) -> tuple[list[Message], object]:
    """Read the messages queued on `request`'s cookie and return them
    alongside a copy of `response` with that cookie cleared -- read
    once, then gone. Returns `response` unchanged (no cookie to clear)
    when there were none."""
    raw = request.cookies.get(MESSAGES_COOKIE_NAME, "")
    messages = _read_cookie_messages(raw)
    if not messages:
        return [], response
    return messages, replace(response, headers=(*response.headers, cookie(MESSAGES_COOKIE_NAME, "", max_age=0, same_site="Lax")))
