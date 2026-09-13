import unittest

from mangoo.ui.components import Alert, Button, Card, Form, FormField, NavBar, Page


class ComponentTests(unittest.TestCase):
    def test_button_renders_variant_class_and_hx_attributes(self):
        result = Button("Refresh", hx_get="/fragment", hx_target="#list")
        self.assertIn("mgo-btn mgo-btn-primary", result)
        self.assertIn('hx-get="/fragment"', result)

    def test_alert_defaults_to_info_variant(self):
        self.assertIn("mgo-alert-info", Alert("hi"))

    def test_alert_variant_selects_class(self):
        self.assertIn("mgo-alert-success", Alert("ok", variant="success"))

    def test_card_wraps_heading_and_children(self):
        result = Card("Title", "body")
        self.assertIn("<h3>Title</h3>", result)
        self.assertIn("mgo-card", result)

    def test_form_field_renders_label_and_input(self):
        result = FormField("email", "Email address", type="email", required=True)
        self.assertIn('for="email"', result)
        self.assertIn('type="email"', result)
        self.assertIn("required", result)

    def test_form_sets_action_and_method(self):
        result = Form(FormField("x", "X"), action="/submit", hx_post="/submit", hx_target="#out")
        self.assertIn('action="/submit"', result)
        self.assertIn('hx-post="/submit"', result)

    def test_navbar_renders_brand_and_links(self):
        result = NavBar("Mangoo", [("Home", "/"), ("Admin", "/admin")])
        self.assertIn("Mangoo", result)
        self.assertIn('href="/admin"', result)

    def test_page_wraps_title_stylesheets_and_scripts(self):
        result = Page("Home", Card("x"), stylesheets=["/mangoo-ui/mangoo-ui.css"], scripts=["/mangoo-ui/mangoo-ui.js"])
        self.assertIn("<title>Home</title>", result)
        self.assertIn('href="/mangoo-ui/mangoo-ui.css"', result)
        self.assertIn('src="/mangoo-ui/mangoo-ui.js"', result)


if __name__ == "__main__":
    unittest.main()
