import importlib.util
import sys
import unittest
from unittest import mock

import mangoo.graphql as graphql_module
from mangoo.graphql import GraphQLSyntaxError, Schema, _depth, _Parser, _tokenize, graphql_view


class SchemaArgumentsTests(unittest.TestCase):
    def test_string_and_int_arguments_are_passed_to_the_resolver(self):
        schema = Schema(query={"greet": lambda request, name, times: f"{name}!" * times})
        result = schema.execute('{ greet(name: "Ada", times: 2) }', None)
        self.assertEqual(result, {"data": {"greet": "Ada!Ada!"}})

    def test_boolean_null_and_float_arguments_are_parsed(self):
        seen = {}

        def resolver(request, flag, missing, ratio):
            seen.update(flag=flag, missing=missing, ratio=ratio)
            return "ok"

        schema = Schema(query={"probe": resolver})
        schema.execute("{ probe(flag: true, missing: null, ratio: 1.5) }", None)
        self.assertEqual(seen, {"flag": True, "missing": None, "ratio": 1.5})

    def test_list_and_object_arguments_are_parsed(self):
        seen = {}

        def resolver(request, tags, filters):
            seen.update(tags=tags, filters=filters)
            return "ok"

        schema = Schema(query={"probe": resolver})
        schema.execute('{ probe(tags: ["a", "b"], filters: {active: true, limit: 5}) }', None)
        self.assertEqual(seen, {"tags": ["a", "b"], "filters": {"active": True, "limit": 5}})

    def test_enum_style_bareword_argument_is_passed_as_a_string(self):
        seen = {}
        schema = Schema(query={"probe": lambda request, status: seen.setdefault("status", status) or "ok"})
        schema.execute("{ probe(status: ACTIVE) }", None)
        self.assertEqual(seen["status"], "ACTIVE")


class LexerAndParserEdgeCaseTests(unittest.TestCase):
    def test_negative_and_float_and_exponent_numbers_are_parsed(self):
        seen = {}

        def resolver(request, a, b, c):
            seen.update(a=a, b=b, c=c)
            return "ok"

        schema = Schema(query={"probe": resolver})
        schema.execute("{ probe(a: -3, b: -1.5, c: 2e3) }", None)
        self.assertEqual(seen, {"a": -3, "b": -1.5, "c": 2000.0})

    def test_false_boolean_argument_is_parsed(self):
        schema = Schema(query={"probe": lambda request, flag: flag})
        result = schema.execute("{ probe(flag: false) }", None)
        self.assertEqual(result, {"data": {"probe": False}})

    def test_comments_are_skipped(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("# a comment\n{ greeting # trailing comment\n}", None)
        self.assertEqual(result, {"data": {"greeting": "hi"}})

    def test_list_typed_variable_declaration_is_parsed(self):
        schema = Schema(query={"probe": lambda request, ids: ids})
        result = schema.execute("query($ids: [Int!]!) { probe(ids: $ids) }", None, variables={"ids": [1, 2]})
        self.assertEqual(result, {"data": {"probe": [1, 2]}})

    def test_unterminated_string_is_a_syntax_error(self):
        schema = Schema(query={"probe": lambda request, name: name})
        result = schema.execute('{ probe(name: "unterminated) }', None)
        self.assertIn("errors", result)

    def test_missing_value_after_colon_is_a_syntax_error(self):
        schema = Schema(query={"probe": lambda request, name: name})
        result = schema.execute("{ probe(name:) }", None)
        self.assertIn("errors", result)

    def test_unexpected_content_after_operation_is_a_syntax_error(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ greeting } { greeting }", None)
        self.assertIn("errors", result)

    def test_unexpected_character_is_a_syntax_error(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ greeting % }", None)
        self.assertIn("errors", result)

    # The tests below construct `_tokenize`/`_Parser` directly (the same
    # pattern as `test_pathologically_nested_query_is_rejected_during_parsing`
    # above) because `Schema.execute()` prefers the Rust-ported
    # `mangoo_native.parse_graphql_document` whenever it is built, which
    # would otherwise never exercise these pure-Python code paths.

    def test_comment_without_a_trailing_newline_is_skipped_by_the_pure_python_tokenizer(self):
        tokens = _tokenize("foo # trailing comment with no newline")
        self.assertEqual([token.kind for token in tokens], ["name", "eof"])

    def test_string_literal_is_tokenized_by_the_pure_python_tokenizer(self):
        tokens = _tokenize('"hello"')
        self.assertEqual((tokens[0].kind, tokens[0].value), ("string", "hello"))

    def test_unterminated_string_is_rejected_by_the_pure_python_tokenizer(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _tokenize('"unterminated')
        self.assertIn("Unterminated string literal", str(ctx.exception))

    def test_invalid_escape_sequence_is_rejected_by_the_pure_python_tokenizer(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _tokenize('"\\q"')
        self.assertIn("Invalid string literal", str(ctx.exception))

    def test_exponent_numbers_with_and_without_a_sign_are_tokenized_by_the_pure_python_tokenizer(self):
        tokens = _tokenize("1e+3 2E-2 3e4")
        values = [token.value for token in tokens if token.kind == "float"]
        self.assertEqual(values, [1000.0, 0.02, 30000.0])

    def test_fragment_dot_is_rejected_by_the_pure_python_tokenizer(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _tokenize(".foo")
        self.assertIn("Fragments are not supported", str(ctx.exception))

    def test_unsupported_character_is_rejected_by_the_pure_python_tokenizer(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _tokenize("%")
        self.assertIn("Unexpected character", str(ctx.exception))

    def test_missing_colon_between_argument_name_and_value_is_rejected_by_the_pure_python_parser(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _Parser(_tokenize("{ foo(bar 1) }")).parse_document()
        self.assertIn("Expected ':'", str(ctx.exception))

    def test_non_name_token_where_a_name_is_expected_is_rejected_by_the_pure_python_parser(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _Parser(_tokenize("{ 1 }")).parse_document()
        self.assertIn("Expected a name", str(ctx.exception))

    def test_trailing_tokens_after_the_operation_are_rejected_by_the_pure_python_parser(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _Parser(_tokenize("{ greeting } { greeting }")).parse_document()
        self.assertIn("Unexpected content", str(ctx.exception))

    def test_empty_selection_set_is_rejected_by_the_pure_python_parser(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _Parser(_tokenize("{ }")).parse_document()
        self.assertIn("at least one field", str(ctx.exception))

    def test_false_null_and_bare_enum_values_are_parsed_by_the_pure_python_parser(self):
        _, _, _, selection_set = _Parser(_tokenize("{ probe(flag: false, missing: null, status: ACTIVE) }")).parse_document()
        self.assertEqual(selection_set[0].arguments, {"flag": False, "missing": None, "status": "ACTIVE"})

    def test_list_value_argument_is_parsed_by_the_pure_python_parser(self):
        _, _, _, selection_set = _Parser(_tokenize("{ probe(tags: [1, 2, 3]) }")).parse_document()
        self.assertEqual(selection_set[0].arguments, {"tags": [1, 2, 3]})

    def test_missing_value_after_colon_is_rejected_by_the_pure_python_parser(self):
        with self.assertRaises(GraphQLSyntaxError) as ctx:
            _Parser(_tokenize("{ probe(name:) }")).parse_document()
        self.assertIn("Expected a value", str(ctx.exception))

    def test_depth_of_an_empty_selection_set_is_zero(self):
        self.assertEqual(_depth([]), 0)


class VariableTests(unittest.TestCase):
    def test_variable_value_is_bound_from_the_variables_mapping(self):
        schema = Schema(query={"greet": lambda request, name: f"Hello, {name}!"})
        result = schema.execute("query($name: String!) { greet(name: $name) }", None, variables={"name": "Grace"})
        self.assertEqual(result, {"data": {"greet": "Hello, Grace!"}})

    def test_missing_required_variable_is_an_error(self):
        schema = Schema(query={"greet": lambda request, name: name})
        result = schema.execute("query($name: String!) { greet(name: $name) }", None, variables={})
        self.assertIn("required", result["errors"][0]["message"])

    def test_optional_variable_default_is_used_when_not_provided(self):
        schema = Schema(query={"greet": lambda request, name: name})
        result = schema.execute('query($name: String = "World") { greet(name: $name) }', None, variables={})
        self.assertEqual(result, {"data": {"greet": "World"}})

    def test_undeclared_variable_reference_is_an_error(self):
        schema = Schema(query={"greet": lambda request, name: name})
        result = schema.execute("{ greet(name: $name) }", None, variables={"name": "x"})
        self.assertIn("not defined", result["errors"][0]["message"])

    def test_operation_name_mismatch_is_an_error(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("query Hello { greeting }", None, operation_name="Other")
        self.assertIn("Unknown operation", result["errors"][0]["message"])

    def test_optional_variable_without_a_default_is_bound_to_none_when_not_provided(self):
        schema = Schema(query={"probe": lambda request, x: x})
        result = schema.execute("query($x: Int) { probe(x: $x) }", None, variables={})
        self.assertEqual(result, {"data": {"probe": None}})


class NestingTests(unittest.TestCase):
    def test_subfields_are_projected_from_a_mapping_result(self):
        schema = Schema(query={"user": lambda request: {"id": 1, "name": "Ada", "secret": "hidden"}})
        result = schema.execute("{ user { id name } }", None)
        self.assertEqual(result, {"data": {"user": {"id": 1, "name": "Ada"}}})

    def test_unknown_subfield_is_reported_as_an_error(self):
        schema = Schema(query={"user": lambda request: {"id": 1}})
        result = schema.execute("{ user { id missing } }", None)
        self.assertEqual(result["data"], {"user": {"id": 1, "missing": None}})
        self.assertIn("Cannot query field 'missing'", result["errors"][0]["message"])

    def test_subfields_are_projected_across_a_list_of_mappings(self):
        schema = Schema(query={"users": lambda request: [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Grace"}]})
        result = schema.execute("{ users { id } }", None)
        self.assertEqual(result, {"data": {"users": [{"id": 1}, {"id": 2}]}})

    def test_nested_selection_sets_project_two_levels_deep(self):
        schema = Schema(query={"team": lambda request: {"name": "Core", "lead": {"id": 1, "name": "Ada"}}})
        result = schema.execute("{ team { name lead { name } } }", None)
        self.assertEqual(result, {"data": {"team": {"name": "Core", "lead": {"name": "Ada"}}}})

    def test_selecting_subfields_on_a_scalar_is_an_error(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ greeting { nope } }", None)
        self.assertIn("does not have subfields", result["errors"][0]["message"])

    def test_null_value_with_subselection_stays_null(self):
        schema = Schema(query={"user": lambda request: None})
        result = schema.execute("{ user { id } }", None)
        self.assertEqual(result, {"data": {"user": None}})

    def test_none_items_in_a_projected_list_stay_none(self):
        schema = Schema(query={"users": lambda request: [{"id": 1}, None]})
        result = schema.execute("{ users { id } }", None)
        self.assertEqual(result, {"data": {"users": [{"id": 1}, None]}})

    def test_non_mapping_item_in_a_projected_list_is_a_subfield_error(self):
        schema = Schema(query={"users": lambda request: [{"id": 1}, "oops"]})
        result = schema.execute("{ users { id } }", None)
        self.assertEqual(result["data"]["users"], [{"id": 1}, None])
        self.assertIn("does not have subfields", result["errors"][0]["message"])

    def test_double_underscore_subfield_other_than_typename_resolves_through_introspection(self):
        schema = Schema(query={"user": lambda request: {"id": 1}}, introspection=True)
        result = schema.execute("{ user { id __foo } }", None)
        self.assertEqual(result["data"]["user"], {"id": 1, "__foo": None})
        self.assertIn("minimal introspection", result["errors"][0]["message"])


class MutationTests(unittest.TestCase):
    def test_mutation_field_is_executed_and_projected(self):
        def create_user(request, name):
            return {"id": 1, "name": name}

        schema = Schema(query={}, mutation={"createUser": create_user})
        result = schema.execute('mutation { createUser(name: "Ada") { id name } }', None)
        self.assertEqual(result, {"data": {"createUser": {"id": 1, "name": "Ada"}}})

    def test_mutation_against_a_schema_without_mutations_is_an_error(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("mutation { doStuff }", None)
        self.assertIn("does not define any mutations", result["errors"][0]["message"])

    def test_unknown_mutation_field_is_reported(self):
        schema = Schema(query={}, mutation={"createUser": lambda request: {}})
        result = schema.execute("mutation { deleteUser }", None)
        self.assertIn("Cannot query field 'deleteUser'", result["errors"][0]["message"])


class ValidationAndErrorHandlingTests(unittest.TestCase):
    def test_resolver_exception_becomes_a_field_error_not_a_crash(self):
        def boom(request):
            raise RuntimeError("kaboom")

        schema = Schema(query={"broken": boom})
        result = schema.execute("{ broken }", None)
        self.assertIsNone(result["data"]["broken"])
        self.assertEqual(result["errors"][0]["error_type"], "RuntimeError")

    def test_resolver_exception_message_is_hidden_by_default(self):
        """A resolver exception's message can carry confidential details (a
        database driver's connection-failure message routinely includes the
        host, port, and username it tried) -- it must never reach the
        client unless the schema was explicitly built with debug=True."""

        def boom(request):
            raise RuntimeError("connection to server at '10.0.0.5', user 'admin', password 'hunter2' failed")

        schema = Schema(query={"broken": boom})
        result = schema.execute("{ broken }", None)
        self.assertNotIn("10.0.0.5", result["errors"][0]["message"])
        self.assertNotIn("hunter2", result["errors"][0]["message"])
        self.assertEqual(result["errors"][0]["error_type"], "RuntimeError")

    def test_resolver_exception_message_is_included_in_debug_mode(self):
        def boom(request):
            raise RuntimeError("kaboom")

        schema = Schema(query={"broken": boom}, debug=True)
        result = schema.execute("{ broken }", None)
        self.assertIn("kaboom", result["errors"][0]["message"])

    def test_syntax_error_is_reported_without_raising(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ greeting", None)
        self.assertIn("errors", result)

    def test_fragments_are_rejected_with_a_clear_message(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ ...Frag }", None)
        self.assertIn("Fragments are not supported", result["errors"][0]["message"])

    def test_directives_are_rejected_with_a_clear_message(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ greeting @include(if: true) }", None)
        self.assertIn("Directives are not supported", result["errors"][0]["message"])

    def test_empty_selection_set_is_a_syntax_error(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ }", None)
        self.assertIn("errors", result)


class LimitTests(unittest.TestCase):
    def test_query_exceeding_max_depth_is_rejected(self):
        schema = Schema(query={"a": lambda request: {"b": {"c": {"d": "x"}}}}, max_depth=2)
        result = schema.execute("{ a { b { c { d } } } }", None)
        self.assertIn("exceeds the maximum", result["errors"][0]["message"])

    def test_query_within_max_depth_executes_normally(self):
        schema = Schema(query={"a": lambda request: {"b": "x"}}, max_depth=2)
        result = schema.execute("{ a { b } }", None)
        self.assertEqual(result, {"data": {"a": {"b": "x"}}})

    def test_query_exceeding_max_fields_is_rejected(self):
        schema = Schema(query={"a": lambda request: "x", "b": lambda request: "y"}, max_fields=1)
        result = schema.execute("{ a b }", None)
        self.assertIn("exceeding the maximum", result["errors"][0]["message"])

    def test_pathologically_nested_query_is_rejected_during_parsing(self):
        deeply_nested = "{ a " * 100 + "}" * 100
        with self.assertRaises(GraphQLSyntaxError):
            _Parser(_tokenize(deeply_nested)).parse_document()


class IntrospectionTests(unittest.TestCase):
    def test_typename_is_always_available_at_the_root(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ __typename }", None)
        self.assertEqual(result, {"data": {"__typename": "Query"}})

    def test_typename_reflects_mutation_operations(self):
        schema = Schema(query={}, mutation={"createUser": lambda request: {}})
        result = schema.execute("mutation { __typename }", None)
        self.assertEqual(result, {"data": {"__typename": "Mutation"}})

    def test_nested_typename_is_always_available(self):
        schema = Schema(query={"user": lambda request: {"id": 1}})
        result = schema.execute("{ user { id __typename } }", None)
        self.assertEqual(result, {"data": {"user": {"id": 1, "__typename": "Object"}}})

    def test_schema_introspection_is_disabled_by_default(self):
        schema = Schema(query={"greeting": lambda request: "hi"})
        result = schema.execute("{ __schema { queryType { name } } }", None)
        self.assertIn("Introspection is disabled", result["errors"][0]["message"])

    def test_schema_introspection_lists_root_fields_when_enabled(self):
        schema = Schema(query={"greeting": lambda request: "hi"}, mutation={"createUser": lambda request: {}}, introspection=True)
        result = schema.execute("{ __schema { queryType { name fields { name } } mutationType { name fields { name } } } }", None)
        self.assertEqual(
            result["data"]["__schema"],
            {
                "queryType": {"name": "Query", "fields": [{"name": "greeting"}]},
                "mutationType": {"name": "Mutation", "fields": [{"name": "createUser"}]},
            },
        )


class GraphQLViewTests(unittest.TestCase):
    def _make_view(self, schema):
        class FakeRequest:
            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        return lambda payload: graphql_view(schema)(FakeRequest(payload))

    def test_non_string_query_field_is_rejected(self):
        view = self._make_view(Schema(query={"greeting": lambda request: "hi"}))
        response = view({"query": 123})
        self.assertEqual(response.status, 400)

    def test_non_object_variables_are_rejected(self):
        view = self._make_view(Schema(query={"greeting": lambda request: "hi"}))
        response = view({"query": "{ greeting }", "variables": "nope"})
        self.assertEqual(response.status, 400)

    def test_unknown_introspection_field_is_reported_when_enabled(self):
        schema = Schema(query={"greeting": lambda request: "hi"}, introspection=True)
        result = schema.execute("{ __type }", None)
        self.assertIn("minimal introspection", result["errors"][0]["message"])


class NativeParserBridgeTests(unittest.TestCase):
    """`Schema.execute()` parses via `mangoo_native.parse_graphql_document`
    when built (benchmarks/native_graphql_parser.py measured a 2.7-3.9x win),
    falling back to `_Parser`/`_tokenize` otherwise -- same result either way."""

    _QUERY = """
        query ListPosts($limit: Int! = 10, $tags: [String!]) {
            posts(limit: $limit, tags: $tags, extra: { active: true, score: 3.5 }) {
                id
                title
                author { name }
            }
        }
    """

    def test_uses_the_native_parser_when_mangoo_native_is_built(self):
        from mangoo.graphql import _native_parse_graphql_document

        if _native_parse_graphql_document is None:
            self.skipTest("mangoo_native is not built in this environment")
        seen = {}
        schema = Schema(query={"posts": lambda request, **kwargs: seen.update(kwargs) or []})
        result = schema.execute(self._QUERY, None, variables={"tags": ["a", "b"]})
        self.assertEqual(result, {"data": {"posts": []}})
        self.assertEqual(seen, {"limit": 10, "tags": ["a", "b"], "extra": {"active": True, "score": 3.5}})

    def test_falls_back_to_pure_python_when_mangoo_native_is_unavailable(self):
        with mock.patch("mangoo.graphql._native_parse_graphql_document", None):
            seen = {}
            schema = Schema(query={"posts": lambda request, **kwargs: seen.update(kwargs) or []})
            result = schema.execute(self._QUERY, None, variables={"tags": ["a", "b"]})
            self.assertEqual(result, {"data": {"posts": []}})
            self.assertEqual(seen, {"limit": 10, "tags": ["a", "b"], "extra": {"active": True, "score": 3.5}})

    def test_native_and_fallback_paths_report_the_same_syntax_error(self):
        from mangoo.graphql import _native_parse_graphql_document

        if _native_parse_graphql_document is None:
            self.skipTest("mangoo_native is not built in this environment")
        schema = Schema(query={"greeting": lambda request: "hi"})
        native_result = schema.execute("{ greeting @skip }", None)
        with mock.patch("mangoo.graphql._native_parse_graphql_document", None):
            fallback_result = schema.execute("{ greeting @skip }", None)
        self.assertIn("errors", native_result)
        self.assertIn("errors", fallback_result)


class NativeParserImportFallbackTests(unittest.TestCase):
    """mangoo.graphql's `try: import mangoo_native ... except ImportError:
    _native_parse_graphql_document = None` only runs at module import time.
    `NativeParserBridgeTests` above mocks the already-bound attribute, which
    never executes those two lines.

    Reaching them for real means re-executing the module with
    `mangoo_native` made unimportable -- but `importlib.reload
    (mangoo.graphql)` would do that *in place*, rebinding every name in the
    live module (including `GraphQLSyntaxError`, `Schema`, ...) to new
    objects, which would desync every reference this test file already
    holds to the old ones (e.g. `assertRaises(GraphQLSyntaxError)` would stop
    matching). Loading a throwaway module object from the same source file
    under a different name avoids that: `mangoo.graphql` itself is never
    touched."""

    def test_module_falls_back_to_the_pure_python_parser_when_mangoo_native_is_unavailable(self):
        original = sys.modules.get("mangoo_native", None)
        sys.modules["mangoo_native"] = None  # sentinel: forces ImportError on import
        try:
            spec = importlib.util.spec_from_file_location("mangoo._graphql_reimport_test", graphql_module.__file__)
            fresh_module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = fresh_module  # dataclasses in this module need their class's module registered
            try:
                spec.loader.exec_module(fresh_module)
            finally:
                sys.modules.pop(spec.name, None)
            self.assertIsNone(fresh_module._native_parse_graphql_document)
            schema = fresh_module.Schema(query={"greet": lambda request, name: f"Hello, {name}!"})
            result = schema.execute('{ greet(name: "Ada") }', None)
            self.assertEqual(result, {"data": {"greet": "Hello, Ada!"}})
        finally:
            if original is None:
                sys.modules.pop("mangoo_native", None)
            else:
                sys.modules["mangoo_native"] = original


if __name__ == "__main__":
    unittest.main()
