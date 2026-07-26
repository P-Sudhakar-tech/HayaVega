import json
import unittest

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
