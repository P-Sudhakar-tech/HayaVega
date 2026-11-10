import unittest

from hayavega import Entity, Integer, Text, Toggle
from hayavega.forms import BooleanField, CharField, DecimalField, EmailField, Field, FieldError, Form, IntegerField, entity_form


class _FakeRequest:
    def __init__(self):
        self.cookies: dict[str, str] = {}


class FieldTests(unittest.TestCase):
    def test_char_field_requires_a_value_by_default(self):
        with self.assertRaises(FieldError):
            CharField().clean("")

    def test_char_field_accepts_empty_when_not_required(self):
        self.assertIsNone(CharField(required=False).clean(""))

    def test_char_field_enforces_max_length(self):
        with self.assertRaises(FieldError):
            CharField(max_length=3).clean("abcd")
        self.assertEqual(CharField(max_length=3).clean("abc"), "abc")

    def test_integer_field_converts_and_rejects_non_numeric(self):
        self.assertEqual(IntegerField().clean("42"), 42)
        with self.assertRaises(FieldError):
            IntegerField().clean("not-a-number")

    def test_decimal_field_converts_and_rejects_non_numeric(self):
        self.assertEqual(DecimalField().clean("9.5"), 9.5)
        with self.assertRaises(FieldError):
            DecimalField().clean("not-a-number")

    def test_boolean_field_is_true_only_for_a_non_empty_submitted_value(self):
        self.assertTrue(BooleanField().clean("on"))
        self.assertFalse(BooleanField().clean(""))

    def test_boolean_field_is_not_required_by_default(self):
        self.assertFalse(BooleanField().required)

    def test_email_field_accepts_a_valid_address(self):
        self.assertEqual(EmailField().clean("alice@example.com"), "alice@example.com")

    def test_email_field_rejects_an_invalid_address(self):
        with self.assertRaises(FieldError):
            EmailField().clean("not-an-email")

    def test_base_field_convert_returns_the_raw_value_unchanged(self):
        self.assertEqual(Field().clean("raw-value"), "raw-value")

    def test_email_field_widget_renders_an_email_input(self):
        widget = EmailField().widget("email", "alice@example.com")
        self.assertIn("type='email'", widget)
        self.assertIn("value='alice@example.com'", widget)
        self.assertIn("required", widget)

    def test_decimal_field_widget_renders_a_step_any_number_input(self):
        widget = DecimalField(required=False).widget("price", 9.5)
        self.assertIn("type='number'", widget)
        self.assertIn("step='any'", widget)
        self.assertNotIn("required", widget)


class DemoForm(Form):
    name = CharField()
    age = IntegerField(required=False)
    subscribed = BooleanField()


class FormTests(unittest.TestCase):
    def test_unbound_form_is_never_valid(self):
        self.assertFalse(DemoForm().is_valid())

    def test_valid_submission_populates_cleaned_data(self):
        form = DemoForm({"name": "Alice", "age": "30", "subscribed": "on"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data, {"name": "Alice", "age": 30, "subscribed": True})
        self.assertEqual(form.errors, {})

    def test_invalid_submission_collects_per_field_errors(self):
        form = DemoForm({"name": "", "age": "not-a-number"})
        self.assertFalse(form.is_valid())
        self.assertIn("name", form.errors)
        self.assertIn("age", form.errors)

    def test_optional_field_may_be_omitted(self):
        form = DemoForm({"name": "Alice", "subscribed": ""})
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.cleaned_data["age"])

    def test_render_without_a_request_has_no_csrf_field(self):
        form = DemoForm({"name": "Alice", "subscribed": ""})
        form.is_valid()
        self.assertNotIn("csrf_token", form.render())

    def test_render_with_a_request_includes_a_csrf_field(self):
        form = DemoForm({"name": "Alice", "subscribed": ""}, request=_FakeRequest())
        form.is_valid()
        self.assertIn("name='csrf_token'", form.render())

    def test_render_includes_the_error_message_for_an_invalid_field(self):
        form = DemoForm({"name": ""})
        form.is_valid()
        self.assertIn("This field is required.", form.render())

    def test_a_custom_field_only_needs_convert(self):
        class UpperField(Field):
            def _convert(self, raw):
                return raw.upper()

        class ShoutForm(Form):
            message = UpperField()

        form = ShoutForm({"message": "hi"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["message"], "HI")


class Product(Entity):
    name = Text(unique=True)
    stock = Integer(default=0)
    published = Toggle(default=False)


class EntityFormTests(unittest.TestCase):
    def test_generates_one_field_per_column_except_id(self):
        ProductForm = entity_form(Product)
        self.assertEqual(set(ProductForm._fields), {"name", "stock", "published"})

    def test_field_types_match_column_types(self):
        ProductForm = entity_form(Product)
        self.assertIsInstance(ProductForm._fields["stock"], IntegerField)
        self.assertIsInstance(ProductForm._fields["published"], BooleanField)
        self.assertIsInstance(ProductForm._fields["name"], CharField)

    def test_a_column_with_a_default_is_not_required(self):
        ProductForm = entity_form(Product)
        self.assertFalse(ProductForm._fields["stock"].required)
        self.assertTrue(ProductForm._fields["name"].required)

    def test_fields_argument_restricts_which_columns_are_included(self):
        ProductForm = entity_form(Product, fields=("name",))
        self.assertEqual(set(ProductForm._fields), {"name"})

    def test_valid_submission_cleans_every_column(self):
        ProductForm = entity_form(Product)
        form = ProductForm({"name": "Notebook", "stock": "10", "published": "on"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data, {"name": "Notebook", "stock": 10, "published": True})


if __name__ == "__main__":
    unittest.main()
