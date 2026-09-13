import unittest

from mangoo.ui.elements import Markup, div, fragment, input_, tag


class ElementTests(unittest.TestCase):
    def test_basic_element_renders_children_and_attributes(self):
        result = div("hello", class_="x")
        self.assertEqual(result, '<div class="x">hello</div>')

    def test_underscore_attribute_becomes_hyphenated(self):
        result = div(hx_get="/fragment", hx_target="#list")
        self.assertEqual(result, '<div hx-get="/fragment" hx-target="#list"></div>')

    def test_class_and_for_strip_trailing_underscore(self):
        result = div(class_="a", for_="b")
        self.assertIn('class="a"', result)
        self.assertIn('for="b"', result)

    def test_boolean_true_renders_bare_attribute(self):
        result = div(mgo_boost=True)
        self.assertEqual(result, "<div mgo-boost></div>")

    def test_boolean_false_and_none_omit_attribute(self):
        result = div(required=False, disabled=None)
        self.assertEqual(result, "<div></div>")

    def test_children_are_escaped_by_default(self):
        result = div("<script>alert(1)</script>")
        self.assertEqual(result, "<div>&lt;script&gt;alert(1)&lt;/script&gt;</div>")

    def test_markup_children_are_not_escaped(self):
        result = div(Markup("<b>bold</b>"))
        self.assertEqual(result, "<div><b>bold</b></div>")

    def test_attribute_values_are_escaped(self):
        result = div(title='"quoted"')
        self.assertEqual(result, '<div title="&quot;quoted&quot;"></div>')

    def test_void_element_has_no_closing_tag_or_children(self):
        result = input_(name="x", type="text")
        self.assertEqual(result, '<input name="x" type="text">')

    def test_fragment_groups_children_with_no_wrapper(self):
        result = fragment(div("a"), div("b"))
        self.assertEqual(result, "<div>a</div><div>b</div>")

    def test_tag_builds_an_arbitrary_element(self):
        custom = tag("custom-el")
        self.assertEqual(custom("x"), "<custom-el>x</custom-el>")


if __name__ == "__main__":
    unittest.main()
