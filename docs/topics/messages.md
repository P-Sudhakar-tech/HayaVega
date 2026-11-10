# Messages

One-shot flash messages across a redirect -- "Saved!", "Invalid
credentials", that kind of thing.

## Why this works differently from Django

Django's messages framework rides on its generic session storage.
Hayavega's sessions are auth-specific -- server-side, keyed by an opaque
token issued only at login -- so there's no equivalent session table to
store messages in for an anonymous visitor. Messages instead live in a
small JSON-encoded `hayavega_messages` cookie: queued on an outgoing
`Response`, read and cleared on the next request's `Response`. Keep
messages short -- cookies have a practical ~4KB limit, so this is a
notification channel, not a general data-passing one.

`Response` is a frozen dataclass, so neither function below mutates
one -- both return a (new) `Response` instead.

## Queuing a message

```python
from hayavega.messages import SUCCESS, add_message
from hayavega.response import Response


def save_post(request):
    ...
    response = Response("", status=303, headers=(("Location", "/posts"),))
    return add_message(response, SUCCESS, "Post saved.")
```

Calling `add_message` more than once on the same response chain
accumulates every message into the one cookie:

```python
response = add_message(response, SUCCESS, "Post saved.")
response = add_message(response, "info", "You have 2 drafts pending review.")
```

## Reading and clearing messages

```python
from hayavega.messages import consume_messages


def posts_list(request):
    draft = Response(render_template("posts.html", {"posts": posts}))
    messages, response = consume_messages(request, draft)
    return response
```

`consume_messages(request, draft_response)` returns
`(messages, response)` -- the queued `Message` objects, and a copy of
`draft_response` with the cookie cleared so the same messages don't
show again on the next request. If there was nothing queued, it
returns `([], draft_response)` unchanged -- no cookie-clearing header
added when there's nothing to clear.

## Levels

`hayavega.messages` defines `DEBUG`, `INFO`, `SUCCESS`, `WARNING`,
`ERROR` as plain string constants -- the same level names Django's
messages use, for familiarity. `add_message` accepts any string, so
your own levels work too; `Message.level` is just whatever you passed.
