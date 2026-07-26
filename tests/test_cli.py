import unittest
from unittest.mock import patch

from mangoo.cli import main


class CliTests(unittest.TestCase):
    @patch("mangoo.management.commands.execute_from_command_line")
    def test_launch_forwards_port_to_runserver(self, execute):
        main(["launch", "8001"])
        execute.assert_called_once_with(["mango", "runserver", "8001"])

    @patch("mangoo.management.commands.execute_from_command_line")
    def test_launch_supports_disabling_autoreload(self, execute):
        main(["launch", "8001", "--noreload"])
        execute.assert_called_once_with(["mango", "runserver", "8001", "--noreload"])
