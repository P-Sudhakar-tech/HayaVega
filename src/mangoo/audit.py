"""Persistent, privacy-safe audit events for security-relevant actions."""
from __future__ import annotations

import json

from .db import connect, user_id_definition


def initialize_audit_db(database) -> None:
    connection = connect(database)
    try:
        connection.execute(f"CREATE TABLE IF NOT EXISTS mangoo_audit_events (id {user_id_definition(connection.engine)}, actor_id INTEGER, event_type TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{{}}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.commit()
    finally:
        connection.close()


def record_audit_event(database, event_type: str, *, actor_id: int | None = None, metadata: dict | None = None) -> None:
    """Record a small event. Never put credentials, tokens, or headers in metadata."""
    initialize_audit_db(database)
    connection = connect(database)
    try:
        connection.execute("INSERT INTO mangoo_audit_events (actor_id, event_type, metadata) VALUES (?, ?, ?)", (actor_id, event_type, json.dumps(metadata or {}, separators=(",", ":"))))
        connection.commit()
    finally:
        connection.close()


def audit_events(database, *, limit: int = 100) -> list[dict]:
    if not 1 <= limit <= 1_000:
        raise ValueError("limit must be between 1 and 1000")
    initialize_audit_db(database)
    connection = connect(database)
    try:
        rows = connection.execute("SELECT id, actor_id, event_type, metadata, created_at FROM mangoo_audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]
    finally:
        connection.close()
