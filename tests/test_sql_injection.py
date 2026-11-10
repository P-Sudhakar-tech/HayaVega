"""Regression tests proving SQL injection payloads are always treated as data, never as SQL.

Every query in Hayavega either uses parameterized `?` placeholders for values,
or validates identifiers (table/column names) against a strict allowlist
regex plus the entity's own declared columns before they ever reach a SQL
string. These tests attack both boundaries directly with classic injection
payloads and confirm the payload is stored/matched/rejected as inert text,
never executed, and that the underlying tables survive intact.
"""

import re
import tempfile
import unittest
from pathlib import Path

from hayavega.admin import ModelAdmin, admin_routes
from hayavega.auth import authenticate, create_session, create_superuser, create_user, get_user_by_username
from hayavega.db import connect
from hayavega.models import Entity, EntityError, Integer, Text
from hayavega.runtime import create_application
from hayavega.testing import TestClient

PASSWORD = "Correct-Horse-7!"

# A payload that, if ever concatenated unparameterized into a query, would
# either always match (auth bypass) or destroy the table it targets.
TAUTOLOGY_PAYLOAD = "' OR '1'='1"
DROP_TABLE_PAYLOAD = "x'; DROP TABLE hayavega_users; --"
DROP_PRODUCTS_PAYLOAD = "x'; DROP TABLE product_entries; --"


class Product(Entity):
    name = Text()
    price = Integer()


class AuthInjectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.sqlite3"
        create_user(self.database, "alice", "alice@example.com", PASSWORD)

    def tearDown(self):
        self.directory.cleanup()

    def _users_table_is_intact(self) -> bool:
        connection = connect(self.database)
        try:
            row = connection.execute("SELECT COUNT(*) AS total FROM hayavega_users").fetchone()
            return row["total"] >= 1
        finally:
            connection.close()

    def test_login_with_tautology_payload_as_username_fails(self):
        self.assertIsNone(authenticate(self.database, TAUTOLOGY_PAYLOAD, "irrelevant"))

    def test_login_with_tautology_payload_as_password_fails(self):
        self.assertIsNone(authenticate(self.database, "alice", TAUTOLOGY_PAYLOAD))

    def test_login_with_drop_table_payload_leaves_the_table_intact(self):
        self.assertIsNone(authenticate(self.database, DROP_TABLE_PAYLOAD, "irrelevant"))
        self.assertTrue(self._users_table_is_intact())
        # A legitimate login still works afterward -- the table was never touched.
        self.assertIsNotNone(authenticate(self.database, "alice", PASSWORD))

    def test_get_user_by_username_with_injection_payload_returns_none_not_all_users(self):
        self.assertIsNone(get_user_by_username(self.database, TAUTOLOGY_PAYLOAD))

    def test_username_containing_quotes_can_be_stored_and_queried_back_as_literal_text(self):
        odd_username = "o'brien"
        create_user(self.database, odd_username, "obrien@example.com", PASSWORD)
        user = get_user_by_username(self.database, odd_username)
        self.assertIsNotNone(user)
        self.assertEqual(user.username, odd_username)


class OrmInjectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.sqlite3"

    def tearDown(self):
        self.directory.cleanup()

    def _products_table_is_intact(self) -> bool:
        connection = connect(self.database)
        try:
            connection.execute("SELECT COUNT(*) FROM product_entries")
            return True
        except Exception:
            return False
        finally:
            connection.close()

    def test_injection_payload_in_a_value_is_stored_as_literal_text(self):
        collection = Product.collection(self.database)
        record = collection.append(name=DROP_PRODUCTS_PAYLOAD, price=1)
        fetched = collection.seek(id__exact=record.id).only()
        self.assertEqual(fetched.name, DROP_PRODUCTS_PAYLOAD)
        self.assertTrue(self._products_table_is_intact())

    def test_match_contains_with_injection_payload_matches_nothing_and_does_not_error(self):
        collection = Product.collection(self.database)
        collection.append(name="Widget", price=1)
        collection.append(name="Gadget", price=2)
        results = collection.seek(name__contains=TAUTOLOGY_PAYLOAD).fetch()
        self.assertEqual(results, [])
        self.assertTrue(self._products_table_is_intact())

    def test_match_rejects_a_column_name_outside_the_declared_schema(self):
        collection = Product.collection(self.database)
        with self.assertRaises(EntityError):
            collection.seek(**{"price; DROP TABLE product_entries; --": 1})

    def test_arrange_rejects_an_unsafe_ordering_field(self):
        collection = Product.collection(self.database)
        with self.assertRaises(EntityError):
            collection.browse().arrange("id; DROP TABLE product_entries; --")

    def test_revise_rejects_an_undeclared_column(self):
        collection = Product.collection(self.database)
        record = collection.append(name="Widget", price=1)
        with self.assertRaises(EntityError):
            collection.seek(id__exact=record.id).revise(**{"price; DROP TABLE product_entries; --": 5})

    def test_entity_with_a_malicious_table_name_is_rejected_before_any_query_runs(self):
        class Malicious(Entity):
            __table__ = "products; DROP TABLE product_entries; --"
            name = Text()

        with self.assertRaises(EntityError):
            Malicious.collection(self.database)

    def test_unknown_column_in_a_selection_filter_is_rejected(self):
        collection = Product.collection(self.database)
        with self.assertRaises(EntityError):
            collection.seek(nonexistent_column="x")


class AdminFormInjectionTests(unittest.TestCase):
    """The admin's add/edit forms are the most request-adjacent surface: raw
    HTTP form data flows through them into the ORM. Confirm an injection
    payload submitted through an actual form POST is stored as inert text."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "app.sqlite3"
        admin_user = create_superuser(self.database, "admin", "admin@example.com", PASSWORD)
        self.token = create_session(self.database, admin_user)
        self.app = create_application(admin_routes(self.database, models=[ModelAdmin(Product)]))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.directory.cleanup()

    def _headers(self):
        return {"Authorization": f"Bearer {self.token}"}

    def test_admin_create_form_stores_injection_payload_as_literal_text(self):
        get_response = self.client.get("/admin/product/new", headers=self._headers())
        csrf = re.search(r"name='csrf_token' value='([^']*)'", get_response.text).group(1)
        response = self.client.post(
            "/admin/product/new",
            form_data={"csrf_token": csrf, "name": DROP_PRODUCTS_PAYLOAD, "price": "1"},
            headers=self._headers(),
        )
        self.assertEqual(response.status_code, 303)

        connection = connect(self.database)
        try:
            row = connection.execute("SELECT name FROM product_entries").fetchone()
        finally:
            connection.close()
        self.assertEqual(row["name"], DROP_PRODUCTS_PAYLOAD)

        listing = self.client.get("/admin/product", headers=self._headers())
        self.assertEqual(listing.status_code, 200)


if __name__ == "__main__":
    unittest.main()
