import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mangoo import Decimal, Entity, Integer, Text, Toggle
from mangoo.models import EntityError, MissingEntityError
from mangoo.models import connect as models_connect


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

    def test_repeated_fetches_do_not_reissue_create_table(self):
        """A fresh Collection() (the common shape of every real request --
        Entity.collection(DATABASE) is called anew each time) must not pay
        for a second connection and a CREATE TABLE IF NOT EXISTS round trip
        once the table is already known to exist in this process."""
        self.products.append(name="Notebook", price=9.5, published=True, stock=10)
        with patch("mangoo.models.connect", wraps=models_connect) as spy:
            Product.collection(self.database).browse().arrange("id").fetch()
            Product.collection(self.database).browse().arrange("id").fetch()
        self.assertEqual(spy.call_count, 2, "each fetch should open exactly one connection once the table is already prepared")

    def test_prepare_still_creates_the_table_on_first_use_of_a_new_database(self):
        other_database = Path(self.directory.name) / "other.sqlite3"
        other_products = Product.collection(other_database)
        other_products.append(name="Pen", price=1.5, published=True, stock=1)
        self.assertEqual(other_products.browse().total(), 1)

    def test_values_returns_plain_dicts_for_requested_columns_only(self):
        self.products.append(name="Notebook", price=9.5, published=True, stock=10)
        self.products.append(name="Marker", price=3.0, published=False, stock=3)
        rows = self.products.browse().arrange("id").values("name", "price")
        self.assertEqual(rows, [{"name": "Notebook", "price": 9.5}, {"name": "Marker", "price": 3.0}])
        self.assertIsInstance(rows[0], dict)

    def test_values_with_no_fields_returns_every_column(self):
        self.products.append(name="Notebook", price=9.5, published=True, stock=10)
        row = self.products.browse().values()[0]
        self.assertEqual(set(row), {"id", "name", "price", "published", "stock"})

    def test_values_rejects_an_unknown_column(self):
        with self.assertRaises(EntityError):
            self.products.browse().values("not_a_real_column")

    def test_values_composes_with_seek_arrange_and_window(self):
        self.products.append(name="Notebook", price=9.5, published=True, stock=10)
        self.products.append(name="Marker", price=3.0, published=True, stock=3)
        self.products.append(name="Eraser", price=1.0, published=False, stock=20)
        rows = self.products.seek(published=True).arrange("-price").window(1).values("name")
        self.assertEqual(rows, [{"name": "Notebook"}])
