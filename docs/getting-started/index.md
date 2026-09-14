# Getting started

New to Mangoo? Start here.

<div class="grid cards" markdown>

-   :material-download:{ .lg .middle } **Installation**

    ---

    Get Mangoo installed, with or without the optional Rust native extension.

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

## What Mangoo assumes you already know

Mangoo borrows deliberately from two frameworks, so prior experience with either transfers directly:

- **From Django**: `manage.py`, `settings.py`, `urls.py`, migrations, and an admin site all work the way you'd expect if you've used Django.
- **From FastAPI**: `api_route`-decorated views generate an OpenAPI schema automatically, the same way FastAPI's own path operations do.

If you know neither, the [tutorial](tutorial.md) doesn't assume either &mdash; it starts from a blank project.
