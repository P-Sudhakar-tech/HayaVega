"""Benchmark a Rust GraphQL lexer+parser (hayavega_native.parse_graphql_document)
against hayavega.graphql's pure-Python `_tokenize`/`_Parser.parse_document()`,
before wiring it in -- per this project's own stated principle: "Benchmark
each candidate first; schedule only once a measured win justifies it."

Field *resolution* (`Schema.execute()`'s calls into arbitrary Python
resolvers) can never move to Rust and is out of scope here; only tokenizing
and parsing a query string into a parsed structure is a candidate, since
that step needs zero Python callbacks mid-parse. See
RUST_CORE_REWRITE_ROADMAP.md's Phase 6 entry.

Requires hayavega_native to be built (see hatch_build.py / README.md).

Usage:
    python benchmarks/native_graphql_parser.py
"""

from __future__ import annotations

import timeit

from hayavega.graphql import GraphQLSyntaxError, _FieldNode, _Parser, _tokenize, _VariableDefinition, _VariableRef

try:
    from hayavega_native import parse_graphql_document as _native_parse_graphql_document
except ImportError as error:
    raise SystemExit(f"hayavega_native is not built: {error}. See README.md's 'Building the Rust-backed dispatcher' section.") from error


_SMALL_QUERY = "{ greeting }"

_COMPLEX_QUERY = """
query ListPosts($limit: Int! = 10, $search: String, $tags: [String!]) {
    posts(limit: $limit, search: $search, tags: $tags, extra: { active: true, score: 3.5 }) {
        id
        title
        views
        author {
            id
            name
        }
    }
    viewer {
        id
        name
    }
}
"""


def _python_parse(source: str):
    return _Parser(_tokenize(source)).parse_document()


def _decode_value(wire):
    if isinstance(wire, tuple) and len(wire) == 2 and wire[0] == "$":
        return _VariableRef(wire[1])
    if isinstance(wire, list):
        return [_decode_value(item) for item in wire]
    if isinstance(wire, dict):
        return {key: _decode_value(item) for key, item in wire.items()}
    return wire


def _decode_fields(wire) -> list[_FieldNode]:
    return [
        _FieldNode(
            name=name,
            arguments={arg_name: _decode_value(arg_value) for arg_name, arg_value in arguments},
            selection_set=_decode_fields(selection_set) if selection_set is not None else None,
        )
        for name, arguments, selection_set in wire
    ]


def _decode_document(wire):
    operation_type, operation_name, variable_definitions_wire, selection_set_wire = wire
    variable_definitions = {name: _VariableDefinition(required=required, default=_decode_value(default), has_default=has_default) for name, required, default, has_default in variable_definitions_wire}
    return operation_type, operation_name, variable_definitions, _decode_fields(selection_set_wire)


def _native_parse(source: str):
    return _decode_document(_native_parse_graphql_document(source))


def _values_equal(a, b) -> bool:
    if isinstance(a, _VariableRef) and isinstance(b, _VariableRef):
        return a.name == b.name
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_values_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_values_equal(a[key], b[key]) for key in a)
    return a == b


def _fields_equal(a: list[_FieldNode] | None, b: list[_FieldNode] | None) -> bool:
    if a is None or b is None:
        return a is b
    if len(a) != len(b):
        return False
    for field_a, field_b in zip(a, b, strict=True):
        if field_a.name != field_b.name:
            return False
        if not _values_equal(field_a.arguments, field_b.arguments):
            return False
        if not _fields_equal(field_a.selection_set, field_b.selection_set):
            return False
    return True


def _documents_equal(python_result, native_result) -> bool:
    (p_type, p_name, p_vars, p_selection) = python_result
    (n_type, n_name, n_vars, n_selection) = native_result
    if p_type != n_type or p_name != n_name or p_vars.keys() != n_vars.keys():
        return False
    for key in p_vars:
        if p_vars[key].required != n_vars[key].required or p_vars[key].has_default != n_vars[key].has_default:
            return False
        if not _values_equal(p_vars[key].default, n_vars[key].default):
            return False
    return _fields_equal(p_selection, n_selection)


def _report(name: str, python_fn, native_fn, *, number: int = 100_000) -> None:
    python_time = timeit.timeit(python_fn, number=number)
    native_time = timeit.timeit(native_fn, number=number)
    faster = "native" if native_time < python_time else "python"
    ratio = python_time / native_time if native_time else float("inf")
    print(f"{name}:")
    print(f"  python: {python_time:.4f}s for {number:,} calls ({python_time / number * 1e6:.3f} us/call)")
    print(f"  native: {native_time:.4f}s for {number:,} calls ({native_time / number * 1e6:.3f} us/call)")
    print(f"  -> {faster} is faster ({ratio:.2f}x)" if faster == "native" else f"  -> {faster} is faster ({1 / ratio:.2f}x)")
    print()


def main() -> None:
    for query in (_SMALL_QUERY, _COMPLEX_QUERY):
        python_result = _python_parse(query)
        native_result = _native_parse(query)
        assert _documents_equal(python_result, native_result), f"python/native parse mismatch for:\n{query}\npython: {python_result}\nnative: {native_result}"

    try:
        _python_parse("{ greeting @skip }")
        raise AssertionError("python parser should reject directives")
    except GraphQLSyntaxError:
        pass
    try:
        _native_parse_graphql_document("{ greeting @skip }")
        raise AssertionError("native parser should reject directives")
    except ValueError:
        pass

    # Timed with the Python-side decode step included (parse + reconstruct
    # _FieldNode/_VariableDefinition) -- that decode is the real cost
    # `Schema.execute()` would pay if this candidate were wired in, not just
    # the raw Rust call.
    _report("GraphQL parse: small query (`{ greeting }`)", lambda: _python_parse(_SMALL_QUERY), lambda: _native_parse(_SMALL_QUERY))
    _report("GraphQL parse: complex query (variables, args, nesting)", lambda: _python_parse(_COMPLEX_QUERY), lambda: _native_parse(_COMPLEX_QUERY))


if __name__ == "__main__":
    main()
