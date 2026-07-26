"""A deliberately small GraphQL query endpoint for Mangoo projects."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import re

from .response import json_response


_FIELD_QUERY = re.compile(r"^\s*\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\s*$")


class Schema:
    def __init__(self, query: Mapping[str, Callable]):
        self.query = dict(query)

    def execute(self, query: str, request):
        match = _FIELD_QUERY.match(query)
        if not match:
            return {"errors": [{"message": "Only a single root query field is supported in this starter."}]}
        field = match.group(1)
        resolver = self.query.get(field)
        if resolver is None:
            return {"errors": [{"message": f"Cannot query field '{field}'."}]}
        return {"data": {field: resolver(request)}}


def graphql_view(schema: Schema):
    """Return a POST endpoint compatible with GraphQL JSON request bodies."""
    def endpoint(request):
        try:
            payload = request.json()
            query = payload["query"]
        except (KeyError, TypeError, ValueError):
            return json_response({"errors": [{"message": "Request body must contain a JSON 'query' field."}]}, 400)
        return json_response(schema.execute(query, request))

    return endpoint
