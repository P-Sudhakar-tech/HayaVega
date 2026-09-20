"""Tests for the DB-backed background job queue (`mangoo.jobs`), against real SQLite."""

import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mangoo.jobs import _mark_failed, _mark_succeeded, claim_next_job, enqueue, initialize_jobs_db, run_worker


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


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


if __name__ == "__main__":
    unittest.main()
