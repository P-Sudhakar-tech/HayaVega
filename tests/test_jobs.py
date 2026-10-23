"""Tests for the DB-backed background job queue (`mangoo.jobs`), against real SQLite."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import mangoo.jobs as jobs_module
from mangoo.jobs import _mark_failed, _mark_succeeded, claim_next_job, enqueue, initialize_jobs_db, retry_job, run_worker


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


class _StaleRowCursor:
    """Returns a pre-captured row regardless of what the table looks like
    now -- stands in for a real SELECT's result from *before* a competing
    claim ran, the same stale read a genuine race would produce."""

    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row is not None else []


class JobsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.database = str(Path(self._tmpdir.name) / "jobs.db")
        initialize_jobs_db(self.database)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _row(self, job_id):
        from mangoo.db import connect

        connection = connect(self.database)
        try:
            return connection.execute("SELECT * FROM mangoo_jobs WHERE id = ?", (job_id,)).fetchone()
        finally:
            connection.close()

    def test_enqueue_and_claim_round_trip(self):
        job_id = enqueue(self.database, "send_email", {"to": "a@example.com"})
        job = claim_next_job(self.database)
        self.assertIsNotNone(job)
        self.assertEqual(job.id, job_id)
        self.assertEqual(job.name, "send_email")
        self.assertEqual(job.payload, {"to": "a@example.com"})
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.max_attempts, 3)
        row = self._row(job_id)
        self.assertEqual(row["status"], "running")

    def test_claim_with_no_eligible_jobs_returns_none(self):
        self.assertIsNone(claim_next_job(self.database))

    def test_future_run_at_not_claimed_until_now_passes_it(self):
        job_id = enqueue(self.database, "send_email", run_at=_iso(3600))
        self.assertIsNone(claim_next_job(self.database))
        job = claim_next_job(self.database, now=_iso(3601))
        self.assertIsNotNone(job)
        self.assertEqual(job.id, job_id)

    def test_mark_succeeded_sets_status(self):
        job_id = enqueue(self.database, "send_email")
        claim_next_job(self.database)
        _mark_succeeded(self.database, job_id)
        self.assertEqual(self._row(job_id)["status"], "succeeded")

    def test_mark_failed_retries_when_attempts_below_max(self):
        job_id = enqueue(self.database, "send_email", max_attempts=3)
        claim_next_job(self.database)
        _mark_failed(self.database, job_id, attempts=1, max_attempts=3, exc=ValueError("boom"))
        row = self._row(job_id)
        self.assertEqual(row["status"], "pending")
        self.assertGreater(row["run_at"], datetime.now(timezone.utc).isoformat())
        self.assertIn("ValueError", row["last_error"])

    def test_mark_failed_dead_letters_when_attempts_exhausted(self):
        job_id = enqueue(self.database, "send_email", max_attempts=2)
        claim_next_job(self.database)
        _mark_failed(self.database, job_id, attempts=2, max_attempts=2, exc=ValueError("boom"))
        self.assertEqual(self._row(job_id)["status"], "dead")

    def test_last_error_is_truncated(self):
        job_id = enqueue(self.database, "send_email")
        claim_next_job(self.database)
        long_message = "x" * 500
        _mark_failed(self.database, job_id, attempts=1, max_attempts=3, exc=ValueError(long_message))
        stored = self._row(job_id)["last_error"]
        self.assertLess(len(stored), 500)
        self.assertNotIn("x" * 300, stored)

    def test_second_claim_of_same_job_returns_none(self):
        """Two workers racing for one job: only the first claim wins.

        This is the scenario the whole atomic-claim design exists to prevent
        -- two worker processes must never both run the same job. Calling
        `claim_next_job` twice in a row simulates the race: the second call
        must see the row already moved out of `pending` and return `None`.
        """
        enqueue(self.database, "send_email")
        first = claim_next_job(self.database)
        second = claim_next_job(self.database)
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_concurrent_threaded_claim_only_runs_once(self):
        """Two real threads race `claim_next_job` against the same SQLite file.

        A shared counter simulates "the job ran" -- it must be incremented
        exactly once no matter how the two threads interleave.
        """
        enqueue(self.database, "send_email")
        results = []
        lock = threading.Lock()

        def worker():
            job = claim_next_job(self.database)
            if job is not None:
                with lock:
                    results.append(job.id)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 1)

    def test_a_losing_claim_after_its_own_select_returns_none_deterministically(self):
        """The exact race claim_next_job's own docstring describes: this
        call's SELECT sees the row as pending, but a competing claim
        completes first, so this call's own UPDATE matches zero rows.

        test_concurrent_threaded_claim_only_runs_once above is a genuine
        concurrency smoke test (8 real threads), but whether it actually
        interleaves into *this specific* window -- SELECT-sees-pending,
        UPDATE-finds-nothing -- rather than the competing thread finishing
        its whole claim before this one's SELECT even runs (which takes
        the earlier "no candidate" branch instead) depends on real
        thread-scheduling timing that isn't guaranteed the same way on
        every platform/Python version. This test forces that exact
        interleaving directly instead of hoping real threads happen to
        produce it.
        """
        enqueue(self.database, "send_email")
        real_connect = jobs_module.connect
        triggered = []

        def stealing_connect(database):
            connection = real_connect(database)
            real_execute = connection.execute

            def execute(sql, params=()):
                if sql.startswith("SELECT id FROM mangoo_jobs WHERE status = 'pending'") and not triggered:
                    triggered.append(True)
                    cursor = real_execute(sql, params)
                    row = cursor.fetchone()
                    self.assertIsNotNone(row, "setup bug: nothing pending to race for")
                    # A competing claim completes in full, for real, before
                    # this call's own UPDATE below ever runs.
                    stolen = claim_next_job(self.database)
                    self.assertIsNotNone(stolen)
                    return _StaleRowCursor(row)
                return real_execute(sql, params)

            connection.execute = execute
            return connection

        with patch("mangoo.jobs.connect", side_effect=stealing_connect):
            result = claim_next_job(self.database)
        self.assertIsNone(result)

    def test_run_worker_processes_jobs_with_max_iterations(self):
        processed = []
        enqueue(self.database, "greet", {"who": "a"})
        enqueue(self.database, "greet", {"who": "b"})
        run_worker(self.database, {"greet": lambda payload: processed.append(payload["who"])}, poll_interval=0, max_iterations=3)
        self.assertEqual(sorted(processed), ["a", "b"])
        from mangoo.db import connect

        connection = connect(self.database)
        try:
            rows = connection.execute("SELECT status FROM mangoo_jobs").fetchall()
        finally:
            connection.close()
        self.assertTrue(all(row["status"] == "succeeded" for row in rows))

    def test_run_worker_no_handler_registered_marks_job_failed_not_crash(self):
        job_id = enqueue(self.database, "unregistered", max_attempts=3)
        run_worker(self.database, {}, poll_interval=0, max_iterations=1)
        row = self._row(job_id)
        self.assertIn(row["status"], {"pending", "dead"})
        self.assertIn("No handler registered", row["last_error"])

    def test_run_worker_survives_handler_exception_and_continues(self):
        job_id_1 = enqueue(self.database, "boom")
        job_id_2 = enqueue(self.database, "greet", {"who": "c"})
        processed = []

        def boom_handler(payload):
            raise RuntimeError("handler blew up")

        run_worker(
            self.database,
            {"boom": boom_handler, "greet": lambda payload: processed.append(payload["who"])},
            poll_interval=0,
            max_iterations=2,
        )
        self.assertEqual(processed, ["c"])
        self.assertEqual(self._row(job_id_2)["status"], "succeeded")
        row_1 = self._row(job_id_1)
        self.assertIn(row_1["status"], {"pending", "dead"})
        self.assertIn("RuntimeError", row_1["last_error"])


class JobSignalsTests(unittest.TestCase):
    """`enqueue`/`_mark_succeeded`/`_mark_failed`/`retry_job` each publish an
    event on the caller's `SignalBus`, after their own DB commit -- delivery
    is always-async (a worker thread), so each test connects a real
    subscriber and waits on a `threading.Event` it sets, the same pattern
    `test_admin_jobs.py`'s `SignalsIntegrationTests` already uses."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.database = str(Path(self._tmpdir.name) / "jobs.db")
        initialize_jobs_db(self.database)

    def _bus_and_waiter(self, event_name):
        from threading import Event

        from mangoo.addons.signals import SignalBus

        bus = SignalBus()
        self.addCleanup(bus.close)
        received = []
        seen = Event()

        def on_event(event):
            received.append(event)
            seen.set()

        bus.connect(event_name, on_event)
        return bus, received, seen

    def test_enqueue_publishes_job_enqueued(self):
        bus, received, seen = self._bus_and_waiter("job.enqueued")
        job_id = enqueue(self.database, "send_email", {"to": "a@example.com"}, signals=bus)
        self.assertTrue(seen.wait(2))
        self.assertEqual(received[0].data["job_id"], job_id)
        self.assertEqual(received[0].data["name"], "send_email")

    def test_mark_succeeded_publishes_job_succeeded(self):
        bus, received, seen = self._bus_and_waiter("job.succeeded")
        job_id = enqueue(self.database, "send_email")
        claim_next_job(self.database)
        _mark_succeeded(self.database, job_id, signals=bus)
        self.assertTrue(seen.wait(2))
        self.assertEqual(received[0].data["job_id"], job_id)

    def test_mark_failed_publishes_job_failed_when_a_retry_is_scheduled(self):
        bus, received, seen = self._bus_and_waiter("job.failed")
        job_id = enqueue(self.database, "send_email", max_attempts=3)
        claim_next_job(self.database)
        _mark_failed(self.database, job_id, attempts=1, max_attempts=3, exc=ValueError("boom"), signals=bus)
        self.assertTrue(seen.wait(2))
        self.assertEqual(received[0].data["job_id"], job_id)

    def test_mark_failed_publishes_job_dead_when_attempts_are_exhausted(self):
        bus, received, seen = self._bus_and_waiter("job.dead")
        job_id = enqueue(self.database, "send_email", max_attempts=2)
        claim_next_job(self.database)
        _mark_failed(self.database, job_id, attempts=2, max_attempts=2, exc=ValueError("boom"), signals=bus)
        self.assertTrue(seen.wait(2))
        self.assertEqual(received[0].data["job_id"], job_id)

    def test_retry_job_publishes_job_retried(self):
        bus, received, seen = self._bus_and_waiter("job.retried")
        job_id = enqueue(self.database, "send_email", max_attempts=1)
        claim_next_job(self.database)
        _mark_failed(self.database, job_id, attempts=1, max_attempts=1, exc=ValueError("boom"))
        self.assertTrue(retry_job(self.database, job_id, signals=bus))
        self.assertTrue(seen.wait(2))
        self.assertEqual(received[0].data["job_id"], job_id)


if __name__ == "__main__":
    unittest.main()
