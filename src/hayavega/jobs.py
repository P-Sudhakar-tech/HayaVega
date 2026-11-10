"""Durable, DB-backed background jobs for Hayavega.

Reuses the same connection layer (`db.py`) every other piece of persistent
state in this framework already uses -- no new broker or infrastructure
dependency (no Redis, no RabbitMQ). A worker process polls `hayavega_jobs` for
eligible rows via `run_worker`/`hayavega runworker`; latency between a job
becoming eligible and actually running is bounded by `poll_interval`, not
instant, since this is poll-based rather than push-based.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import msgspec

from .db import connect, user_id_definition

if TYPE_CHECKING:
    from .addons.signals import SignalBus

logger = logging.getLogger(__name__)

_ERROR_MESSAGE_LIMIT = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class JobRecord:
    id: int
    name: str
    payload: dict
    attempts: int
    max_attempts: int


def initialize_jobs_db(database) -> None:
    connection = connect(database)
    try:
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS hayavega_jobs ("
            f"id {user_id_definition(connection.engine)}, "
            f"name TEXT NOT NULL, "
            f"payload TEXT NOT NULL DEFAULT '{{}}', "
            f"status TEXT NOT NULL DEFAULT 'pending', "
            f"attempts INTEGER NOT NULL DEFAULT 0, "
            f"max_attempts INTEGER NOT NULL DEFAULT 3, "
            f"run_at TEXT NOT NULL, "
            f"last_error TEXT, "
            f"created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            f"updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.commit()
    finally:
        connection.close()


def enqueue(database, name: str, payload: dict | None = None, *, run_at: str | None = None, max_attempts: int = 3, signals: SignalBus | None = None) -> int:
    initialize_jobs_db(database)
    connection = connect(database)
    try:
        payload_json = msgspec.json.encode(payload or {}).decode("utf-8")
        cursor = connection.execute(
            "INSERT INTO hayavega_jobs (name, payload, run_at, max_attempts) VALUES (?, ?, ?, ?)" + (" RETURNING id" if connection.engine == "postgresql" else ""),
            (name, payload_json, run_at or _now(), max_attempts),
        )
        job_id = cursor.fetchone()["id"] if connection.engine == "postgresql" else cursor.lastrowid
        connection.commit()
    finally:
        connection.close()
    if signals is not None:
        signals.publish("job.enqueued", data={"job_id": job_id, "name": name})
    return job_id


def claim_next_job(database, *, now: str | None = None) -> JobRecord | None:
    """Atomically claim one eligible pending job, safe under concurrent callers.

    Portable across SQLite/PostgreSQL/MySQL: instead of `SELECT ... FOR UPDATE
    SKIP LOCKED` (no SQLite equivalent), this uses a select-then-guarded-update
    pattern. Step 1 finds a candidate row; step 2 flips it to `running` with an
    `UPDATE ... WHERE id = ? AND status = 'pending'` guard. If another caller
    claimed the same row between steps 1 and 2, that `UPDATE` matches zero
    rows -- checked via `cursor.rowcount`, which all three drivers
    (`sqlite3`, `psycopg`, `pymysql`) expose after an `UPDATE`. This is safe
    even though both steps run sequentially on the *same* `Connection`
    object, with no explicit transaction spanning them: the guarantee comes
    from the `WHERE status = 'pending'` condition on the `UPDATE` statement
    itself (only one caller's `UPDATE` can ever be the one that observes and
    flips a given row's status from `pending`), not from any isolation the
    connection provides between the `SELECT` and the `UPDATE`. A second
    caller running its own `SELECT` concurrently might also pick the same
    row id as a candidate, but at most one of the two `UPDATE`s will
    actually match it.
    """
    initialize_jobs_db(database)
    now = now or _now()
    connection = connect(database)
    try:
        candidate = connection.execute("SELECT id FROM hayavega_jobs WHERE status = 'pending' AND run_at <= ? ORDER BY run_at, id LIMIT 1", (now,)).fetchone()
        if candidate is None:
            return None
        job_id = candidate["id"]
        cursor = connection.execute("UPDATE hayavega_jobs SET status = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ? AND status = 'pending'", (now, job_id))
        if cursor.rowcount == 0:
            connection.commit()
            return None
        connection.commit()
        row = connection.execute("SELECT id, name, payload, attempts, max_attempts FROM hayavega_jobs WHERE id = ?", (job_id,)).fetchone()
        return JobRecord(id=row["id"], name=row["name"], payload=msgspec.json.decode(row["payload"]), attempts=row["attempts"], max_attempts=row["max_attempts"])
    finally:
        connection.close()


def _mark_succeeded(database, job_id: int, *, signals: SignalBus | None = None) -> None:
    connection = connect(database)
    try:
        connection.execute("UPDATE hayavega_jobs SET status = 'succeeded', updated_at = ? WHERE id = ?", (_now(), job_id))
        connection.commit()
    finally:
        connection.close()
    if signals is not None:
        signals.publish("job.succeeded", data={"job_id": job_id})


def _mark_failed(database, job_id: int, attempts: int, max_attempts: int, exc: Exception, *, signals: SignalBus | None = None) -> None:
    """Record a job failure, either rescheduling it or moving it to `dead`.

    The full exception, with traceback, is logged here via `logging` --
    operator-only, never surfaced to an end user. The `last_error` *database
    column* stores only `type(exc).__name__` plus `str(exc)` truncated to
    `_ERROR_MESSAGE_LIMIT` characters, matching `asgi.py`'s `_error_response`
    discipline of never exposing an exception's raw message/traceback outside
    a server-side log: a database column is a value that might later be
    surfaced (e.g. a future admin UI listing failed jobs), and an unbounded
    traceback in a column is also just bad practice -- unbounded size, and it
    may embed interpolated request data.

    Retries back off exponentially, `min(2 ** attempts, 300)` seconds,
    capped at 5 minutes -- uncapped exponential backoff could push a retry
    days into the future for a job that has failed many times, which is
    rarely what's wanted for a job a developer will want retried, and
    dead-lettered, within a reasonable operational window.
    """
    logger.exception("Job %s failed (attempt %s/%s)", job_id, attempts, max_attempts, exc_info=exc)
    message = f"{type(exc).__name__}: {str(exc)[:_ERROR_MESSAGE_LIMIT]}"
    connection = connect(database)
    try:
        if attempts < max_attempts:
            run_at = (datetime.now(timezone.utc) + timedelta(seconds=min(2**attempts, 300))).isoformat()
            connection.execute("UPDATE hayavega_jobs SET status = 'pending', run_at = ?, last_error = ?, updated_at = ? WHERE id = ?", (run_at, message, _now(), job_id))
        else:
            connection.execute("UPDATE hayavega_jobs SET status = 'dead', last_error = ?, updated_at = ? WHERE id = ?", (message, _now(), job_id))
        connection.commit()
    finally:
        connection.close()
    if signals is not None:
        signals.publish("job.dead" if attempts >= max_attempts else "job.failed", data={"job_id": job_id, "attempts": attempts})


def list_jobs(database, *, status: str | None = None, page: int = 1, page_size: int = 25) -> tuple[list[dict], int]:
    """Return one page of jobs (most recently updated first) and the total matching count.

    Powers the admin jobs view. ``status``, when given, restricts to one of
    ``pending``/``running``/``succeeded``/``dead``.
    """
    initialize_jobs_db(database)
    connection = connect(database)
    try:
        where = "WHERE status = ?" if status else ""
        params: tuple = (status,) if status else ()
        total = connection.execute(f"SELECT COUNT(*) AS count FROM hayavega_jobs {where}", params).fetchone()["count"]
        offset = max(0, (page - 1) * page_size)
        rows = connection.execute(
            f"SELECT id, name, status, attempts, max_attempts, run_at, last_error, created_at, updated_at FROM hayavega_jobs {where} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, page_size, offset),
        ).fetchall()
        return [dict(row) for row in rows], total
    finally:
        connection.close()


def retry_job(database, job_id: int, *, signals: SignalBus | None = None) -> bool:
    """Reset one dead-lettered job back to `pending` for another attempt.

    Only affects jobs currently `dead` -- guarded the same way
    `claim_next_job` guards its claim, via `WHERE status = 'dead'` on the
    `UPDATE`, so retrying a job that isn't actually dead (already retried by
    another admin request, or since picked up) is a no-op rather than a
    race. Returns whether a row was actually reset.
    """
    initialize_jobs_db(database)
    connection = connect(database)
    try:
        cursor = connection.execute(
            "UPDATE hayavega_jobs SET status = 'pending', attempts = 0, run_at = ?, last_error = NULL, updated_at = ? WHERE id = ? AND status = 'dead'",
            (_now(), _now(), job_id),
        )
        connection.commit()
        reset = cursor.rowcount > 0
    finally:
        connection.close()
    if reset and signals is not None:
        signals.publish("job.retried", data={"job_id": job_id})
    return reset


def run_worker(database, handlers: dict[str, Callable[[dict], None]], *, poll_interval: float = 1.0, signals: SignalBus | None = None, max_iterations: int | None = None) -> None:
    """Poll for eligible jobs and run them, forever (or `max_iterations` times).

    `max_iterations` exists purely for testability -- run exactly N poll
    cycles then return, instead of looping forever; tests always use it
    rather than spinning up a real infinite loop. One unhandled exception
    from one job's handler is always caught and resolved to a success or
    failure state on that job -- it never crashes this loop.
    """
    initialize_jobs_db(database)
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        job = claim_next_job(database)
        if job is None:
            time.sleep(poll_interval)
            continue
        handler = handlers.get(job.name)
        if handler is None:
            _mark_failed(database, job.id, job.attempts, job.max_attempts, KeyError(f"No handler registered for job {job.name!r}"), signals=signals)
            continue
        try:
            handler(job.payload)
        except Exception as exc:  # any handler failure routes to _mark_failed, never crashes the loop
            _mark_failed(database, job.id, job.attempts, job.max_attempts, exc, signals=signals)
        else:
            _mark_succeeded(database, job.id, signals=signals)
