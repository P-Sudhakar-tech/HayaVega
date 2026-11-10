"""Covers hayavega/__init__.py's PEP 562 lazy-import machinery directly:
every public name is resolved via __getattr__ on first access instead of
being imported eagerly, so these tests exercise that layer directly rather
than relying on it being incidentally covered by every other test file
(which do already cover __getattr__'s success path and its AttributeError
branch, just never dir())."""

import unittest

import hayavega


class LazyImportTests(unittest.TestCase):
    def test_dir_includes_every_public_name(self):
        names = dir(hayavega)
        for name in hayavega.__all__:
            self.assertIn(name, names)

    def test_unknown_attribute_raises_attribute_error(self):
        with self.assertRaises(AttributeError):
            _ = hayavega.this_name_does_not_exist

    def test_every_public_name_resolves_to_the_right_module(self):
        for name, (module_name, attr_name) in hayavega._LAZY.items():
            module = __import__(f"hayavega.{module_name}", fromlist=[attr_name])
            self.assertIs(getattr(hayavega, name), getattr(module, attr_name))
