"""Small, composable server-rendered components built on ``hayavega.ui.elements``.

Every component is a plain function returning ``Markup`` -- no base class,
no template file, no build step.
"""

from __future__ import annotations

from .elements import Markup, a, body, button, div, form, fragment, h3, head, html, input_, label, link, nav, script, span, title

__all__ = ["Alert", "Button", "Card", "Form", "FormField", "NavBar", "Page"]

_VARIANT_CLASS = {
    "info": "mgo-alert-info",
    "success": "mgo-alert-success",
    "warning": "mgo-alert-warning",
    "error": "mgo-alert-error",
}


def Page(page_title: str, *children, stylesheets: tuple[str, ...] = (), scripts: tuple[str, ...] = ()) -> Markup:
    """A full HTML document: ``<title>``, stylesheets, body content, then scripts."""
    return html(
        head(
            title(page_title),
            *(link(rel="stylesheet", href=href) for href in stylesheets),
        ),
        body(
            *children,
            *(script(src=src) for src in scripts),
        ),
    )


def Button(text: str, *, variant: str = "primary", type: str = "button", **attrs) -> Markup:  # noqa: A002 -- `type` matches the HTML attribute name.
    return button(text, class_=f"mgo-btn mgo-btn-{variant}", type=type, **attrs)


def Alert(message: str, *, variant: str = "info") -> Markup:
    return div(message, class_=_VARIANT_CLASS.get(variant, _VARIANT_CLASS["info"]))


def Card(heading: str, *children) -> Markup:
    return div(h3(heading), *children, class_="mgo-card")


def FormField(name: str, label_text: str, *, type: str = "text", required: bool = False) -> Markup:  # noqa: A002
    return div(
        label(label_text, for_=name),
        input_(name=name, id=name, type=type, required=required),
        class_="mgo-field",
    )


def Form(*children, action: str, method: str = "POST", **attrs) -> Markup:
    return form(*children, action=action, method=method, class_="mgo-form", **attrs)


def NavBar(brand: str, links: list[tuple[str, str]]) -> Markup:
    return nav(
        span(brand, class_="mgo-nav-brand"),
        fragment(*(a(text, href=href, class_="mgo-nav-link") for text, href in links)),
        class_="mgo-nav",
    )
