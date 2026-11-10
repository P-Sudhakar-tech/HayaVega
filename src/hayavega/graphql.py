"""A small, dependency-free GraphQL query endpoint for Hayavega projects.

This executor supports a practical subset of the GraphQL language: field
arguments, variables (with declared defaults and required-ness), nested
selections, and mutations. It deliberately does not support fragments,
directives, or a declared type system -- field values are projected
dynamically from whatever a resolver returns (a mapping, a list of mappings,
or a scalar), so there is no schema to validate a selection against ahead of
calling the resolver. Introspection is limited to the same reasoning: with
no declared type system there is no real `__schema` to expose, so it stays
off by default (a common production hardening default) and, when enabled,
returns only root field names rather than a spec-complete introspection
schema. `__typename` is always available since it does not reveal schema
shape.
"""

from __future__ import annotations

import json as _json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from typing import Any, cast

from .response import json_response
from .security import request_user

try:
    from hayavega_native import parse_graphql_document as _native_parse_graphql_document
except ImportError:
    _native_parse_graphql_document = None

LOGGER = logging.getLogger("hayavega.graphql")

_MAX_STRUCTURAL_DEPTH = 64


class GraphQLSyntaxError(ValueError):
    """Raised for a malformed or unsupported GraphQL document."""


class GraphQLAuthorizationError(Exception):
    """Raised by `permission_required_resolver`/`login_required_resolver` to reject
    an unauthenticated or unauthorized caller.

    Unlike an arbitrary resolver exception -- whose message is hidden unless
    `Schema(debug=True)`, since it might carry confidential internal details
    (a database driver's own connection-failure message, for example) -- this
    error's message is always shown to the client, the same as a REST
    `permission_required` view's 401/403 `detail`: the app author chose this
    exact message to be read by the caller, so suppressing it would make the
    rejection unactionable for no security benefit.
    """


def permission_required_resolver(database_path, predicate, *, message: str = "Permission denied."):
    """Require an authenticated user satisfying `predicate(user)` before a GraphQL
    resolver runs -- the GraphQL-side equivalent of `hayavega.security.permission_required`
    for REST views. Without this (or an equivalent manual `request_user()` check written
    into the resolver itself), a mutation runs for any caller regardless of authentication.
    """

    def decorate(resolver):
        @wraps(resolver)
        def protected(request, **kwargs):
            user = request_user(request, database_path)
            if user is None:
                raise GraphQLAuthorizationError("Authentication required.")
            if not predicate(user):
                raise GraphQLAuthorizationError(message)
            return resolver(request, **kwargs)

        return protected

    return decorate


def login_required_resolver(database_path):
    return permission_required_resolver(database_path, lambda user: True)


def superuser_required_resolver(database_path):
    return permission_required_resolver(database_path, lambda user: user.is_superuser, message="Administrator permission required.")


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------

_PUNCTUATION = set("{}():$[]!=")
_NAME_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_NAME_CONTINUE = _NAME_START | set("0123456789")
_DIGITS = set("0123456789")


@dataclass(frozen=True)
class _Token:
    kind: str
    value: object


def _tokenize(source: str) -> list[_Token]:
    tokens: list[_Token] = []
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch in " \t\r\n," or ord(ch) == 0xFEFF:
            i += 1
            continue
        if ch == "#":
            while i < n and source[i] not in "\r\n":
                i += 1
            continue
        if ch == '"':
            j = i + 1
            while j < n and source[j] != '"':
                j += 2 if source[j] == "\\" else 1
            if j >= n:
                raise GraphQLSyntaxError("Unterminated string literal.")
            try:
                value = _json.loads(source[i : j + 1])
            except ValueError as exc:
                raise GraphQLSyntaxError("Invalid string literal.") from exc
            tokens.append(_Token("string", value))
            i = j + 1
            continue
        if ch in _PUNCTUATION:
            tokens.append(_Token("punct", ch))
            i += 1
            continue
        if ch in _NAME_START:
            j = i + 1
            while j < n and source[j] in _NAME_CONTINUE:
                j += 1
            tokens.append(_Token("name", source[i:j]))
            i = j
            continue
        if ch in _DIGITS or (ch == "-" and i + 1 < n and source[i + 1] in _DIGITS):
            j = i + 1
            is_float = False
            while j < n and source[j] in _DIGITS:
                j += 1
            if j < n and source[j] == ".":
                is_float = True
                j += 1
                while j < n and source[j] in _DIGITS:
                    j += 1
            if j < n and source[j] in "eE":
                is_float = True
                j += 1
                if j < n and source[j] in "+-":
                    j += 1
                while j < n and source[j] in _DIGITS:
                    j += 1
            raw = source[i:j]
            tokens.append(_Token("float" if is_float else "int", float(raw) if is_float else int(raw)))
            i = j
            continue
        if ch == "@":
            raise GraphQLSyntaxError("Directives are not supported.")
        if ch == ".":
            raise GraphQLSyntaxError("Fragments are not supported.")
        raise GraphQLSyntaxError(f"Unexpected character {ch!r} in query.")
    tokens.append(_Token("eof", None))
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


class _VariableRef:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


@dataclass
class _VariableDefinition:
    required: bool
    default: object
    has_default: bool


@dataclass
class _FieldNode:
    name: str
    arguments: dict
    selection_set: list[_FieldNode] | None


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._selection_depth = 0

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _advance(self) -> _Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _peek_is_punct(self, value: str) -> bool:
        token = self._peek()
        return token.kind == "punct" and token.value == value

    def _expect_punct(self, value: str) -> None:
        token = self._advance()
        if token.kind != "punct" or token.value != value:
            raise GraphQLSyntaxError(f"Expected '{value}'.")

    def _expect_name(self) -> str:
        token = self._advance()
        if token.kind != "name":
            raise GraphQLSyntaxError("Expected a name.")
        return cast(str, token.value)

    def parse_document(self):
        operation_type = "query"
        operation_name = None
        if self._peek().kind == "name" and self._peek().value in ("query", "mutation"):
            operation_type = self._advance().value
            if self._peek().kind == "name":
                operation_name = self._advance().value
        variable_definitions = self._parse_variable_definitions()
        selection_set = self._parse_selection_set()
        if self._peek().kind != "eof":
            raise GraphQLSyntaxError("Unexpected content after the operation.")
        return operation_type, operation_name, variable_definitions, selection_set

    def _parse_variable_definitions(self) -> dict:
        definitions: dict[str, _VariableDefinition] = {}
        if not self._peek_is_punct("("):
            return definitions
        self._advance()
        while not self._peek_is_punct(")"):
            self._expect_punct("$")
            name = self._expect_name()
            self._expect_punct(":")
            non_null = self._parse_type()
            has_default = False
            default = None
            if self._peek_is_punct("="):
                self._advance()
                default = self._parse_value()
                has_default = True
            definitions[name] = _VariableDefinition(required=non_null and not has_default, default=default, has_default=has_default)
        self._advance()
        return definitions

    def _parse_type(self) -> bool:
        if self._peek_is_punct("["):
            self._advance()
            self._parse_type()
            self._expect_punct("]")
        else:
            self._expect_name()
        if self._peek_is_punct("!"):
            self._advance()
            return True
        return False

    def _parse_selection_set(self) -> list[_FieldNode]:
        self._selection_depth += 1
        if self._selection_depth > _MAX_STRUCTURAL_DEPTH:
            raise GraphQLSyntaxError("Query is nested too deeply to parse.")
        self._expect_punct("{")
        fields = []
        while not self._peek_is_punct("}"):
            fields.append(self._parse_field())
        self._advance()
        if not fields:
            raise GraphQLSyntaxError("A selection set must select at least one field.")
        self._selection_depth -= 1
        return fields

    def _parse_field(self) -> _FieldNode:
        name = self._expect_name()
        arguments = self._parse_arguments()
        selection_set = self._parse_selection_set() if self._peek_is_punct("{") else None
        return _FieldNode(name=name, arguments=arguments, selection_set=selection_set)

    def _parse_arguments(self) -> dict:
        arguments: dict[str, Any] = {}
        if not self._peek_is_punct("("):
            return arguments
        self._advance()
        while not self._peek_is_punct(")"):
            name = self._expect_name()
            self._expect_punct(":")
            arguments[name] = self._parse_value()
        self._advance()
        return arguments

    def _parse_value(self):
        token = self._peek()
        if token.kind == "punct" and token.value == "$":
            self._advance()
            return _VariableRef(self._expect_name())
        if token.kind in ("int", "float", "string"):
            self._advance()
            return token.value
        if token.kind == "name":
            self._advance()
            if token.value == "true":
                return True
            if token.value == "false":
                return False
            if token.value == "null":
                return None
            return token.value  # bare identifier: treated as an enum value
        if token.kind == "punct" and token.value == "[":
            self._advance()
            items = []
            while not self._peek_is_punct("]"):
                items.append(self._parse_value())
            self._advance()
            return items
        if token.kind == "punct" and token.value == "{":
            self._advance()
            obj = {}
            while not self._peek_is_punct("}"):
                key = self._expect_name()
                self._expect_punct(":")
                obj[key] = self._parse_value()
            self._advance()
            return obj
        raise GraphQLSyntaxError("Expected a value.")


# ---------------------------------------------------------------------------
# Native lexing+parsing bridge. `hayavega_native.parse_graphql_document` is a
# faithful Rust port of `_tokenize`/`_Parser.parse_document()` above (same
# grammar, same rejected constructs) -- measured at a real 2.7-3.9x win
# (benchmarks/native_graphql_parser.py) since parsing is self-contained,
# substantial-enough work per call to be worth one Rust/Python FFI crossing,
# unlike field *resolution* (arbitrary Python callables), which stays here.
# ---------------------------------------------------------------------------


def _decode_native_value(wire):  # pragma: no cover -- only called from the native branch below; see its own pragma note.
    if isinstance(wire, tuple) and len(wire) == 2 and wire[0] == "$":
        return _VariableRef(wire[1])
    if isinstance(wire, list):
        return [_decode_native_value(item) for item in wire]
    if isinstance(wire, dict):
        return {key: _decode_native_value(item) for key, item in wire.items()}
    return wire


def _decode_native_fields(wire) -> list[_FieldNode]:  # pragma: no cover -- only called from the native branch below; see its own pragma note.
    return [
        _FieldNode(
            name=name,
            arguments={arg_name: _decode_native_value(arg_value) for arg_name, arg_value in arguments},
            selection_set=_decode_native_fields(selection_set) if selection_set is not None else None,
        )
        for name, arguments, selection_set in wire
    ]


def _parse_document(query: str):
    if _native_parse_graphql_document is None:
        return _Parser(_tokenize(query)).parse_document()
    # Environment-dependent, not a design gap: this branch only runs when
    # hayavega_native is actually built, so no single test environment can
    # cover both this and the fallback branch above -- the native-extension
    # CI job (hayavega_native present) exercises it for real; excluded here so
    # the pure-Python fallback-mode job's 100% coverage gate stays meaningful.
    try:  # pragma: no cover
        operation_type, operation_name, variable_definitions_wire, selection_set_wire = _native_parse_graphql_document(query)
    except ValueError as exc:  # pragma: no cover
        raise GraphQLSyntaxError(str(exc)) from exc
    variable_definitions = {name: _VariableDefinition(required=required, default=_decode_native_value(default), has_default=has_default) for name, required, default, has_default in variable_definitions_wire}  # pragma: no cover
    return operation_type, operation_name, variable_definitions, _decode_native_fields(selection_set_wire)  # pragma: no cover


def _bind(value, variables: dict):
    if isinstance(value, _VariableRef):
        if value.name in variables:
            return variables[value.name]
        raise GraphQLSyntaxError(f'Variable "${value.name}" is not defined.')
    if isinstance(value, list):
        return [_bind(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _bind(item, variables) for key, item in value.items()}
    return value


def _depth(fields: list[_FieldNode]) -> int:
    if not fields:
        return 0
    return 1 + max((_depth(f.selection_set) if f.selection_set else 0) for f in fields)


def _count_fields(fields: list[_FieldNode]) -> int:
    total = 0
    for f in fields:
        total += 1
        if f.selection_set:
            total += _count_fields(f.selection_set)
    return total


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class Schema:
    """A root query/mutation resolver map with limits and an introspection policy.

    Resolvers are called as ``resolver(request, **arguments)``. When a
    resolver's return value is a mapping (or a list of mappings) and the
    field has a sub-selection, the sub-selection is projected recursively
    against it; unknown sub-fields and selections on non-mapping values are
    reported as errors rather than raising.

    An unhandled resolver exception always becomes a per-field GraphQL
    error rather than an unhandled exception reaching the WSGI/ASGI layer.
    With the default ``debug=False``, the client only receives the
    exception's type name (e.g. ``"ValueError"``) -- never its message,
    which could contain confidential details (a database driver's
    connection-failure message routinely includes the host, port, and
    username it tried). The full exception, with traceback, is always
    logged server-side. Pass ``debug=True`` (development only, matching
    ``create_application``/``render_template``'s own ``debug=`` convention)
    to include the exception's message in the response as well, for local
    troubleshooting.
    """

    def __init__(
        self,
        query: Mapping[str, Callable] | None = None,
        mutation: Mapping[str, Callable] | None = None,
        *,
        max_depth: int = 10,
        max_fields: int = 200,
        introspection: bool = False,
        debug: bool = False,
    ):
        self.query = dict(query or {})
        self.mutation = dict(mutation or {})
        self.max_depth = max_depth
        self.max_fields = max_fields
        self.introspection = introspection
        self.debug = debug

    def execute(self, query: str, request, *, variables: Mapping | None = None, operation_name: str | None = None) -> dict:
        try:
            operation_type, parsed_name, variable_definitions, selection_set = _parse_document(query)
        except GraphQLSyntaxError as exc:
            return {"errors": [{"message": str(exc)}]}

        if operation_name is not None and operation_name != parsed_name:
            return {"errors": [{"message": f"Unknown operation '{operation_name}'."}]}

        if operation_type == "mutation" and not self.mutation:
            return {"errors": [{"message": "This schema does not define any mutations."}]}
        resolvers = self.mutation if operation_type == "mutation" else self.query

        try:
            bound_variables = self._bind_variables(variable_definitions, variables or {})
        except GraphQLSyntaxError as exc:
            return {"errors": [{"message": str(exc)}]}

        depth = _depth(selection_set)
        if depth > self.max_depth:
            return {"errors": [{"message": f"Query depth {depth} exceeds the maximum of {self.max_depth}."}]}
        total_fields = _count_fields(selection_set)
        if total_fields > self.max_fields:
            return {"errors": [{"message": f"Query selects {total_fields} fields, exceeding the maximum of {self.max_fields}."}]}

        errors: list[dict] = []
        data = {field_node.name: self._resolve_root_field(field_node, resolvers, request, bound_variables, operation_type, errors) for field_node in selection_set}
        result: dict[str, Any] = {"data": data}
        if errors:
            result["errors"] = errors
        return result

    @staticmethod
    def _bind_variables(definitions: dict, provided: Mapping) -> dict:
        bound = {}
        for name, definition in definitions.items():
            if name in provided:
                bound[name] = provided[name]
            elif definition.has_default:
                bound[name] = definition.default
            elif definition.required:
                raise GraphQLSyntaxError(f'Variable "${name}" of required type was not provided.')
            else:
                bound[name] = None
        return bound

    def _resolve_root_field(self, field_node: _FieldNode, resolvers: dict, request, variables: dict, operation_type: str, errors: list) -> object:
        if field_node.name == "__typename":
            return "Mutation" if operation_type == "mutation" else "Query"
        if field_node.name.startswith("__"):
            return self._project(self._resolve_schema_introspection(field_node, errors), field_node, errors)
        resolver = resolvers.get(field_node.name)
        if resolver is None:
            errors.append({"message": f"Cannot query field '{field_node.name}'."})
            return None
        try:
            arguments = {name: _bind(value, variables) for name, value in field_node.arguments.items()}
            value = resolver(request, **arguments)
        except GraphQLSyntaxError as exc:
            errors.append({"message": str(exc)})
            return None
        except GraphQLAuthorizationError as exc:
            errors.append({"message": str(exc), "error_type": type(exc).__name__})
            return None
        except Exception as exc:
            LOGGER.exception("Unhandled resolver error field=%s", field_node.name)
            error: dict[str, Any] = {"message": f"Error resolving field '{field_node.name}'.", "error_type": type(exc).__name__}
            if self.debug:
                error["message"] = f"Error resolving field '{field_node.name}': {exc}"
            errors.append(error)
            return None
        return self._project(value, field_node, errors)

    def _project(self, value, field_node: _FieldNode, errors: list):
        selection_set = field_node.selection_set
        if selection_set is None:
            return value
        if value is None:
            return None
        if isinstance(value, Mapping):
            return self._project_mapping(value, selection_set, errors)
        if isinstance(value, (list, tuple)):
            projected: list[Any] = []
            for item in value:
                if item is None:
                    projected.append(None)
                elif isinstance(item, Mapping):
                    projected.append(self._project_mapping(item, selection_set, errors))
                else:
                    projected.append(self._scalar_subselection_error(field_node, errors))
            return projected
        return self._scalar_subselection_error(field_node, errors)

    def _project_mapping(self, mapping: Mapping, selection_set: list[_FieldNode], errors: list) -> dict:
        projected: dict[str, Any] = {}
        for sub in selection_set:
            if sub.name == "__typename":
                projected[sub.name] = "Object" if isinstance(mapping, dict) else type(mapping).__name__
                continue
            if sub.name.startswith("__"):
                projected[sub.name] = self._project(self._resolve_schema_introspection(sub, errors), sub, errors)
                continue
            if sub.name not in mapping:
                errors.append({"message": f"Cannot query field '{sub.name}'."})
                projected[sub.name] = None
                continue
            projected[sub.name] = self._project(mapping[sub.name], sub, errors)
        return projected

    @staticmethod
    def _scalar_subselection_error(field_node: _FieldNode, errors: list) -> None:
        errors.append({"message": f"Field '{field_node.name}' does not have subfields."})
        return None

    def _resolve_schema_introspection(self, field_node: _FieldNode, errors: list):
        if not self.introspection:
            errors.append({"message": f"Introspection is disabled on this schema; cannot query '{field_node.name}'."})
            return None
        if field_node.name == "__schema":
            return {
                "queryType": {"name": "Query", "fields": [{"name": name} for name in sorted(self.query)]},
                "mutationType": {"name": "Mutation", "fields": [{"name": name} for name in sorted(self.mutation)]} if self.mutation else None,
            }
        errors.append({"message": f"Hayavega's minimal introspection supports only '__schema' and '__typename', not '{field_node.name}'."})
        return None


def graphql_view(schema: Schema):
    """Return a POST endpoint compatible with GraphQL JSON request bodies."""

    def endpoint(request):
        try:
            payload = request.json()
            query = payload["query"]
        except (KeyError, TypeError, ValueError):
            return json_response({"errors": [{"message": "Request body must contain a JSON 'query' field."}]}, 400)
        if not isinstance(query, str):
            return json_response({"errors": [{"message": "Request body must contain a JSON 'query' field."}]}, 400)
        variables = payload.get("variables")
        if variables is not None and not isinstance(variables, Mapping):
            return json_response({"errors": [{"message": "'variables' must be a JSON object."}]}, 400)
        operation_name = payload.get("operationName")
        return json_response(schema.execute(query, request, variables=variables, operation_name=operation_name))

    return endpoint
