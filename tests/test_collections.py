import unittest

from mangoo import mango_list


class MangoListTests(unittest.TestCase):
    def test_mango_list_behaves_like_a_sequence(self):
        values = mango_list([1, 2])
        values.append(3)
        self.assertEqual(values[0], 1)
        self.assertEqual(values[-1], 3)
        self.assertEqual(values.to_list(), [1, 2, 3])
        self.assertEqual(repr(values), "mango_list([1, 2, 3])")
