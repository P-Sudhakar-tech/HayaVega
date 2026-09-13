import tempfile
import time
import unittest
from pathlib import Path

from mangoo.templates import TemplateNotFound, TemplateRenderError, TemplateSyntaxError, _resolve_chain, render_template


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


if __name__ == "__main__":
    unittest.main()
