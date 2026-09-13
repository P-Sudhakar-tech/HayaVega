import re
import tempfile
import unittest
from pathlib import Path

from mangoo.admin import ModelAdmin, admin_routes
from mangoo.audit import audit_events
from mangoo.auth import create_session, create_superuser, create_user
from mangoo.models import Entity, Integer, Text, Toggle
from mangoo.runtime import create_application
from mangoo.testing import TestClient


class Product(Entity):
    name = Text()
    price = Integer()
    active = Toggle(default=True)


def _csrf_from(response) -> tuple[str, str]:
    token = re.search(r"name='csrf_token' value='([^']*)'", response.text).group(1)
    cookie_value = re.search(r"mangoo_csrf=([^;]*)", response.headers.get("Set-Cookie", "")).group(1)
    return token, cookie_value


class ModelAdminSetup(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.sqlite3"
        self.admin_user = create_superuser(self.database, "admin", "admin@example.com", "Strong-Password-7!")
        self.member = create_user(self.database, "member", "member@example.com", "Strong-Password-7!")
        self.admin_token = create_session(self.database, self.admin_user)
        self.member_token = create_session(self.database, self.member)
        self.app = create_application(admin_routes(self.database, models=[ModelAdmin(Product)]))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.directory.cleanup()

    def bearer(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def create_product(self, **fields) -> int:
        get_response = self.client.get("/admin/product/new", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        response = self.client.post("/admin/product/new", form_data={"csrf_token": token, **fields}, headers=headers)
        return int(response.headers["Location"].rsplit("/", 1)[1])


class RegistrationAndPermissionTests(ModelAdminSetup):
    def test_index_links_to_registered_models(self):
        response = self.client.get("/admin", headers=self.bearer(self.admin_token))
        self.assertIn("/admin/product", response.text)

    def test_non_superuser_is_denied_by_default(self):
        response = self.client.get("/admin/product", headers=self.bearer(self.member_token))
        self.assertEqual(response.status_code, 403)

    def test_custom_can_access_predicate_is_honored(self):
        app = create_application(admin_routes(self.database, models=[ModelAdmin(Product, can_access=lambda user: True)]))
        client = TestClient(app)
        response = client.get("/admin/product", headers=self.bearer(self.member_token))
        self.assertEqual(response.status_code, 200)

    def test_page_size_must_be_positive(self):
        with self.assertRaises(ValueError):
            ModelAdmin(Product, page_size=0)

    def test_default_slug_is_lowercased_class_name(self):
        self.assertEqual(ModelAdmin(Product).slug, "product")

    def test_custom_name_overrides_slug(self):
        self.assertEqual(ModelAdmin(Product, name="items").slug, "items")


class CreateTests(ModelAdminSetup):
    def test_create_redirects_to_the_new_record(self):
        record_id = self.create_product(name="Widget", price="10")
        response = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        self.assertIn("Widget", response.text)

    def test_created_record_appears_in_the_list_view(self):
        self.create_product(name="Widget", price="10")
        response = self.client.get("/admin/product", headers=self.bearer(self.admin_token))
        self.assertIn("Widget", response.text)

    def test_missing_required_field_is_a_validation_error_not_a_crash(self):
        get_response = self.client.get("/admin/product/new", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        response = self.client.post("/admin/product/new", form_data={"csrf_token": token, "price": "5"}, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("required", response.text)

    def test_malformed_integer_is_a_validation_error_not_a_crash(self):
        get_response = self.client.get("/admin/product/new", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        response = self.client.post("/admin/product/new", form_data={"csrf_token": token, "name": "x", "price": "not-a-number"}, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid value", response.text)

    def test_creation_is_audited(self):
        record_id = self.create_product(name="Widget", price="10")
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.product.created"]
        self.assertEqual(events[0]["metadata"]["id"], record_id)


class PaginationTests(ModelAdminSetup):
    def test_list_view_paginates(self):
        app = create_application(admin_routes(self.database, models=[ModelAdmin(Product, page_size=2)]))
        client = TestClient(app)
        for i in range(5):
            get_response = client.get("/admin/product/new", headers=self.bearer(self.admin_token))
            token, cookie_value = _csrf_from(get_response)
            headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
            client.post("/admin/product/new", form_data={"csrf_token": token, "name": f"item{i}", "price": str(i)}, headers=headers)

        page1 = client.get("/admin/product", headers=self.bearer(self.admin_token))
        page2 = client.get("/admin/product?page=2", headers=self.bearer(self.admin_token))
        page3 = client.get("/admin/product?page=3", headers=self.bearer(self.admin_token))
        self.assertIn("Page 1 of 3 (5 total)", page1.text)
        self.assertIn("item0", page1.text)
        self.assertNotIn("item2", page1.text)
        self.assertIn("item2", page2.text)
        self.assertIn("item4", page3.text)

    def test_out_of_range_page_clamps_to_last_page(self):
        self.create_product(name="Widget", price="10")
        response = self.client.get("/admin/product?page=999", headers=self.bearer(self.admin_token))
        self.assertIn("Page 1 of 1", response.text)

    def test_non_numeric_page_defaults_to_first_page(self):
        self.create_product(name="Widget", price="10")
        response = self.client.get("/admin/product?page=abc", headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 200)


class UpdateAndDeleteTests(ModelAdminSetup):
    def test_update_changes_the_record_and_is_audited(self):
        record_id = self.create_product(name="Widget", price="10")
        get_response = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        self.client.post(f"/admin/product/{record_id}", form_data={"csrf_token": token, "name": "Widget2", "price": "20"}, headers=headers)

        detail = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        self.assertIn("Widget2", detail.text)
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.product.updated"]
        self.assertEqual(sorted(events[0]["metadata"]["fields"]), ["name", "price"])

    def test_update_with_no_changes_does_not_add_an_update_event(self):
        record_id = self.create_product(name="Widget", price="10")
        get_response = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        self.client.post(f"/admin/product/{record_id}", form_data={"csrf_token": token, "name": "Widget", "price": "10"}, headers=headers)
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.product.updated"]
        self.assertEqual(events, [])

    def test_change_history_is_visible_on_the_detail_page(self):
        record_id = self.create_product(name="Widget", price="10")
        get_response = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        self.client.post(f"/admin/product/{record_id}", form_data={"csrf_token": token, "name": "Widget2", "price": "10"}, headers=headers)
        detail = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        self.assertIn("admin.product.updated", detail.text)
        self.assertIn("name", detail.text)

    def test_delete_removes_the_record_and_is_audited(self):
        record_id = self.create_product(name="Widget", price="10")
        get_response = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        response = self.client.post(f"/admin/product/{record_id}/delete", form_data={"csrf_token": token}, headers=headers)
        self.assertEqual(response.status_code, 303)

        listing = self.client.get("/admin/product", headers=self.bearer(self.admin_token))
        self.assertNotIn("Widget", listing.text)
        events = [e for e in audit_events(self.database) if e["event_type"] == "admin.product.deleted"]
        self.assertEqual(events[0]["metadata"]["id"], record_id)

    def test_detail_view_404s_for_unknown_id(self):
        response = self.client.get("/admin/product/999999", headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 404)

    def test_detail_view_404s_for_non_numeric_id(self):
        response = self.client.get("/admin/product/not-a-number", headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 404)

    def test_delete_via_get_is_method_not_allowed(self):
        record_id = self.create_product(name="Widget", price="10")
        response = self.client.get(f"/admin/product/{record_id}/delete", headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 405)

    def test_edit_with_validation_errors_shows_the_error_and_keeps_record_id_in_the_form(self):
        record_id = self.create_product(name="Widget", price="10")
        get_response = self.client.get(f"/admin/product/{record_id}", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        response = self.client.post(f"/admin/product/{record_id}", form_data={"csrf_token": token, "name": "x", "price": "nope"}, headers=headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn("valid value", response.text)


class SignalsIntegrationTests(ModelAdminSetup):
    def test_model_admin_actions_publish_admin_action_events(self):
        from threading import Event

        from mangoo.addons.signals import SignalBus

        bus = SignalBus()
        actions = []
        seen = Event()

        def on_action(event):
            actions.append(event.data["action"])
            if len(actions) >= 2:
                seen.set()

        bus.connect("admin.action", on_action)
        app = create_application(admin_routes(self.database, models=[ModelAdmin(Product)], signals=bus))
        client = TestClient(app)

        get_response = client.get("/admin/product/new", headers=self.bearer(self.admin_token))
        token, cookie_value = _csrf_from(get_response)
        headers = {**self.bearer(self.admin_token), "Cookie": f"mangoo_csrf={cookie_value}"}
        client.post("/admin/product/new", form_data={"csrf_token": token, "name": "Widget", "price": "10"}, headers=headers)
        client.get("/admin/product", headers=self.bearer(self.admin_token))

        seen.wait(2)
        self.assertIn("product.created", actions)
        self.assertIn("product.list_viewed", actions)


class CsrfProtectionTests(ModelAdminSetup):
    def test_cookie_session_without_csrf_token_is_rejected(self):
        token = create_session(self.database, self.admin_user)
        response = self.client.post("/admin/product/new", form_data={"name": "x", "price": "1"}, headers={"Cookie": f"mangoo_session={token}"})
        self.assertEqual(response.status_code, 403)

    def test_cookie_session_with_wrong_csrf_token_is_rejected(self):
        token = create_session(self.database, self.admin_user)
        get_response = self.client.get("/admin/product/new", headers={"Cookie": f"mangoo_session={token}"})
        _, cookie_value = _csrf_from(get_response)
        headers = {"Cookie": f"mangoo_session={token}; mangoo_csrf={cookie_value}"}
        response = self.client.post("/admin/product/new", form_data={"csrf_token": "wrong", "name": "x", "price": "1"}, headers=headers)
        self.assertEqual(response.status_code, 403)

    def test_cookie_session_with_correct_csrf_token_succeeds(self):
        token = create_session(self.database, self.admin_user)
        get_response = self.client.get("/admin/product/new", headers={"Cookie": f"mangoo_session={token}"})
        csrf, cookie_value = _csrf_from(get_response)
        headers = {"Cookie": f"mangoo_session={token}; mangoo_csrf={cookie_value}"}
        response = self.client.post("/admin/product/new", form_data={"csrf_token": csrf, "name": "x", "price": "1"}, headers=headers)
        self.assertEqual(response.status_code, 303)

    def test_bearer_token_requests_are_not_csrf_checked(self):
        response = self.client.post("/admin/product/new", form_data={"name": "x", "price": "1"}, headers=self.bearer(self.admin_token))
        self.assertEqual(response.status_code, 303)


if __name__ == "__main__":
    unittest.main()
