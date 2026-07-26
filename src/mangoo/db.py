"""Pluggable DB-API database connections for Mangoo."""
from __future__ import annotations

from pathlib import Path
import sqlite3


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

    def execute(self, sql: str, params=()):
        cursor = self.raw.cursor()
        cursor.execute(sql.replace("?", "%s") if self.engine in {"postgresql", "mysql"} else sql, params)
        return cursor

    def commit(self):
        self.raw.commit()

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
