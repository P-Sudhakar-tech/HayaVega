import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from hayavega.autoreload import has_changes, run_with_reloader, snapshot_python_files


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

    @patch("hayavega.autoreload.time.sleep", side_effect=KeyboardInterrupt)
    @patch("hayavega.autoreload.subprocess.Popen")
    def test_ctrl_c_terminates_reloader_child(self, popen, sleep):
        process = MagicMock()
        process.poll.return_value = None
        popen.return_value = process
        with tempfile.TemporaryDirectory() as directory:
            run_with_reloader(["python", "hayavega.py", "launch"], Path(directory))
        process.terminate.assert_called_once()
        process.wait.assert_called_once()

    def test_a_real_detected_change_restarts_the_real_child_then_stops_on_its_own_exit(self):
        """Real subprocesses throughout, no mocking: a first real child
        process is started, a watched file is changed while it's running
        (triggering the terminate+restart branch), and the second real
        child this spawns exits on its own (the natural-exit `return`
        branch) -- the child itself tracks its own invocation count via a
        counter file, so there's no timing-sensitive marker to race."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            watched = root / "app.py"
            watched.write_text("VALUE = 1\n", encoding="utf-8")
            counter_file = root / "invocations.txt"

            # First invocation (n=0): sleeps, simulating a running server --
            # long enough for the test's change below to be detected.
            # Second invocation (n=1, after a restart): exits immediately.
            child_script = f"import pathlib, time\np = pathlib.Path({str(counter_file)!r})\nn = int(p.read_text()) if p.exists() else 0\np.write_text(str(n + 1))\ntime.sleep(5) if n == 0 else None\n"
            command = [sys.executable, "-c", child_script]

            def change_the_watched_file_once_the_first_child_is_running():
                deadline = time.monotonic() + 5
                while not counter_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                watched.write_text("VALUE = 2\n", encoding="utf-8")

            trigger = threading.Thread(target=change_the_watched_file_once_the_first_child_is_running, daemon=True)
            trigger.start()
            run_with_reloader(command, root, interval=0.1)  # must return on its own, not hang
            trigger.join(timeout=5)
            self.assertEqual(counter_file.read_text(), "2", "the child should have been restarted exactly once")
