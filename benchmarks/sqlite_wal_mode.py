"""Benchmark `mangoo.db.enable_wal_mode()` against SQLite's untouched
defaults (journal_mode=DELETE, synchronous=FULL) for a representative
write transaction -- two inserts, one update, one commit, the same
shape a checkout/order-placement endpoint does.

Found via a real cross-framework e-commerce benchmark comparison: a
write-heavy authenticated endpoint built on Mangoo was only a near-tie
with a comparable FastAPI backend, unlike every other scenario Mangoo
won decisively. Profiling isolated the cost to SQLite's own commit
overhead (`fsync`, notoriously slow on Windows), not framework code.

Usage:
    python benchmarks/sqlite_wal_mode.py
"""

from __future__ import annotations

import sqlite3
import tempfile
import timeit
from pathlib import Path


def _fresh_connection(directory: str, *, wal: bool) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(directory) / ("wal.sqlite3" if wal else "default.sqlite3"))
    connection.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, total REAL)")
    connection.execute("CREATE TABLE order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price REAL)")
    connection.execute("CREATE TABLE products (id INTEGER PRIMARY KEY, stock INTEGER)")
    connection.execute("INSERT INTO products (id, stock) VALUES (1, 10000000)")
    connection.commit()
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def _place_order(connection: sqlite3.Connection) -> None:
    cursor = connection.execute("INSERT INTO orders (user_id, total) VALUES (?, ?)", (1, 9.99))
    connection.execute("INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?)", (cursor.lastrowid, 1, 1, 9.99))
    connection.execute("UPDATE products SET stock = stock - 1 WHERE id = 1")
    connection.commit()


def _report(name: str, default_fn, wal_fn, *, number: int = 2_000) -> None:
    default_time = timeit.timeit(default_fn, number=number)
    wal_time = timeit.timeit(wal_fn, number=number)
    ratio = default_time / wal_time if wal_time else float("inf")
    print(f"{name}:")
    print(f"  default (journal_mode=DELETE, synchronous=FULL): {default_time:.4f}s for {number:,} calls ({default_time / number * 1000:.3f} ms/call)")
    print(f"  WAL+NORMAL:                                       {wal_time:.4f}s for {number:,} calls ({wal_time / number * 1000:.3f} ms/call)")
    print(f"  -> WAL+NORMAL is {ratio:.2f}x faster" if ratio >= 1 else f"  -> default is {1 / ratio:.2f}x faster")
    print()


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        default_connection = _fresh_connection(directory, wal=False)
        wal_connection = _fresh_connection(directory, wal=True)
        assert default_connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert wal_connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"

        _report(
            "Write transaction (2 inserts + 1 update + commit): default vs WAL+NORMAL",
            lambda: _place_order(default_connection),
            lambda: _place_order(wal_connection),
        )

        default_connection.close()
        wal_connection.close()


if __name__ == "__main__":
    main()
