import logging
import unittest

from hayavega.logging import JsonFormatter, configure_logging


class ConfigureLoggingTests(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger("hayavega")
        self._original_handlers = list(self.logger.handlers)
        self._original_level = self.logger.level

    def tearDown(self):
        for handler in list(self.logger.handlers):
            if handler not in self._original_handlers:
                self.logger.removeHandler(handler)
        self.logger.setLevel(self._original_level)

    def test_defaults_to_a_plain_stream_handler_at_info_level(self):
        handler = configure_logging()
        self.addCleanup(self.logger.removeHandler, handler)
        self.assertIn(handler, self.logger.handlers)
        self.assertIsInstance(handler, logging.StreamHandler)
        self.assertNotIsInstance(handler.formatter, JsonFormatter)
        self.assertEqual(self.logger.level, logging.INFO)

    def test_json_output_uses_the_json_formatter(self):
        handler = configure_logging(json_output=True)
        self.addCleanup(self.logger.removeHandler, handler)
        self.assertIsInstance(handler.formatter, JsonFormatter)

    def test_a_custom_handler_and_level_are_honored(self):
        custom_handler = logging.NullHandler()
        handler = configure_logging(level=logging.WARNING, handler=custom_handler)
        self.addCleanup(self.logger.removeHandler, handler)
        self.assertIs(handler, custom_handler)
        self.assertIn(custom_handler, self.logger.handlers)
        self.assertEqual(self.logger.level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
