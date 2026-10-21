# Templates

Server-rendered HTML via a real tokenizer/parser/renderer &mdash; not a
single regex, and never arbitrary code execution.

## Rendering

```python
from mangoo.conf import load_settings
from mangoo.templates import render_template

_settings = load_settings()


def posts_page(request):
    posts = Post.collection(_settings.database).browse().values("title", "views")
    return Response(render_template("posts.html", {"posts": posts}, directories=(_settings.base_dir / "templates",)))
```

## Syntax

```django title="templates/posts.html"
{% extends "base.html" %}

{% block content %}
  {% if posts %}
    <ul>
      {% for post in posts %}
        <li>{{ post.title }} ({{ post.views }} views){% if loop.first %} &mdash; newest{% endif %}</li>
      {% endfor %}
    </ul>
  {% else %}
    <p>No posts yet.</p>
  {% endif %}
{% endblock %}
```

Supported: `{{ variable.path }}` interpolation (auto-escaped),
`{% if/elif/else/endif %}` with `==`, `!=`, `in`, `and`, `or`, `not`, and
string/number/`true`/`false`/`none` literals, `{% for x in expr %}` loops
with `loop.index`/`loop.index0`/`loop.first`/`loop.last`, and
`{% extends %}`/`{% block %}` multi-level inheritance.

Deliberately **not** supported: filters, macros, or fragments &mdash; and
no arbitrary Python `eval` anywhere in the expression evaluator, by
design. `render_template(..., debug=True)` raises a descriptive
`TemplateSyntaxError`/`TemplateRenderError` (template name and line
number) for an undefined variable, a non-iterable loop target, or an
undeclared block; the default (`debug=False`) renders these as empty
text instead, so a template typo degrades gracefully in production
rather than 500ing.

## Rendering flash messages

`consume_messages` needs a `Response` to attach the cookie-clearing
header to, but you need the message list *before* rendering the page
that will display it. Get both in one call by consuming against an
empty placeholder, then filling in the real body:

```python
from dataclasses import replace

from mangoo.messages import consume_messages


def posts_page(request):
    messages, draft = consume_messages(request, Response(""))
    body = render_template("posts.html", {"posts": posts, "messages": messages}, directories=(_settings.base_dir / "templates",))
    return replace(draft, body=body)
```

```django title="templates/posts.html"
{% if messages %}
  <ul class="messages">
    {% for message in messages %}
      <li class="{{ message.level }}">{{ message.text }}</li>
    {% endfor %}
  </ul>
{% endif %}
```

See [Messages](messages.md) for the full API.

## Caching

Templates are parsed once and cached per file, invalidated by mtime &mdash;
an edited template is picked up on the next render with no server
restart, while an unchanged one skips re-parsing entirely. A
compile-to-real-Python-functions path (structure only &mdash; `if`/`for`
control flow &mdash; not expression evaluation, which stays interpreted for
its exact undefined-vs-`None` semantics) makes plain templates
1.27&ndash;2.19&times; faster; `{% extends %}` templates fall back to the
interpreter automatically. See [Performance](../performance.md) for the
full measurement, including why this specific win didn't move the
published HTTP-level benchmark (the per-request SQLite connection cost
dominates total request time far more than template rendering does).
