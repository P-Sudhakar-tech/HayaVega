import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from hayavega.templates import (
    TemplateNotFound,
    TemplateRenderError,
    TemplateSyntaxError,
    _build_compiled_render,
    _compiled_render_for,
    _resolve_chain,
    _tokenize_expression,
    render_template,
)


class TemplateDirectory:
    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        return self

    def __exit__(self, *exc_info):
        self._tmp.cleanup()

    def write(self, name: str, content: str) -> None:
        (self.path / name).write_text(content, encoding="utf-8")


class VariableTests(unittest.TestCase):
    def test_variable_is_escaped_by_default(self):
        with TemplateDirectory() as templates:
            templates.write("hello.html", "<h1>Hello {{ name }}</h1>")
            result = render_template("hello.html", {"name": "<script>"}, directories=(templates.path,))
        self.assertEqual(result, "<h1>Hello &lt;script&gt;</h1>")

    def test_dotted_attribute_access_on_a_mapping(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ user.name }}")
            result = render_template("t.html", {"user": {"name": "Ada"}}, directories=(templates.path,))
        self.assertEqual(result, "Ada")

    def test_dotted_attribute_access_on_an_object(self):
        class User:
            name = "Grace"

        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ user.name }}")
            result = render_template("t.html", {"user": User()}, directories=(templates.path,))
        self.assertEqual(result, "Grace")

    def test_undefined_variable_renders_empty_by_default(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "[{{ missing }}]")
            result = render_template("t.html", {}, directories=(templates.path,))
        self.assertEqual(result, "[]")

    def test_undefined_variable_raises_in_debug_mode(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ missing }}")
            with self.assertRaises(TemplateRenderError):
                render_template("t.html", {}, directories=(templates.path,), debug=True)

    def test_path_traversal_is_rejected(self):
        with TemplateDirectory() as templates:
            with self.assertRaises(ValueError):
                render_template("../secret.html", directories=(templates.path,))

    def test_missing_template_raises_template_not_found(self):
        with TemplateDirectory() as templates:
            with self.assertRaises(TemplateNotFound):
                render_template("missing.html", directories=(templates.path,))

    def test_attribute_missing_on_a_plain_object_renders_empty(self):
        class User:
            name = "Ada"

        with TemplateDirectory() as templates:
            templates.write("t.html", "[{{ user.missing }}]")
            result = render_template("t.html", {"user": User()}, directories=(templates.path,))
        self.assertEqual(result, "[]")

    def test_dotted_path_breaks_early_once_an_intermediate_key_is_missing(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "[{{ a.b.c }}]")
            result = render_template("t.html", {"a": {}}, directories=(templates.path,))
        self.assertEqual(result, "[]")


class ConditionalTests(unittest.TestCase):
    def test_if_true_branch_renders(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if show %}yes{% endif %}")
            self.assertEqual(render_template("t.html", {"show": True}, directories=(templates.path,)), "yes")
            self.assertEqual(render_template("t.html", {"show": False}, directories=(templates.path,)), "")

    def test_if_else(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if show %}yes{% else %}no{% endif %}")
            self.assertEqual(render_template("t.html", {"show": False}, directories=(templates.path,)), "no")

    def test_if_elif_else_chain(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if score == 1 %}one{% elif score == 2 %}two{% else %}many{% endif %}")
            self.assertEqual(render_template("t.html", {"score": 2}, directories=(templates.path,)), "two")
            self.assertEqual(render_template("t.html", {"score": 9}, directories=(templates.path,)), "many")

    def test_not_and_and_or_operators(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if not a and b or c %}yes{% else %}no{% endif %}")
            self.assertEqual(render_template("t.html", {"a": False, "b": True, "c": False}, directories=(templates.path,)), "yes")
            self.assertEqual(render_template("t.html", {"a": True, "b": True, "c": False}, directories=(templates.path,)), "no")

    def test_in_operator(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if role in admins %}admin{% else %}guest{% endif %}")
            self.assertEqual(render_template("t.html", {"role": "x", "admins": ["x", "y"]}, directories=(templates.path,)), "admin")

    def test_string_and_none_literals(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", '{% if status == "active" %}on{% endif %}{% if missing == none %}empty{% endif %}')
            self.assertEqual(render_template("t.html", {"status": "active"}, directories=(templates.path,)), "onempty")

    def test_true_and_false_literals(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if flag == true %}T{% endif %}{% if flag == false %}F{% endif %}")
            self.assertEqual(render_template("t.html", {"flag": True}, directories=(templates.path,)), "T")
            self.assertEqual(render_template("t.html", {"flag": False}, directories=(templates.path,)), "F")

    def test_not_equal_operator(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if a != b %}yes{% else %}no{% endif %}")
            self.assertEqual(render_template("t.html", {"a": 1, "b": 2}, directories=(templates.path,)), "yes")
            self.assertEqual(render_template("t.html", {"a": 1, "b": 1}, directories=(templates.path,)), "no")

    def test_in_operator_against_a_non_container_is_false_instead_of_raising(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if x in n %}yes{% else %}no{% endif %}")
            result = render_template("t.html", {"x": 1, "n": 5}, directories=(templates.path,))
        self.assertEqual(result, "no")


class LoopTests(unittest.TestCase):
    def test_for_loop_renders_each_item(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for item in items %}[{{ item }}]{% endfor %}")
            result = render_template("t.html", {"items": ["a", "b", "c"]}, directories=(templates.path,))
        self.assertEqual(result, "[a][b][c]")

    def test_for_loop_over_empty_or_missing_is_blank(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for item in items %}x{% endfor %}")
            self.assertEqual(render_template("t.html", {"items": []}, directories=(templates.path,)), "")
            self.assertEqual(render_template("t.html", {}, directories=(templates.path,)), "")

    def test_loop_metadata_index_first_last(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for item in items %}{{ loop.index }}:{{ loop.first }}:{{ loop.last }} {% endfor %}")
            result = render_template("t.html", {"items": ["a", "b"]}, directories=(templates.path,))
        self.assertEqual(result, "1:True:False 2:False:True ")

    def test_nested_loop_and_dotted_access(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for user in users %}{{ user.name }}={{ loop.index }} {% endfor %}")
            result = render_template("t.html", {"users": [{"name": "Ada"}, {"name": "Grace"}]}, directories=(templates.path,))
        self.assertEqual(result, "Ada=1 Grace=2 ")

    def test_non_iterable_loop_target_raises_in_debug_mode(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for x in n %}{{ x }}{% endfor %}")
            with self.assertRaises(TemplateRenderError):
                render_template("t.html", {"n": 5}, directories=(templates.path,), debug=True)


class InheritanceTests(unittest.TestCase):
    def test_child_overrides_a_block(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "<html><title>{% block title %}Default{% endblock %}</title></html>")
            templates.write("child.html", '{% extends "base.html" %}{% block title %}Custom{% endblock %}')
            result = render_template("child.html", {}, directories=(templates.path,))
        self.assertEqual(result, "<html><title>Custom</title></html>")

    def test_child_falls_back_to_default_block_content(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "<p>{% block body %}fallback{% endblock %}</p>")
            templates.write("child.html", '{% extends "base.html" %}')
            result = render_template("child.html", {}, directories=(templates.path,))
        self.assertEqual(result, "<p>fallback</p>")

    def test_multi_level_inheritance_chain(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "[{% block a %}A0{% endblock %}][{% block b %}B0{% endblock %}]")
            templates.write("mid.html", '{% extends "base.html" %}{% block a %}A1{% endblock %}')
            templates.write("leaf.html", '{% extends "mid.html" %}{% block b %}B2{% endblock %}')
            result = render_template("leaf.html", {}, directories=(templates.path,))
        self.assertEqual(result, "[A1][B2]")

    def test_block_content_can_use_context_variables(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "{% block title %}base{% endblock %}")
            templates.write("child.html", '{% extends "base.html" %}{% block title %}Hi {{ name }}{% endblock %}')
            result = render_template("child.html", {"name": "Ada"}, directories=(templates.path,))
        self.assertEqual(result, "Hi Ada")

    def test_for_loop_inside_a_block_uses_the_interpreter_path_correctly(self):
        """A template using {% extends %}/{% block %} always renders via the
        pure-Python interpreter (the compiled render path only handles
        variable/if/for-only templates), so a for-loop inside a block is
        this suite's only coverage of _render_for's mutate-and-restore loop
        context handling once a template also uses inheritance."""
        with TemplateDirectory() as templates:
            templates.write("base.html", "<ul>{% block items %}{% endblock %}</ul>")
            templates.write("child.html", '{% extends "base.html" %}{% block items %}{% for item in items %}<li>{{ loop.index }}: {{ item }}{% if loop.last %} (last){% endif %}</li>{% endfor %}{% endblock %}')
            result = render_template("child.html", {"items": ["a", "b"]}, directories=(templates.path,))
        self.assertEqual(result, "<ul><li>1: a</li><li>2: b (last)</li></ul>")

    def test_unknown_block_in_child_raises_in_debug_mode(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "{% block title %}x{% endblock %}")
            templates.write("child.html", '{% extends "base.html" %}{% block typo %}y{% endblock %}')
            with self.assertRaises(TemplateSyntaxError):
                render_template("child.html", {}, directories=(templates.path,), debug=True)

    def test_circular_extends_is_rejected(self):
        with TemplateDirectory() as templates:
            templates.write("a.html", '{% extends "b.html" %}')
            templates.write("b.html", '{% extends "a.html" %}')
            with self.assertRaises(TemplateSyntaxError):
                render_template("a.html", {}, directories=(templates.path,))

    def test_for_loop_over_missing_value_inside_a_block_is_blank(self):
        """Forces the interpreter's _render_for (not the compiled path) so
        its own 'missing/empty iterable -> no items' branch gets exercised,
        mirroring test_for_loop_over_empty_or_missing_is_blank but via the
        inheritance-triggered interpreter path."""
        with TemplateDirectory() as templates:
            templates.write("base.html", "<ul>{% block items %}{% endblock %}</ul>")
            templates.write("child.html", '{% extends "base.html" %}{% block items %}{% for item in missing %}X{% endfor %}{% endblock %}')
            result = render_template("child.html", {}, directories=(templates.path,))
        self.assertEqual(result, "<ul></ul>")

    def test_non_iterable_for_loop_target_inside_a_block_raises_in_debug_mode(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "<ul>{% block items %}{% endblock %}</ul>")
            templates.write("child.html", '{% extends "base.html" %}{% block items %}{% for item in n %}X{% endfor %}{% endblock %}')
            with self.assertRaises(TemplateRenderError):
                render_template("child.html", {"n": 5}, directories=(templates.path,), debug=True)

    def test_non_iterable_for_loop_target_inside_a_block_is_blank_outside_debug_mode(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "<ul>{% block items %}{% endblock %}</ul>")
            templates.write("child.html", '{% extends "base.html" %}{% block items %}{% for item in n %}X{% endfor %}{% endblock %}')
            result = render_template("child.html", {"n": 5}, directories=(templates.path,))
        self.assertEqual(result, "<ul></ul>")

    def test_nested_loop_reusing_the_same_variable_name_restores_the_outer_values(self):
        """The outer loop's `x`/`loop` are already present in context by the
        time the inner loop (which reuses the name `x`) starts, so this is
        the only coverage of _render_for's had_var/had_loop *True* restore
        branches (as opposed to the pop() branches a single, non-nested
        loop already covers)."""
        with TemplateDirectory() as templates:
            templates.write("base.html", "<ul>{% block items %}{% endblock %}</ul>")
            templates.write(
                "child.html",
                '{% extends "base.html" %}{% block items %}{% for x in groups %}{% for x in x %}{{ x }}{% endfor %}{% endfor %}{% endblock %}',
            )
            result = render_template("child.html", {"groups": [[1, 2], [3]]}, directories=(templates.path,))
        self.assertEqual(result, "<ul>123</ul>")


class SyntaxErrorTests(unittest.TestCase):
    def test_unterminated_if_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if x %}yes")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {"x": True}, directories=(templates.path,))

    def test_unterminated_for_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for x in items %}{{ x }}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {"items": []}, directories=(templates.path,))

    def test_dangling_endif_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% endif %}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))

    def test_malformed_expression_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% if a == %}x{% endif %}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {"a": 1}, directories=(templates.path,))

    def test_extends_not_first_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "x")
            templates.write("t.html", 'hi {% extends "base.html" %}')
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))

    def test_unparseable_character_in_an_expression_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ a $ b }}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {"a": 1}, directories=(templates.path,))

    def test_empty_expression_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{  }}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))

    def test_trailing_extra_token_in_an_expression_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ x y }}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {"x": 1}, directories=(templates.path,))

    def test_bare_keyword_used_as_a_value_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ in }}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))

    def test_operator_where_a_value_is_expected_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ == }}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))

    def test_unquoted_extends_target_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("base.html", "x")
            templates.write("t.html", "{% extends base %}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))

    def test_unknown_tag_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% foobar %}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))

    def test_for_without_in_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for x items %}{{ x }}{% endfor %}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {"items": []}, directories=(templates.path,))

    def test_for_with_an_invalid_loop_variable_name_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for 1x in items %}{{ 1x }}{% endfor %}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {"items": []}, directories=(templates.path,))

    def test_block_with_an_invalid_name_is_a_syntax_error(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% block 1x %}y{% endblock %}")
            with self.assertRaises(TemplateSyntaxError):
                render_template("t.html", {}, directories=(templates.path,))


class ExpressionTokenizerTests(unittest.TestCase):
    """_tokenize_expression is always called with text that's already been
    through a caller-side .strip() (var/if/elif/for all do this before
    building an expression), so its own "only whitespace remains" early
    exit can't actually be reached through render_template -- it's a
    defensive branch for callers that don't strip. Tested directly."""

    def test_whitespace_only_text_tokenizes_to_no_tokens(self):
        self.assertEqual(_tokenize_expression("   ", "t", 1), [])


class ParserDefensiveConsistencyChecksTests(unittest.TestCase):
    """A handful of checks in _TemplateParser re-verify, right after calling
    self._parse_nodes(...), that the tag it stopped on is exactly the one
    its own stop_tags told it to stop on. Given _parse_nodes's only early
    return is gated on the tag already matching one of those stop_tags,
    that tag can never mismatch through any template _scan() can produce
    -- every real mismatch is instead caught earlier, inside _parse_nodes
    itself, with a generic "unexpected tag" error. These re-verification
    branches are therefore exercised directly here, against a hand-built
    segment list (not one _scan() would ever emit), with _parse_nodes
    stubbed out so the surrounding method's own check is what actually runs."""

    @staticmethod
    def _parser_with_stubbed_body(segments):
        from hayavega.templates import _TemplateParser

        parser = _TemplateParser(segments, "t")
        parser._parse_nodes = lambda stop_tags: []
        return parser

    def test_parse_reports_the_exact_leftover_tag(self):
        parser = self._parser_with_stubbed_body([("tag", "bogus", 1)])
        with self.assertRaises(TemplateSyntaxError):
            parser.parse()

    def test_elif_after_else_is_rejected_by_parse_if(self):
        parser = self._parser_with_stubbed_body([("tag", "if a", 1), ("tag", "else", 1), ("tag", "elif b", 1)])
        with self.assertRaises(TemplateSyntaxError):
            parser._parse_if("if a", 1)

    def test_second_else_is_rejected_by_parse_if(self):
        parser = self._parser_with_stubbed_body([("tag", "if a", 1), ("tag", "else", 1), ("tag", "else", 1)])
        with self.assertRaises(TemplateSyntaxError):
            parser._parse_if("if a", 1)

    def test_parse_if_reports_an_unexpected_tag_where_elif_else_or_endif_is_expected(self):
        parser = self._parser_with_stubbed_body([("tag", "if a", 1), ("tag", "bogus", 1)])
        with self.assertRaises(TemplateSyntaxError):
            parser._parse_if("if a", 1)

    def test_parse_for_reports_an_unexpected_tag_where_endfor_is_expected(self):
        parser = self._parser_with_stubbed_body([("tag", "for x in y", 1), ("tag", "bogus", 1)])
        with self.assertRaises(TemplateSyntaxError):
            parser._parse_for("for x in y", 1)

    def test_parse_block_reports_an_unexpected_tag_where_endblock_is_expected(self):
        parser = self._parser_with_stubbed_body([("tag", "block x", 1), ("tag", "bogus", 1)])
        with self.assertRaises(TemplateSyntaxError):
            parser._parse_block("block x", 1)


class CachingTests(unittest.TestCase):
    def test_editing_a_template_invalidates_the_cache(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "v1")
            self.assertEqual(render_template("t.html", {}, directories=(templates.path,)), "v1")
            time.sleep(0.01)
            templates.write("t.html", "v2")
            self.assertEqual(render_template("t.html", {}, directories=(templates.path,)), "v2")

    def test_repeated_renders_of_an_unchanged_template_are_consistent(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{{ n }}")
            first = render_template("t.html", {"n": 1}, directories=(templates.path,))
            second = render_template("t.html", {"n": 2}, directories=(templates.path,))
        self.assertEqual((first, second), ("1", "2"))


class CompiledRenderPathTests(unittest.TestCase):
    """Proves the compile()/exec()-based render path (templates.py's module
    docstring) is genuinely engaged for ordinary templates and genuinely
    bypassed for ones using {% extends %}/{% block %}, rather than trusting
    that it's wired up correctly just because output matches either way."""

    def test_plain_template_gets_a_compiled_render_function(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% for x in items %}{{ x }}{% endfor %}")
            render_template("t.html", {"items": ["a", "b"]}, directories=(templates.path,))
            root, _blocks = _resolve_chain("t.html", (templates.path,))
        self.assertTrue(root.compiled_render_attempted)
        self.assertIsNotNone(root.compiled_render)

    def test_template_using_block_does_not_get_a_compiled_render_function(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "{% block body %}x{% endblock %}")
            render_template("t.html", {}, directories=(templates.path,))
            root, _blocks = _resolve_chain("t.html", (templates.path,))
        self.assertTrue(root.compiled_render_attempted)
        self.assertIsNone(root.compiled_render)

    def test_editing_a_compiled_template_invalidates_its_compiled_function_too(self):
        with TemplateDirectory() as templates:
            templates.write("t.html", "v1")
            self.assertEqual(render_template("t.html", {}, directories=(templates.path,)), "v1")
            time.sleep(0.01)
            templates.write("t.html", "v2")
            self.assertEqual(render_template("t.html", {}, directories=(templates.path,)), "v2")
            root, _blocks = _resolve_chain("t.html", (templates.path,))
        self.assertTrue(root.compiled_render_attempted)


class CompiledRenderDoubleCheckedLockTests(unittest.TestCase):
    """`_compiled_render_for`'s double-checked lock: a second thread that
    loses the race to compile must find the work already done once it
    gets the lock, instead of compiling a second time."""

    def test_two_threads_racing_the_same_template_compile_only_once(self):
        with TemplateDirectory() as templates:
            templates.write("race.html", "static text, no expressions")
            root, _blocks = _resolve_chain("race.html", (templates.path,))
            root.compiled_render = None
            root.compiled_render_attempted = False

            compile_calls = []
            real_build = _build_compiled_render
            first_thread_is_compiling = threading.Event()
            let_first_thread_finish = threading.Event()

            def slow_build(nodes, name):
                compile_calls.append(1)
                first_thread_is_compiling.set()
                let_first_thread_finish.wait(timeout=5)
                return real_build(nodes, name)

            with patch("hayavega.templates._build_compiled_render", side_effect=slow_build):
                first = threading.Thread(target=_compiled_render_for, args=(root, "race.html"))
                first.start()
                self.assertTrue(first_thread_is_compiling.wait(timeout=5), "first thread never started compiling")

                # The second thread's own first (unlocked) check still sees
                # compiled_render_attempted=False -- it proceeds to block on
                # the lock the first thread currently holds while compiling.
                second = threading.Thread(target=_compiled_render_for, args=(root, "race.html"))
                second.start()
                time.sleep(0.2)  # give the second thread time to reach and block on the lock

                let_first_thread_finish.set()
                first.join(timeout=5)
                second.join(timeout=5)

            self.assertEqual(len(compile_calls), 1, "the second thread must not have compiled a second time")
            self.assertIsNotNone(root.compiled_render)


if __name__ == "__main__":
    unittest.main()
