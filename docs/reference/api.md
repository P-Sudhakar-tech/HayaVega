# API reference

Generated directly from Mangoo's own docstrings and type hints (the same
ones `mypy` checks in CI) &mdash; this page can't drift from the actual
public API the way a hand-maintained second copy would.

## Routing

::: mangoo.routing
    options:
      show_root_heading: false
      members:
        - path

::: mangoo.rest.api_route

## Request & Response

::: mangoo.request.Request

::: mangoo.response.Response

::: mangoo.response.json_response

## Models

::: mangoo.models.Entity

::: mangoo.models.Collection

::: mangoo.models.Selection

## Database

::: mangoo.db.connect

::: mangoo.db.connect_readonly

::: mangoo.db.enable_connection_pooling

::: mangoo.db.enable_readonly_connection_pooling

## Authentication

::: mangoo.auth.create_user

::: mangoo.auth.authenticate

::: mangoo.auth.create_session

## Runtime

::: mangoo.runtime.create_application

::: mangoo.asgi.create_asgi_application

::: mangoo.rust_runtime.serve
