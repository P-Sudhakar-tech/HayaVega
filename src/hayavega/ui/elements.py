"""Server-rendered HTML elements: plain Python functions returning ``Markup``.

``tag("div")`` returns a callable: ``div("text", class_="x")`` builds
``<div class="x">text</div>``. Positional arguments are children (escaped
unless already ``Markup``); keyword arguments are attributes.
``class_``/``for_`` strip their trailing underscore (Python keyword
collisions); any other underscore becomes a hyphen, so ``hx_get="/x"``
renders ``hx-get="/x"`` for free -- this is what lets hypermedia attributes
in ``hayavega.ui.hypermedia`` work with zero extra plumbing. ``True`` renders
a bare boolean attribute; ``False``/``None`` omit the attribute entirely.
"""

from __future__ import annotations

from collections.abc import Callable
from html import escape as _escape_text

__all__ = [
    "Markup",
    "fragment",
    "tag",
    "a",
    "body",
    "button",
    "div",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "head",
    "header",
    "html",
    "input_",
    "label",
    "li",
    "link",
    "main",
    "nav",
    "option",
    "p",
    "script",
    "select",
    "span",
    "table",
    "tbody",
    "td",
    "textarea",
    "th",
    "thead",
    "title",
    "tr",
    "ul",
]


class Markup(str):
    """A string that is already safe HTML -- never escaped when rendered as a child."""

    def __html__(self) -> str:
        return self


def _attr_name(name: str) -> str:
    if name in ("class_", "for_"):
        return name[:-1]
    return name.replace("_", "-")


def _render_attrs(attrs: dict) -> str:
    parts = []
    for name, value in attrs.items():
        if value is False or value is None:
            continue
        rendered = _attr_name(name)
        if value is True:
            parts.append(f" {rendered}")
        else:
            parts.append(f' {rendered}="{_escape_text(str(value), quote=True)}"')
    return "".join(parts)


def _render_child(child) -> str:
    if hasattr(child, "__html__"):
        return child.__html__()
    if isinstance(child, (list, tuple)):
        return "".join(_render_child(item) for item in child)
    return _escape_text(str(child))


def _render_children(children) -> str:
    return "".join(_render_child(child) for child in children)


def tag(name: str, *, void: bool = False) -> Callable[..., Markup]:
    """Return an element builder for ``name``. ``void=True`` for self-closing
    elements (``<input>``, ``<link>``, ...): no children, no closing tag."""
    if void:

        def element(**attrs) -> Markup:
            return Markup(f"<{name}{_render_attrs(attrs)}>")
    else:

        def element(*children, **attrs) -> Markup:  # type: ignore[misc]
            return Markup(f"<{name}{_render_attrs(attrs)}>{_render_children(children)}</{name}>")

    element.__name__ = name
    return element


def fragment(*children) -> Markup:
    """Group elements with no wrapping tag, for returning multiple siblings."""
    return Markup(_render_children(children))


html = tag("html")
head = tag("head")
title = tag("title")
body = tag("body")
header = tag("header")
footer = tag("footer")
nav = tag("nav")
main = tag("main")
div = tag("div")
span = tag("span")
p = tag("p")
h1 = tag("h1")
h2 = tag("h2")
h3 = tag("h3")
a = tag("a")
ul = tag("ul")
li = tag("li")
table = tag("table")
thead = tag("thead")
tbody = tag("tbody")
tr = tag("tr")
th = tag("th")
td = tag("td")
form = tag("form")
label = tag("label")
select = tag("select")
option = tag("option")
textarea = tag("textarea")
button = tag("button")
script = tag("script")
input_ = tag("input", void=True)
link = tag("link", void=True)
