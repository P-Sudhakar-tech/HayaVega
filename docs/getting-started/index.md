# Getting started

New to Hayavega? Start here.

<div class="grid cards" markdown>

-   :material-download:{ .lg .middle } **Installation**

    ---

    Get Hayavega installed, with or without the optional Rust native extension.

    [:material-arrow-right: Installation](installation.md)

-   :material-rocket-launch:{ .lg .middle } **Quickstart**

    ---

    Scaffold a project, add a route, and see it respond &mdash; in about two minutes.

    [:material-arrow-right: Quickstart](quickstart.md)

-   :material-book-open-variant:{ .lg .middle } **Your first app**

    ---

    A longer walkthrough: a real app with a model, a migration, a list view, and a test.

    [:material-arrow-right: Tutorial](tutorial.md)

</div>

## What Hayavega assumes you already know

Hayavega borrows deliberately from established, well-understood conventions,
so prior experience with a similarly-structured Python web framework
transfers directly:

- **Project layout**: `hayavega.py`, `settings.py`, `routes.py`, migrations, and an admin site all work the way you'd expect from a conventional Python web project.
- **API documentation**: `api_route`-decorated views generate an OpenAPI schema automatically, the same way many modern API frameworks' route decorators do.

If none of that is familiar yet, the [tutorial](tutorial.md) doesn't assume it &mdash; it starts from a blank project.
