# Admin

Register any `Entity` and get a paginated list/add/edit/delete UI, with
validation, permissions, and change history, for a few lines of
registration code.

## The built-in admin

```python
from mangoo.admin import admin_routes

urlpatterns = [
    *admin_routes(DATABASE),
]
```

This alone gives you `/admin` and `/admin/users` &mdash; a built-in user
management screen, gated and audited.

## Registering your own models

```python
from mangoo.admin import admin_routes, ModelAdmin
from .models import Product

urlpatterns = [
    *admin_routes(
        DATABASE,
        models=[
            ModelAdmin(Product, page_size=50, can_access=lambda user: user.is_staff),
        ],
    ),
]
```

`ModelAdmin` fields:

| Field | Default | Meaning |
|---|---|---|
| `entity` | required | The `Entity` subclass to register |
| `name` | the entity's class name, lowercased | URL slug |
| `list_fields` | every declared column | Which columns the list view shows |
| `page_size` | `25` | Rows per page |
| `can_access` | `user.is_superuser` | `predicate(user) -> bool` gate, same mechanism as `superuser_required` |
| `list_filter` | `()` | Column names to add an exact-match dropdown for |
| `search_fields` | `()` | Column names a search box ORs a substring match across |
| `actions` | `{}` | Named bulk operations: `{"name": callable(list[Entity])}` |

`list_filter`/`search_fields` entries are validated against the
entity's declared columns at `ModelAdmin(...)` construction time --
typo a column name and it fails immediately, not on the first request
that hits it.

## Filtering, searching, and bulk actions

```python
from mangoo.admin import ModelAdmin
from .models import Post


def archive(records):
    for record in records:
        Post.collection(DATABASE).seek(id__exact=record.id).revise(archived=True)


ModelAdmin(
    Post,
    list_filter=("published",),
    search_fields=("title", "body"),
    actions={"archive": archive},
)
```

- **`list_filter`** renders one `<select>` per named column on the list
  view, its options populated from the distinct values actually
  present in the table -- not a fixed, hardcoded choice list. Combines
  with `search_fields` via AND.
- **`search_fields`** adds a search box (`?q=...`) that ORs a
  substring match (`Selection.search`) across the named columns. This
  is intentionally narrow -- not a general `Q()`/OR-expression system
  -- scoped to exactly this one admin feature.
- **`actions`** adds a checkbox per row and an action dropdown. Each
  action is a plain `callable(list[Entity])` -- no special base class
  -- run against every record whose checkbox was selected when the
  list view's own form is submitted. An unregistered action name, or
  no rows selected, is a no-op (redirects back to the list unchanged).

## What you get for free

- **Pagination** &mdash; `?page=N`, sized by `page_size`; an out-of-range
  page clamps to the last page and a non-numeric page falls back to page
  1, rather than erroring.
- **Validation** &mdash; a blank required field or a type-coercion failure
  is a field-level form error (400, form re-rendered with the message),
  never a raw database constraint violation reaching the browser.
- **CSRF** on every add/edit/delete submission &mdash; the same
  `csrf_protect` mechanism used elsewhere, extended to accept a
  `csrf_token` form field for plain HTML `<form>` submissions.
- **Change history** &mdash; the edit page lists every create/update/delete
  recorded against that record, sourced from the audit log; updates
  record which *field names* changed, never the values, matching the
  audit log's no-sensitive-values convention throughout the framework.

## See it end-to-end

`tests/test_admin_models.py` exercises the full stack against a real WSGI
request/response cycle, including a cookie-authenticated session with a
correct CSRF token succeeding and an incorrect one being rejected with
403 &mdash; the same test shape you'd write for your own registered model.
