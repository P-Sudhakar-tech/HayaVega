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
