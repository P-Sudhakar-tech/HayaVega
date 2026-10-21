import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from mangoo.autoreload import has_changes, run_with_reloader, snapshot_python_files


class AutoreloadTests(unittest.TestCase):
    def test_snapshot_records_project_python_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "views.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")

            snapshot = snapshot_python_files(root)

            self.assertIn(source, snapshot)

    def test_has_changes_detects_new_or_modified_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = snapshot_python_files(root)
            (root / "urls.py").write_text("urlpatterns = []\n", encoding="utf-8")

            self.assertTrue(has_changes(before, snapshot_python_files(root)))

    @patch("mangoo.autoreload.time.sleep", side_effect=KeyboardInterrupt)
    @patch("mangoo.autoreload.subprocess.Popen")
    def test_ctrl_c_terminates_reloader_child(self, popen, sleep):
        process = MagicMock()
        process.poll.return_value = None
        popen.return_value = process
        with tempfile.TemporaryDirectory() as directory:
            run_with_reloader(["python", "mango.py", "launch"], Path(directory))
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
