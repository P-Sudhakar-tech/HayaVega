# API reference

Generated directly from Hayavega's own docstrings and type hints (the same
ones `mypy` checks in CI) &mdash; this page can't drift from the actual
public API the way a hand-maintained second copy would.

## Routing

::: hayavega.routing
    options:
      show_root_heading: false
      members:
        - path

::: hayavega.rest.api_route

## Request & Response

::: hayavega.request.Request

::: hayavega.response.Response

::: hayavega.response.json_response

## Models

::: hayavega.models.Entity

::: hayavega.models.Collection

::: hayavega.models.Selection

## Database

::: hayavega.db.connect

::: hayavega.db.connect_readonly

::: hayavega.db.enable_connection_pooling

::: hayavega.db.enable_readonly_connection_pooling

## Authentication

::: hayavega.auth.create_user

::: hayavega.auth.authenticate

::: hayavega.auth.create_session

## Runtime

::: hayavega.runtime.create_application

::: hayavega.asgi.create_asgi_application

::: hayavega.rust_runtime.serve
