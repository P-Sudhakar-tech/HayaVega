"""Modern declarative entities and chainable data selections for Mangoo."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
import re
from typing import Any

from .db import connect


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EntityError(ValueError):
    pass


class MissingEntityError(EntityError):
    pass


class Column:
    def __init__(self, *, default: Any = None, nullable: bool = False, unique: bool = False, primary: bool = False):
        self.default, self.nullable, self.unique, self.primary = default, nullable, unique, primary
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, instance, owner):
        return self if instance is None else instance._values.get(self.name)

    def __set__(self, instance, value):
        instance._values[self.name] = self.coerce(value)

    def coerce(self, value):
        if value is None and not self.nullable and not self.primary:
            raise EntityError(f"{self.name} cannot be null")
        return value

    def sql_type(self, engine: str) -> str:
        return "TEXT"


class Key(Column):
    def __init__(self):
        super().__init__(primary=True)

    def sql_type(self, engine: str) -> str:
        return {"sqlite3": "INTEGER PRIMARY KEY AUTOINCREMENT", "postgresql": "BIGSERIAL PRIMARY KEY", "mysql": "BIGINT AUTO_INCREMENT PRIMARY KEY"}[engine]


class Text(Column):
    def coerce(self, value):
        value = super().coerce(value)
        return None if value is None else str(value)


class Integer(Column):
    def sql_type(self, engine: str) -> str:
        return "BIGINT" if engine in {"postgresql", "mysql"} else "INTEGER"

    def coerce(self, value):
        value = super().coerce(value)
        return None if value is None else int(value)


class Decimal(Column):
    def sql_type(self, engine: str) -> str:
        return "DOUBLE PRECISION" if engine == "postgresql" else "DOUBLE"

    def coerce(self, value):
        value = super().coerce(value)
        return None if value is None else float(value)


class Toggle(Column):
    def sql_type(self, engine: str) -> str:
        return "BOOLEAN" if engine == "postgresql" else "INTEGER"

    def coerce(self, value):
        value = super().coerce(value)
        return None if value is None else bool(value)


class Timestamp(Text):
    def coerce(self, value):
        if isinstance(value, datetime):
            value = value.isoformat()
        return super().coerce(value)


class EntityMeta(type):
    def __new__(mcls, name, bases, namespace):
        columns = OrderedDict()
        for base in bases:
            columns.update(getattr(base, "_columns", {}))
        for column_name, value in namespace.items():
            if isinstance(value, Column):
                columns[column_name] = value
        cls = super().__new__(mcls, name, bases, namespace)
        cls._columns = columns
        if name != "Entity":
            cls.__table__ = getattr(cls, "__table__", f"{name.lower()}_entries")
        return cls


class Entity(metaclass=EntityMeta):
    id = Key()

    def __init__(self, **values):
        self._values = {}
        for name, column in self._columns.items():
            if name in values:
                setattr(self, name, values[name])
            elif callable(column.default):
                setattr(self, name, column.default())
            elif column.default is not None:
                setattr(self, name, column.default)
            else:
                self._values[name] = None

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    @classmethod
    def collection(cls, database) -> "Collection":
        return Collection(cls, database)


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise EntityError(f"Unsafe identifier: {value}")
    return value


class Collection:
    def __init__(self, entity: type[Entity], database):
        self.entity, self.database = entity, database
        _identifier(entity.__table__)

    def prepare(self) -> None:
        connection = connect(self.database)
        try:
            definitions = []
            for name, column in self.entity._columns.items():
                definition = f"{_identifier(name)} {column.sql_type(connection.engine)}"
                if not column.primary:
                    if not column.nullable:
                        definition += " NOT NULL"
                    if column.unique:
                        definition += " UNIQUE"
                definitions.append(definition)
            connection.execute(f"CREATE TABLE IF NOT EXISTS {_identifier(self.entity.__table__)} ({', '.join(definitions)})")
            connection.commit()
        finally:
            connection.close()

    def append(self, **values) -> Entity:
        self.prepare()
        entity = self.entity(**values)
        data = {name: value for name, value in entity.as_dict().items() if name != "id" and value is not None}
        if not data:
            raise EntityError("At least one non-key value is required.")
        fields = list(data)
        connection = connect(self.database)
        try:
            query = f"INSERT INTO {_identifier(self.entity.__table__)} ({', '.join(_identifier(name) for name in fields)}) VALUES ({', '.join('?' for _ in fields)})"
            cursor = connection.execute(query + (" RETURNING id" if connection.engine == "postgresql" else ""), tuple(data.values()))
            connection.commit()
            entity.id = cursor.fetchone()["id"] if connection.engine == "postgresql" else cursor.lastrowid
            return entity
        finally:
            connection.close()

    def seek(self, **criteria) -> "Selection":
        return Selection(self).match(**criteria)

    def browse(self) -> "Selection":
        return Selection(self)

    def total(self) -> int:
        return self.browse().total()


class Selection:
    def __init__(self, collection: Collection, clauses=(), ordering=(), limit: int | None = None, offset: int = 0):
        self.collection, self.clauses, self.ordering, self.limit, self.offset = collection, tuple(clauses), tuple(ordering), limit, offset

    def _copy(self, **changes) -> "Selection":
        return Selection(self.collection, changes.get("clauses", self.clauses), changes.get("ordering", self.ordering), changes.get("limit", self.limit), changes.get("offset", self.offset))

    def match(self, **criteria) -> "Selection":
        clauses = list(self.clauses)
        operators = {"exact": "=", "not": "!=", "above": ">", "at_least": ">=", "below": "<", "at_most": "<=", "contains": "LIKE"}
        for expression, value in criteria.items():
            name, _, modifier = expression.partition("__")
            _identifier(name)
            if name not in self.collection.entity._columns:
                raise EntityError(f"Unknown column: {name}")
            operator = operators.get(modifier or "exact")
            if operator is None:
                raise EntityError(f"Unknown selection operator: {modifier}")
            clauses.append((name, operator, f"%{value}%" if modifier == "contains" else value))
        return self._copy(clauses=clauses)

    def arrange(self, *fields: str) -> "Selection":
        ordering = []
        for field in fields:
            direction, name = ("DESC", field[1:]) if field.startswith("-") else ("ASC", field)
            _identifier(name)
            if name not in self.collection.entity._columns:
                raise EntityError(f"Unknown column: {name}")
            ordering.append((name, direction))
        return self._copy(ordering=ordering)

    def window(self, count: int, skip: int = 0) -> "Selection":
        if count < 1 or skip < 0:
            raise EntityError("window count must be positive and skip cannot be negative")
        return self._copy(limit=count, offset=skip)

    def _sql(self, fields: str = "*") -> tuple[str, tuple]:
        table = _identifier(self.collection.entity.__table__)
        query, values = f"SELECT {fields} FROM {table}", []
        if self.clauses:
            query += " WHERE " + " AND ".join(f"{_identifier(name)} {operator} ?" for name, operator, _ in self.clauses)
            values.extend(value for _, _, value in self.clauses)
        if self.ordering:
            query += " ORDER BY " + ", ".join(f"{_identifier(name)} {direction}" for name, direction in self.ordering)
        if self.limit is not None:
            query += " LIMIT ? OFFSET ?"
            values.extend((self.limit, self.offset))
        return query, tuple(values)

    def fetch(self) -> list[Entity]:
        self.collection.prepare()
        connection = connect(self.collection.database)
        try:
            rows = connection.execute(*self._sql()).fetchall()
            return [self.collection.entity(**dict(row)) for row in rows]
        finally:
            connection.close()

    def first(self) -> Entity | None:
        rows = self.window(1).fetch()
        return rows[0] if rows else None

    def only(self) -> Entity:
        rows = self.window(2).fetch()
        if len(rows) != 1:
            raise MissingEntityError("Selection did not resolve to exactly one entity.")
        return rows[0]

    def total(self) -> int:
        self.collection.prepare()
        connection = connect(self.collection.database)
        try:
            query, values = self._sql("COUNT(*) AS total")
            return int(connection.execute(query, values).fetchone()["total"])
        finally:
            connection.close()

    def revise(self, **values) -> int:
        if not values:
            return 0
        for name in values:
            _identifier(name)
            if name not in self.collection.entity._columns or name == "id":
                raise EntityError(f"Cannot revise column: {name}")
        self.collection.prepare()
        connection = connect(self.collection.database)
        try:
            query = f"UPDATE {_identifier(self.collection.entity.__table__)} SET " + ", ".join(f"{_identifier(name)} = ?" for name in values)
            where, criteria = self._sql()[0].partition(" WHERE ")[2], self._sql()[1]
            if where:
                where = where.split(" ORDER BY ")[0].split(" LIMIT ")[0]
                query += " WHERE " + where
            cursor = connection.execute(query, tuple(values.values()) + tuple(criteria[:len(self.clauses)]))
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()

    def erase(self) -> int:
        self.collection.prepare()
        connection = connect(self.collection.database)
        try:
            query = f"DELETE FROM {_identifier(self.collection.entity.__table__)}"
            values = ()
            if self.clauses:
                query += " WHERE " + " AND ".join(f"{_identifier(name)} {operator} ?" for name, operator, _ in self.clauses)
                values = tuple(value for _, _, value in self.clauses)
            cursor = connection.execute(query, values)
            connection.commit()
            return cursor.rowcount
        finally:
            connection.close()
