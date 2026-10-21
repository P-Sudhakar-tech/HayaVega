import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event as ThreadingEvent
from unittest.mock import patch

from mangoo import Decimal, Entity, Integer, Text, Timestamp, Toggle
from mangoo.addons.signals import SignalBus
from mangoo.models import EntityError, MissingEntityError
from mangoo.models import connect_readonly as models_connect_readonly


def _wait(flag: ThreadingEvent, message: str) -> None:
    if not flag.wait(timeout=2):
        raise AssertionError(message)


class Product(Entity):
    name = Text(unique=True)
    price = Decimal()
    published = Toggle(default=False)
    stock = Integer(default=0)


class Event(Entity):
    name = Text()
    occurred_at = Timestamp(nullable=True)


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
        with patch("mangoo.models.connect_readonly", wraps=models_connect_readonly) as spy:
            Product.collection(self.database).browse().arrange("id").fetch()
            Product.collection(self.database).browse().arrange("id").fetch()
        self.assertEqual(spy.call_count, 2, "each fetch should open exactly one read-only connection once the table is already prepared")

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


class SelectionSearchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "models.sqlite3"
        self.products = Product.collection(self.database)
        self.products.append(name="Notebook", price=9.5, published=True)
        self.products.append(name="Marker Pen", price=3.0, published=True)
        self.products.append(name="Eraser", price=1.0, published=False)

    def tearDown(self):
        self.directory.cleanup()

    def test_matches_any_field_in_the_given_list(self):
        results = self.products.browse().search("name", query="Marker").fetch()
        self.assertEqual([item.name for item in results], ["Marker Pen"])

    def test_is_a_substring_match_not_an_exact_match(self):
        results = self.products.browse().search("name", query="ark").fetch()
        self.assertEqual([item.name for item in results], ["Marker Pen"])

    def test_empty_query_is_a_no_op(self):
        self.assertEqual(self.products.browse().search("name", query="").total(), 3)

    def test_combines_with_match_via_and(self):
        results = self.products.seek(published=True).search("name", query="Notebook").fetch()
        self.assertEqual([item.name for item in results], ["Notebook"])

    def test_rejects_an_unknown_field(self):
        with self.assertRaises(EntityError):
            self.products.browse().search("not_a_real_column", query="x")

    def test_total_reflects_the_search(self):
        self.assertEqual(self.products.browse().search("name", query="e").total(), 3)

    def test_revise_respects_the_search_clause(self):
        updated = self.products.browse().search("name", query="Marker").revise(stock=99)
        self.assertEqual(updated, 1)
        self.assertEqual(self.products.seek(name="Marker Pen").only().stock, 99)
        self.assertEqual(self.products.seek(name="Notebook").only().stock, 0)

    def test_erase_respects_the_search_clause(self):
        deleted = self.products.browse().search("name", query="Marker").erase()
        self.assertEqual(deleted, 1)
        self.assertEqual(self.products.browse().total(), 2)


class TimestampColumnTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "events.sqlite3"
        self.events = Event.collection(self.database)

    def tearDown(self):
        self.directory.cleanup()

    def test_a_naive_datetime_is_assumed_utc_immediately_after_construction(self):
        """Reading the attribute right after setting it, before any
        database round trip, must already be an aware `datetime` -- not
        just something that happens to work after a fetch()."""
        event = Event(name="launch", occurred_at=datetime(2024, 1, 1, 12, 0, 0))
        self.assertEqual(event.occurred_at, datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_an_aware_non_utc_datetime_is_converted_to_utc(self):
        plus_five = timezone(timedelta(hours=5))
        event = Event(name="launch", occurred_at=datetime(2024, 1, 1, 17, 0, 0, tzinfo=plus_five))
        self.assertEqual(event.occurred_at, datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_round_trips_through_the_database_as_an_aware_utc_datetime(self):
        self.events.append(name="launch", occurred_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        fetched = self.events.browse().only()
        self.assertEqual(fetched.occurred_at, datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc))
        self.assertIsNotNone(fetched.occurred_at.tzinfo)

    def test_stored_representation_is_a_plain_iso_string_not_a_raw_datetime(self):
        """The stored value must be a plain string -- never a raw
        `datetime` object handed to a DB driver -- since sqlite3's
        implicit datetime adapters are deprecated as of Python 3.12 and
        removed in 3.14."""
        event = Event(name="launch", occurred_at=datetime(2024, 1, 1, 12, 0, 0))
        self.assertIsInstance(event.as_dict()["occurred_at"], str)

    def test_a_null_timestamp_stays_none(self):
        event = Event(name="launch")
        self.assertIsNone(event.occurred_at)


class ModelSignalsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.database = Path(self.directory.name) / "signals.sqlite3"
        self.bus = SignalBus()

    def tearDown(self):
        self.bus.close()
        self.directory.cleanup()

    def test_append_publishes_a_created_event(self):
        received = {}
        flag = ThreadingEvent()

        def on_created(event):
            received.update(name=event.name, data=dict(event.data))
            flag.set()

        self.bus.connect("product.created", on_created)
        products = Product.collection(self.database, signals=self.bus)
        created = products.append(name="Notebook", price=9.5)

        _wait(flag, "product.created was not published")
        self.assertEqual(received["name"], "product.created")
        self.assertEqual(received["data"], {"id": created.id})

    def test_revise_publishes_an_updated_event_with_the_touched_field_names(self):
        received = {}
        flag = ThreadingEvent()

        def on_updated(event):
            received.update(data=dict(event.data))
            flag.set()

        products = Product.collection(self.database)
        products.append(name="Notebook", price=9.5, stock=10)
        self.bus.connect("product.updated", on_updated)
        signaled = Product.collection(self.database, signals=self.bus)
        signaled.seek(name="Notebook").revise(stock=5)

        _wait(flag, "product.updated was not published")
        self.assertEqual(received["data"], {"fields": ["stock"], "count": 1})

    def test_erase_publishes_a_deleted_event_with_a_count(self):
        received = {}
        flag = ThreadingEvent()

        def on_deleted(event):
            received.update(data=dict(event.data))
            flag.set()

        products = Product.collection(self.database)
        products.append(name="Notebook", price=9.5)
        self.bus.connect("product.deleted", on_deleted)
        signaled = Product.collection(self.database, signals=self.bus)
        signaled.seek(name="Notebook").erase()

        _wait(flag, "product.deleted was not published")
        self.assertEqual(received["data"], {"count": 1})

    def test_no_signals_means_no_publishing_and_no_behavior_change(self):
        products = Product.collection(self.database)
        created = products.append(name="Notebook", price=9.5)
        self.assertEqual(products.seek(name="Notebook").revise(stock=5), 1)
        self.assertEqual(products.seek(id__exact=created.id).erase(), 1)

    def test_revise_with_no_matching_rows_does_not_publish(self):
        flag = ThreadingEvent()
        self.bus.connect("product.updated", lambda event: flag.set())
        signaled = Product.collection(self.database, signals=self.bus)
        signaled.seek(name="does-not-exist").revise(stock=5)
        self.assertFalse(flag.wait(timeout=0.2))
