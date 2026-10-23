# Models & migrations

Mangoo's ORM is a small, explicit `Entity`/`Collection`/`Selection` layer
&mdash; no metaclass magic beyond declaring columns, no lazy relationship
loading to reason about.

## Defining a model

```python
from mangoo import Entity, Text, Integer, Decimal, Toggle, Timestamp


class Product(Entity):
    name = Text(unique=True)
    price = Decimal()
    published = Toggle(default=False)
    stock = Integer(default=0)
    created_at = Timestamp()
```

Every `Entity` gets an implicit `id` primary key. The table name defaults
to `{classname.lower()}_entries` (`product_entries` here) &mdash; override
with `__table__ = "products"` if you need a specific name.

| Column type | Python type | Notes |
|---|---|---|
| `Text` | `str` | |
| `Integer` | `int` | |
| `Decimal` | `float` | `DOUBLE`/`DOUBLE PRECISION` depending on engine |
| `Toggle` | `bool` | `BOOLEAN`/`INTEGER` depending on engine |
| `Timestamp` | `datetime` (aware, UTC) | Accepts a naive or aware `datetime`; always reads back aware UTC -- see [Timestamps & timezones](#timestamps-timezones) |
| `Key` | `int` | The implicit `id` column; rarely declared directly |

Every column accepts `default=`, `nullable=`, `unique=`.

## Timestamps & timezones

`Timestamp` is always timezone-aware UTC -- not an opt-in project
setting (Django's `USE_TZ`), just the only behavior this column has:

```python
from datetime import datetime, timedelta, timezone

event = Event(occurred_at=datetime(2024, 1, 1, 12, 0, 0))  # naive -- assumed UTC
event.occurred_at  # datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc) -- aware, immediately

event = Event(occurred_at=datetime(2024, 1, 1, 17, 0, tzinfo=timezone(timedelta(hours=5))))
event.occurred_at  # converted to datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
```

A naive `datetime` is assumed to already be UTC; an aware one is
converted. Either way, reading the attribute back -- immediately after
construction, or after a `fetch()` round trip through the database --
always gives an aware `datetime` in UTC, never a naive one and never a
raw string.

Internally, the stored value is always a plain ISO string, not a raw
`datetime` object handed to a database driver: Python's `sqlite3`
module deprecated its implicit `datetime` adapters as of 3.12 and
removes them in 3.14, so relying on a driver to serialize a `datetime`
automatically is not a safe bet going forward. `record.as_dict()`
reflects this storage representation (a string), not the aware
`datetime` attribute access gives you -- useful as-is for JSON
serialization (`EntitySerializer`) without extra datetime-encoding
logic.

## Migrations

```bash
python mango.py newmigration blog     # generates a checksummed migration from Product's columns
python mango.py applymigrations       # applies pending migrations, transactionally
python mango.py migrationstatus       # shows what's applied / pending
```

Migrations are forward-only, checksummed (a modified or missing migration
file is detected, not silently re-applied), and transactional &mdash; a
failed migration leaves no partial schema change where the database
supports transactions. A schema-diff check rejects type/nullability/
uniqueness changes to an *existing* column with an explicit-migration
error rather than silently ignoring the mismatch.

## Querying: `Collection` and `Selection`

```python
products = Product.collection(DATABASE)

# Create
products.append(name="Notebook", price=9.5, published=True, stock=10)

# Chainable selection
featured = products.seek(published=True).arrange("-price").window(10).fetch()

# Update / delete
products.seek(name="Notebook").revise(stock=5)
products.seek(published=False).erase()

# Count
products.browse().total()

# Substring search across multiple columns, ORed together
products.browse().search("name", "description", query="rust").fetch()
```

`search(*fields, query=...)` ORs a `LIKE` match across `fields` --
narrow and single-purpose (admin's `search_fields` is built on it),
not a general `Q()`/OR-expression system. It combines with `seek()`'s
clauses via AND, and an empty `query` is a no-op. `seek()`'s own
`match()` stays AND-only across every criterion, same as always.

`.seek(**criteria)` supports operator suffixes: `field__above`,
`field__at_least`, `field__below`, `field__at_most`, `field__not`,
`field__contains` (a `LIKE` match). `.arrange("-price")` orders
descending; a bare field name orders ascending. `.window(count, skip=0)`
paginates.

## `.fetch()` vs `.values()`

```python
# Full Entity objects -- coercion, further selection, .revise()-ready
posts = collection.browse().fetch()

# Plain dicts -- skips Entity construction and per-column coercion entirely
rows = collection.browse().values("title", "views")
```

`.values()` exists specifically for the common read-only case (a list
page, a JSON API response) that never needed `Entity` behavior in the
first place &mdash; measured at a real 1.28&ndash;1.36&times; win over
`.fetch()` + manual dict extraction for exactly that shape of query (see
[Performance](../performance.md)). `.fetch()` remains completely
unchanged; `.values()` is additive, not a replacement.

## Transactions

```python
from mangoo.db import connect

connection = connect(DATABASE)
with connection.atomic():
    connection.execute("UPDATE accounts SET balance = balance - ? WHERE id = ?", (100, 1))
    connection.execute("UPDATE accounts SET balance = balance + ? WHERE id = ?", (100, 2))
```

`.atomic()` nests correctly via savepoints &mdash; a nested `atomic()`
block that raises rolls back to its own savepoint, not the whole
transaction.

## Connection pooling (opt-in, benchmark before enabling)

```python
from mangoo import db

db.enable_connection_pooling()  # reuses one connection per (thread, database)
db.enable_readonly_connection_pooling()  # reuses one *read-only* connection per thread
```

Both are measured, disclosed trade-offs, not universal wins &mdash;
`enable_connection_pooling()` is a real win for ASGI and a real
regression for WSGI on this project's own benchmark environment; see
[Performance](../performance.md) for the numbers and reasoning before
enabling either for your deployment.

## SQLite WAL mode (opt-in, a real write-path win)

```python
from mangoo import db

db.enable_wal_mode()  # PRAGMA journal_mode=WAL + synchronous=NORMAL on new connections
```

SQLite only (silently a no-op for PostgreSQL/MySQL configs) &mdash;
sets SQLite's own officially documented recommended pairing for
write-heavy workloads on every new connection `connect()` opens from
then on. Measured (`benchmarks/sqlite_wal_mode.py`): a write
transaction (two inserts, one update, one commit &mdash; the shape a
checkout/order-placement endpoint does) costs ~6.4-6.9ms under
SQLite's untouched defaults on this project's own Windows development
environment, almost entirely `fsync` overhead on commit, and
~0.15-0.17ms with WAL+NORMAL &mdash; a **~39-47x** improvement across
repeated runs. Confirmed at the HTTP level too: a sample e-commerce
app's write-heavy order-placement endpoint moved from a near-tie with
a comparable FastAPI backend (≈70-77 req/s each) to a decisive win
(166 req/s vs. 71 req/s) once enabled, reproduced across independent
runs. `fsync` cost is highly platform- and disk-dependent &mdash;
benchmark your own deployment before enabling, same as every option
above.

Real trade-offs, not a free lunch: WAL mode creates `-wal`/`-shm`
sidecar files next to the main `.sqlite3` file and doesn't work
reliably on some network filesystems (NFS, SMB/CIFS shares) &mdash;
stick to a local disk. `synchronous=NORMAL` is fully safe against an
*application* crash (every committed transaction is still there on
restart), but can lose the most recent transaction(s) specifically on
an OS crash or power loss &mdash; an accepted trade-off for most
deployments, not a universal one. `journal_mode` persists in the
database file itself, not in this flag &mdash; `db.disable_wal_mode()`
only stops *new* connections from turning it on; it does not revert a
file already in WAL mode back to its original journal mode.

## `SerializedConnection`: one connection, Rust-serialized, for a hot read path

```python
from mangoo.db import SerializedConnection

reads = SerializedConnection(DATABASE)  # SQLite, read-only
rows = reads.execute("SELECT id, title, views FROM posts WHERE published = ? ORDER BY id LIMIT ? OFFSET ?", (True, 20, 0))
# -> list[dict], already materialized -- not a cursor, unlike connect()/connect_readonly()
```

A different trade-off than the pooling options above: instead of each
thread holding (or sharing) its own connection, every call goes
through **one** connection, serialized by a dedicated Rust worker
thread when `mangoo_native` is built (a pure-Python lock-based fallback
otherwise -- same interface, no configuration needed either way).
Measured (`benchmarks/serialized_connection_vs_python.py`) at a real,
consistent **~1.5-1.7x** throughput improvement over both the unpooled
default and `enable_readonly_connection_pooling()`, for a
`SELECT ... LIMIT ? OFFSET ?`-shaped read under WSGI/waitress
contention -- the first Rust-acceleration candidate in this project to
clear a decisive win since the native WSGI server/Signals dispatcher
(see `RUST_CORE_REWRITE_ROADMAP.md` for the others that didn't).

**Benchmark your own deployment before reaching for this, same as
every option above** -- every query now queues behind whichever one is
currently running on the one connection, trading SQLite's own
per-connection lock contention for a single serialization point. A
read pattern with occasional genuinely slow queries could see those
queue up behind each other in a way independent connections wouldn't.

SQLite only, read-only only (no `.commit()`/write support -- use
`connect()`/`enable_connection_pooling()` for writes). Not integrated
into `Selection`/`Entity` -- call `.execute(sql, params)` directly with
your own raw SQL for a specific hot path, not as a general `Selection`
replacement.

## Supported databases

SQLite (the default, zero-config), PostgreSQL, and MySQL/MariaDB &mdash;
all three covered by this project's own CI, including real service
containers, not mocks.

```python
class database:
    engine = "postgresql"
    name = "myapp"
    host = "localhost"
    port = 5432
    user = "myapp"
    password = "..."
```

## Multiple databases

Mangoo's built-in helpers (`Collection`, `admin_routes`, `auth_routes`,
migrations) each address exactly one `database` dict -- there's no
Django-style named-connection registry or `.using("replica")`. A project
that talks to a second database (an analytics warehouse, a legacy
read-only source) defines its own extra named section next to `database`
and converts it with the same public helper the loader uses internally,
`mangoo.conf.load_database()`:

```python title="config/settings.py"
class Settings:
    ...

    class database:
        engine = "sqlite3"
        name = BASE_DIR / "mangoo.sqlite3"

    class databases:
        analytics = {
            "engine": "postgresql",
            "name": "warehouse",
            "host": "analytics.internal",
            "user": "warehouse_ro",
            "password": os.getenv("ANALYTICS_DB_PASSWORD"),
        }
```

```python title="apps/blog/routes.py"
from mangoo.conf import load_database, load_settings
from .settings import settings as config_settings

_settings = load_settings()
_analytics_db = load_database(config_settings.databases.analytics, label="Settings.databases.analytics")


def top_posts(request):
    return Post.collection(_analytics_db).browse().arrange("-views").window(10).values("title", "views")
```

`load_database()` does the same validation as the primary section
(`engine` must be `sqlite3`/`postgresql`/`mysql`/`mariadb`, `name` is
required, a relative SQLite `name` needs `base_dir=`) and returns the
same upper-cased dict `connect()`/`Collection`/`admin_routes`/`auth_routes`
all expect -- `label` only changes what a validation error names, so
point it at your own section (e.g. `"Settings.databases.analytics"`)
rather than the default `"database"`.
