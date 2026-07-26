import tempfile
import unittest
from pathlib import Path

from mangoo import Decimal, Entity, Integer, Text, Toggle
from mangoo.models import MissingEntityError


class Product(Entity):
    name = Text(unique=True)
    price = Decimal()
    published = Toggle(default=False)
    stock = Integer(default=0)


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "models.sqlite3"
        self.products = Product.collection(self.database)

    def tearDown(self):
        self.directory.cleanup()

    def test_append_and_chainable_selection(self):
        self.products.append(name="Notebook", price=9.5, published=True, stock=10)
        self.products.append(name="Marker", price=3.0, published=False, stock=3)
        result = self.products.seek(published=True).arrange("-price").fetch()
        self.assertEqual([item.name for item in result], ["Notebook"])
        self.assertEqual(self.products.browse().total(), 2)

    def test_selection_revision_and_erasure(self):
        self.products.append(name="Notebook", price=9.5, published=True, stock=10)
        self.assertEqual(self.products.seek(name="Notebook").revise(stock=5), 1)
        self.assertEqual(self.products.seek(stock__at_most=5).only().stock, 5)
        self.assertEqual(self.products.seek(name="Notebook").erase(), 1)
        self.assertIsNone(self.products.browse().first())

    def test_only_requires_exactly_one_result(self):
        with self.assertRaises(MissingEntityError):
            self.products.browse().only()
