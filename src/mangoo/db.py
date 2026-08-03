"""Pluggable DB-API database connections for Mangoo."""
from __future__ import annotations

from pathlib import Path
import sqlite3
from contextlib import contextmanager


class DatabaseConfigurationError(ValueError):
    pass


class DatabaseDriverError(RuntimeError):
    pass


def normalize_config(database: str | Path | dict) -> dict:
    if isinstance(database, (str, Path)):
        return {"ENGINE": "sqlite3", "NAME": str(database)}
    return dict(database)


class Connection:
    def __init__(self, raw, engine: str):
        self.raw, self.engine = raw, engine
        self._transaction_depth = 0

    def execute(self, sql: str, params=()):
        cursor = self.raw.cursor()
        cursor.execute(sql.replace("?", "%s") if self.engine in {"postgresql", "mysql"} else sql, params)
        return cursor

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    @contextmanager
    def atomic(self):
        """Run a block atomically, using savepoints for nested blocks.

        A connection must be used for all statements in the block.  SQLite,
        PostgreSQL, and MySQL/InnoDB all support the SQL used here.
        """
        depth = self._transaction_depth
        savepoint = f"mangoo_savepoint_{depth}"
        if depth == 0:
            self.raw.execute("BEGIN")
        else:
            self.raw.execute(f"SAVEPOINT {savepoint}")
        self._transaction_depth += 1
        try:
            yield self
        except Exception:
            if depth == 0:
                self.raw.rollback()
            else:
                self.raw.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.raw.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        else:
            if depth == 0:
                self.raw.commit()
            else:
                self.raw.execute(f"RELEASE SAVEPOINT {savepoint}")
        finally:
            self._transaction_depth -= 1

    def close(self):
        self.raw.close()


def connect(database: str | Path | dict) -> Connection:
    config = normalize_config(database)
    engine = str(config.get("ENGINE", "sqlite3")).lower()
    if engine in {"sqlite", "sqlite3"}:
        raw = sqlite3.connect(config["NAME"])
        raw.row_factory = sqlite3.Row
        return Connection(raw, "sqlite3")
    if engine in {"postgres", "postgresql"}:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise DatabaseDriverError("PostgreSQL requires `uv add psycopg[binary]`.") from error
        raw = psycopg.connect(
            dbname=config["NAME"], host=config.get("HOST", "localhost"), port=config.get("PORT", 5432),
            user=config.get("USER"), password=config.get("PASSWORD"), row_factory=dict_row,
        )
        return Connection(raw, "postgresql")
    if engine in {"mysql", "mariadb"}:
        try:
            import pymysql
        except ImportError as error:
            raise DatabaseDriverError("MySQL/MariaDB requires `uv add pymysql`.") from error
        raw = pymysql.connect(
            host=config.get("HOST", "localhost"), port=int(config.get("PORT", 3306)), user=config.get("USER"),
            password=config.get("PASSWORD"), database=config["NAME"], cursorclass=pymysql.cursors.DictCursor,
        )
        return Connection(raw, "mysql")
    raise DatabaseConfigurationError(f"Unsupported database engine: {engine}")


def user_id_definition(engine: str) -> str:
    return {"sqlite3": "INTEGER PRIMARY KEY AUTOINCREMENT", "postgresql": "BIGSERIAL PRIMARY KEY", "mysql": "BIGINT AUTO_INCREMENT PRIMARY KEY"}[engine]
