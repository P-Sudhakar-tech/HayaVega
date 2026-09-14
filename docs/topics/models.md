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
| `Timestamp` | `str` (ISO 8601) | Accepts a `datetime` and stores its `.isoformat()` |
| `Key` | `int` | The implicit `id` column; rarely declared directly |

Every column accepts `default=`, `nullable=`, `unique=`.

## Migrations

```bash
python manage.py newmigration blog     # generates a checksummed migration from Product's columns
python manage.py applymigrations       # applies pending migrations, transactionally
python manage.py migrationstatus       # shows what's applied / pending
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
```

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

## Supported databases

SQLite (the default, zero-config), PostgreSQL, and MySQL/MariaDB &mdash;
all three covered by this project's own CI, including real service
containers, not mocks.

```python
DATABASES = {
    "default": {"ENGINE": "postgresql", "NAME": "myapp", "HOST": "localhost", "PORT": 5432, "USER": "myapp", "PASSWORD": "..."},
}
```
