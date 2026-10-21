"""Modern declarative entities and chainable data selections for Mangoo."""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from .db import connect, connect_readonly, normalize_config

if TYPE_CHECKING:
    from .addons.signals import SignalBus

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
    """Always timezone-aware UTC -- not Django's opt-in ``USE_TZ``, just
    the only behavior this column has. Stored as a plain ISO string
    (SQL-param-safe across every engine and Python version -- sqlite3's
    implicit ``datetime`` adapters are deprecated since 3.12 and gone in
    3.14, so a raw ``datetime`` object is never handed to a driver
    directly); reading it back through attribute access (`__get__`)
    parses it into an aware UTC `datetime`, whether the string just came
    from coercing a freshly-set value or from a fetched database row --
    one consistent Python-side type either way.
    """

    def coerce(self, value):
        if isinstance(value, datetime):
            aware = value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
            value = aware.isoformat()
        return super().coerce(value)

    def __get__(self, instance, owner):
        value = super().__get__(instance, owner)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        return value


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
    __table__: ClassVar[str]
    _columns: ClassVar[OrderedDict[str, Column]]

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
    def collection(cls, database, *, signals: SignalBus | None = None) -> Collection:
        """`signals`, when given a `SignalBus`, publishes
        `"<entity>.created"`/`"<entity>.updated"`/`"<entity>.deleted"`
        from `append`/`revise`/`erase` -- opt-in, matching this
        codebase's "opt-in, not on by default" policy elsewhere
        (`Hardening`), so every existing caller's behavior is unchanged
        when omitted. See `docs/topics/signals.md`."""
        return Collection(cls, database, signals=signals)


def _identifier(value: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise EntityError(f"Unsafe identifier: {value}")
    return value


_PREPARED_TABLES: set[tuple] = set()


class Collection:
    def __init__(self, entity: type[Entity], database, *, signals: SignalBus | None = None):
        self.entity, self.database, self.signals = entity, database, signals
        _identifier(entity.__table__)

    def _event(self, suffix: str) -> str:
        return f"{self.entity.__name__.lower()}.{suffix}"

    def _prepared_key(self) -> tuple:
        config = normalize_config(self.database)
        return (self.entity.__table__, tuple(sorted((key, str(value)) for key, value in config.items())))

    def prepare(self) -> None:
        """Ensure the entity's table exists.

        Every ``fetch``/``total``/``revise``/``erase``/``append`` call goes
        through here, so the "already prepared" case -- true for nearly
        every call after the first -- must not pay for a second connection
        and a ``CREATE TABLE IF NOT EXISTS`` round trip. Cached per
        (table, database) for the life of the process instead: a table's
        schema doesn't change without a migration, which is a separate,
        deliberate operation this cache has no effect on.
        """
        key = self._prepared_key()
        if key in _PREPARED_TABLES:
            return
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
        _PREPARED_TABLES.add(key)

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
            if self.signals is not None:
                self.signals.publish(self._event("created"), data={"id": entity.id})
            return entity
        finally:
            connection.close()

    def seek(self, **criteria) -> Selection:
        return Selection(self).match(**criteria)

    def browse(self) -> Selection:
        return Selection(self)

    def total(self) -> int:
        return self.browse().total()


class Selection:
    def __init__(self, collection: Collection, clauses=(), ordering=(), limit: int | None = None, offset: int = 0, search_clause: tuple[tuple[str, ...], str] | None = None):
        self.collection, self.clauses, self.ordering, self.limit, self.offset, self._search_clause = collection, tuple(clauses), tuple(ordering), limit, offset, search_clause

    def _copy(self, **changes) -> Selection:
        return Selection(
            self.collection,
            changes.get("clauses", self.clauses),
            changes.get("ordering", self.ordering),
            changes.get("limit", self.limit),
            changes.get("offset", self.offset),
            changes.get("search_clause", self._search_clause),
        )

    def match(self, **criteria) -> Selection:
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

    def search(self, *fields: str, query: str) -> Selection:
        """OR a `LIKE` match across `fields` -- a narrow, single-purpose
        capability for admin's `search_fields`, not a general `Q()`/OR
        composition system. Combines with `match()`'s clauses via AND
        (`seek(published=True).search("title", "body", query="rust")`
        means *both*). An empty `query` is a no-op, so a blank search
        box doesn't filter anything out.
        """
        for field in fields:
            _identifier(field)
            if field not in self.collection.entity._columns:
                raise EntityError(f"Unknown column: {field}")
        if not query:
            return self
        return self._copy(search_clause=(fields, query))

    def arrange(self, *fields: str) -> Selection:
        ordering = []
        for field in fields:
            direction, name = ("DESC", field[1:]) if field.startswith("-") else ("ASC", field)
            _identifier(name)
            if name not in self.collection.entity._columns:
                raise EntityError(f"Unknown column: {name}")
            ordering.append((name, direction))
        return self._copy(ordering=ordering)

    def window(self, count: int, skip: int = 0) -> Selection:
        if count < 1 or skip < 0:
            raise EntityError("window count must be positive and skip cannot be negative")
        return self._copy(limit=count, offset=skip)

    def _where(self) -> tuple[str, tuple]:
        """Build just the `WHERE ...` clause (text + bind values, no
        `ORDER BY`/`LIMIT`) -- shared by `_sql()` (which appends those)
        and `revise()`/`erase()` (which never need them)."""
        conditions = [f"{_identifier(name)} {operator} ?" for name, operator, _ in self.clauses]
        values: list[Any] = [value for _, _, value in self.clauses]
        if self._search_clause is not None:
            search_fields, search_query = self._search_clause
            conditions.append("(" + " OR ".join(f"{_identifier(field)} LIKE ?" for field in search_fields) + ")")
            values.extend(f"%{search_query}%" for _ in search_fields)
        if not conditions:
            return "", ()
        return " WHERE " + " AND ".join(conditions), tuple(values)

    def _sql(self, fields: str = "*") -> tuple[str, tuple]:
        table = _identifier(self.collection.entity.__table__)
        where_sql, where_values = self._where()
        query, values = f"SELECT {fields} FROM {table}{where_sql}", list(where_values)
        if self.ordering:
            query += " ORDER BY " + ", ".join(f"{_identifier(name)} {direction}" for name, direction in self.ordering)
        if self.limit is not None:
            query += " LIMIT ? OFFSET ?"
            values.extend((self.limit, self.offset))
        return query, tuple(values)

    def fetch(self) -> list[Entity]:
        self.collection.prepare()
        connection = connect_readonly(self.collection.database)
        try:
            rows = connection.execute(*self._sql()).fetchall()
            return [self.collection.entity(**dict(row)) for row in rows]
        finally:
            connection.close()

    def values(self, *fields: str) -> list[dict]:
        """Fetch rows as plain ``dict``s instead of ``Entity`` instances.

        `fetch()` runs every column through its `Column.coerce()` and
        builds a real `Entity` object per row -- the right default when the
        caller wants entity behavior (further selection, `.revise()`, type
        coercion). Read-only rendering (a list page, a JSON API response)
        often immediately discards that object for a plain dict of a few
        fields, paying for hydration it never uses. `values()` skips it:
        pass specific column names to select only those, or none for every
        column.
        """
        for field in fields:
            _identifier(field)
            if field not in self.collection.entity._columns:
                raise EntityError(f"Unknown column: {field}")
        self.collection.prepare()
        connection = connect_readonly(self.collection.database)
        try:
            columns = ", ".join(_identifier(field) for field in fields) if fields else "*"
            rows = connection.execute(*self._sql(columns)).fetchall()
            return [dict(row) for row in rows]
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
        connection = connect_readonly(self.collection.database)
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
            where_sql, where_values = self._where()
            query = f"UPDATE {_identifier(self.collection.entity.__table__)} SET " + ", ".join(f"{_identifier(name)} = ?" for name in values) + where_sql
            cursor = connection.execute(query, tuple(values.values()) + where_values)
            connection.commit()
            if self.collection.signals is not None and cursor.rowcount:
                # Field *names* set, not a true before/after diff -- a
                # per-row fetch to compute one would cost an extra
                # SELECT for every revise() call, for a Selection that
                # may touch many rows at once. admin.py can afford a
                # real diff because it already has one record loaded
                # for its edit form; this is the general-purpose path.
                self.collection.signals.publish(self.collection._event("updated"), data={"fields": sorted(values), "count": cursor.rowcount})
            return cursor.rowcount
        finally:
            connection.close()

    def erase(self) -> int:
        self.collection.prepare()
        connection = connect(self.collection.database)
        try:
            where_sql, where_values = self._where()
            query = f"DELETE FROM {_identifier(self.collection.entity.__table__)}{where_sql}"
            cursor = connection.execute(query, where_values)
            connection.commit()
            if self.collection.signals is not None and cursor.rowcount:
                self.collection.signals.publish(self.collection._event("deleted"), data={"count": cursor.rowcount})
            return cursor.rowcount
        finally:
            connection.close()
