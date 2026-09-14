# Templates

Server-rendered HTML via a real tokenizer/parser/renderer &mdash; not a
single regex, and never arbitrary code execution.

## Rendering

```python
from mangoo.templates import render_template


def posts_page(request):
    posts = Post.collection(DATABASE).browse().values("title", "views")
    return Response(render_template("posts.html", {"posts": posts}, directories=(BASE_DIR / "templates",)))
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
