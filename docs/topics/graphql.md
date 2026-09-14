# GraphQL

A real tokenizer, recursive-descent parser, and executor &mdash; not a
regex matcher &mdash; supporting arguments, variables, nested selections,
mutations, and configurable depth/field limits.

## Defining a schema

```python
from mangoo.graphql import Schema, graphql_view


def get_post(request, id):
    return {"id": id, "title": "Hello", "author": {"name": "Alice"}}


schema = Schema(
    query={"post": get_post},
    mutation={"createPost": create_post},
    max_depth=10,  # default
    max_fields=200,  # default
    introspection=False,  # default: no __schema exposed
)

urlpatterns = [
    path("graphql", graphql_view(schema), methods=("POST",)),
]
```

## Querying

```graphql
query {
  post(id: "1") {
    title
    author { name }
  }
}
```

Field arguments (string/int/float/boolean/null/enum/list/object
literals), variables (`$id: ID!` required, `$active: Boolean = true`
defaulted), and nested selections on a resolver returning a mapping (or
list of mappings) all work &mdash; an unknown sub-field is a per-field
error, not a crash. A resolver exception becomes a per-field GraphQL
error (`{"data": {...: null}, "errors": [...]}`), never an unhandled
exception reaching the WSGI layer.

## Mutations

```graphql
mutation {
  createPost(title: "Hello") {
    id
  }
}
```

## Limits and introspection

`max_depth`/`max_fields` are checked *before* any resolver runs, plus a
hard structural parse-depth cap independent of configuration so a
maliciously deep query can't overflow the parser itself. Introspection
is off by default (`__typename` always works, since it reveals no schema
shape); `Schema(introspection=True)` opts into a minimal `__schema`
listing root field names.

## What's deliberately not supported

Fragments and directives are explicitly rejected with a clear parse
error, not silently mis-parsed &mdash; kept out to avoid the real
complexity they'd add for a framework this size. Revisit only if a
concrete need emerges.

## Error disclosure

Like REST, a resolver exception never leaks its raw message to the
client (`Schema(debug=False)`, the default, exposes only `error_type`) &mdash;
see [Security](security.md) for the full standard.
