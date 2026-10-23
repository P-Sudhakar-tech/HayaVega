import json
import unittest
from datetime import date

from mangoo import Decimal, Entity, EntitySerializer, Serializer, Text
from mangoo.serializers import SerializationError


class Product(Entity):
    name = Text()
    price = Decimal()


class SerializerTests(unittest.TestCase):
    def test_serializer_presents_entity_as_json(self):
        serializer = EntitySerializer(Product)
        product = Product(id=7, name="Notebook", price=9.5)
        self.assertEqual(json.loads(serializer.json(product)), {"id": 7, "name": "Notebook", "price": 9.5})

    def test_serializer_validates_external_payload(self):
        serializer = Serializer({"name": str, "stock": int})
        self.assertEqual(serializer.accept({"name": "Notebook", "stock": 2}), {"name": "Notebook", "stock": 2})
        with self.assertRaises(SerializationError):
            serializer.accept({"name": "Notebook", "stock": "two"})

    def test_serializer_rejects_unknown_values(self):
        serializer = Serializer({"name": str})
        with self.assertRaises(SerializationError):
            serializer.accept({"name": "Notebook", "extra": True})

    def test_presents_nested_mappings_lists_and_dates_as_plain_values(self):
        serializer = Serializer()
        payload = {"name": "Notebook", "tags": ["new", "sale"], "released": date(2024, 1, 1), "meta": {"color": "red"}}
        self.assertEqual(serializer.present(payload), {"name": "Notebook", "tags": ["new", "sale"], "released": "2024-01-01", "meta": {"color": "red"}})

    def test_present_rejects_a_value_that_is_not_an_entity_or_mapping(self):
        serializer = Serializer()
        with self.assertRaises(SerializationError):
            serializer.present(42)

    def test_present_many_presents_every_value(self):
        serializer = EntitySerializer(Product)
        products = [Product(id=1, name="Notebook", price=9.5), Product(id=2, name="Marker", price=3.0)]
        self.assertEqual(serializer.present_many(products), [{"id": 1, "name": "Notebook", "price": 9.5}, {"id": 2, "name": "Marker", "price": 3.0}])

    def test_accept_requires_every_declared_field_unless_partial(self):
        serializer = Serializer({"name": str, "stock": int})
        with self.assertRaises(SerializationError) as ctx:
            serializer.accept({"name": "Notebook"})
        self.assertIn("stock", str(ctx.exception))

    def test_accept_allows_missing_fields_when_partial(self):
        serializer = Serializer({"name": str, "stock": int})
        self.assertEqual(serializer.accept({"name": "Notebook"}, partial=True), {"name": "Notebook"})

    def test_accept_rejects_a_non_mapping_payload(self):
        serializer = Serializer({"name": str})
        with self.assertRaises(SerializationError):
            serializer.accept(["not", "a", "mapping"])

    def test_entity_serializer_accept_returns_known_fields_unchanged(self):
        serializer = EntitySerializer(Product)
        payload = {"name": "Notebook", "price": 9.5}
        self.assertEqual(serializer.accept(payload), payload)

    def test_entity_serializer_accept_rejects_a_non_mapping_payload(self):
        serializer = EntitySerializer(Product)
        with self.assertRaises(SerializationError):
            serializer.accept(["not", "a", "mapping"])

    def test_entity_serializer_accept_rejects_unknown_fields(self):
        serializer = EntitySerializer(Product)
        with self.assertRaises(SerializationError):
            serializer.accept({"name": "Notebook", "color": "red"})
