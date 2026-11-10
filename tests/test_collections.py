import importlib.util
import sys
import unittest

import hayavega.collections as collections_module
from hayavega import mango_list
from hayavega.collections import _FallbackMangoList


class MangoListTests(unittest.TestCase):
    def test_mango_list_behaves_like_a_sequence(self):
        values = mango_list([1, 2])
        values.append(3)
        self.assertEqual(values[0], 1)
        self.assertEqual(values[-1], 3)
        self.assertEqual(values.to_list(), [1, 2, 3])
        self.assertEqual(repr(values), "mango_list([1, 2, 3])")


class FallbackMangoListTests(unittest.TestCase):
    """Exercises the pure-Python compatibility implementation directly --
    ``mango_list()`` only reaches it when ``hayavega_native`` isn't built,
    which isn't the case in this environment."""

    def test_repr_matches_the_mango_list_format(self):
        values = _FallbackMangoList([1, 2, 3])
        self.assertEqual(repr(values), "mango_list([1, 2, 3])")

    def test_to_list_returns_a_plain_list_copy(self):
        values = _FallbackMangoList([1, 2, 3])
        plain = values.to_list()
        self.assertEqual(plain, [1, 2, 3])
        self.assertIs(type(plain), list)


class NativeMangoListImportFallbackTests(unittest.TestCase):
    """hayavega.collections's `try: import hayavega_native ... except ImportError:
    _NativeMangoList = None` only runs at module import time. Instantiating
    `_FallbackMangoList` directly (as `FallbackMangoListTests` above does)
    never executes those two lines.

    Reaching them for real means re-executing the module with `hayavega_native`
    made unimportable -- but `importlib.reload(hayavega.collections)` would do
    that *in place*, rebinding every name in the live module to new objects.
    Other test files that already did `from hayavega.collections import X`
    (or `from hayavega import mango_list`, which re-exports it) would keep
    their stale reference, so a reload here could silently desync the rest
    of the process from the module they think they're using. Loading a
    throwaway module object from the same source file under a different name
    avoids that entirely: `hayavega.collections` itself is never touched."""

    def test_module_falls_back_to_pure_python_list_when_hayavega_native_is_unavailable(self):
        original = sys.modules.get("hayavega_native", None)
        sys.modules["hayavega_native"] = None  # sentinel: forces ImportError on import
        try:
            spec = importlib.util.spec_from_file_location("hayavega._collections_reimport_test", collections_module.__file__)
            fresh_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(fresh_module)
            self.assertIsNone(fresh_module._NativeMangoList)
            self.assertIs(fresh_module.MangoList, fresh_module._FallbackMangoList)
            values = fresh_module.mango_list([1, 2])
            values.append(3)
            self.assertEqual(values.to_list(), [1, 2, 3])
            self.assertEqual(repr(values), "mango_list([1, 2, 3])")
        finally:
            if original is None:
                sys.modules.pop("hayavega_native", None)
            else:
                sys.modules["hayavega_native"] = original
