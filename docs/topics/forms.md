# Forms

A small, declarative forms layer: field validation plus HTML
rendering, the input-side counterpart to `Entity`/`Column` on the
storage side.

## Defining a form

```python
from mangoo.forms import BooleanField, CharField, EmailField, Form


class SignupForm(Form):
    name = CharField(max_length=150)
    email = EmailField()
    subscribed = BooleanField()
```

## Validating a submission

```python
def signup(request):
    form = SignupForm(request.form(), request=request)
    if not form.is_valid():
        return Response(f"<form method='post'>{form.render()}</form>", status=400)
    name = form.cleaned_data["name"]
    ...
```

`Form(data)` is **unbound** (never valid, nothing to clean) when `data`
is omitted -- use that for the initial "show the empty form" GET.
`Form(data, request=request)` is bound; `is_valid()` cleans every
field, populating `cleaned_data` on success or `errors` (one message
per invalid field, keyed by field name) otherwise.

## Rendering

`form.render()` returns the `<input>`/`<label>`/error-message markup
for every field (not the `<form>` tag itself -- wrap it yourself so you
control the action/method). Passing `request=` to `Form(...)` adds a
hidden `csrf_token` field automatically (`security.csrf_token`), so a
bound form posted back through `@csrf_protect` just works.

## Built-in fields

| Field | Python type | Notes |
|---|---|---|
| `CharField` | `str` | `max_length=` optional |
| `EmailField` | `str` | `CharField` plus an email-format check |
| `IntegerField` | `int` | |
| `DecimalField` | `float` | |
| `BooleanField` | `bool` | Not required by default -- an unchecked checkbox submits nothing at all |

Every field accepts `required=`, `label=`, `initial=`.

## Writing your own field

Override `_convert(raw)` -- it's only called for a non-empty submitted
value; `Field.clean()` already handles the required/empty case:

```python
from mangoo.forms import Field, FieldError


class SlugField(Field):
    def _convert(self, raw):
        if not raw.replace("-", "").isalnum():
            raise FieldError("Enter a valid slug (letters, numbers, hyphens).")
        return raw.lower()
```

## `entity_form`: the `ModelForm` equivalent

For simple CRUD, generate a `Form` from an `Entity`'s declared columns
instead of hand-declaring every field:

```python
from mangoo import Entity, Integer, Text, Toggle
from mangoo.forms import entity_form


class Product(Entity):
    name = Text(unique=True)
    stock = Integer(default=0)
    published = Toggle(default=False)


ProductForm = entity_form(Product)  # name, stock, published fields
ProductForm(request.form(), request=request)
```

A column with a `default` (or `nullable=True`) becomes a non-required
field; everything else is required. Pass `fields=("name",)` to include
only specific columns. `entity_form` builds the field types
automatically (`Toggle` &rarr; `BooleanField`, `Integer`/`Decimal`
&rarr; their matching numeric fields, everything else &rarr;
`CharField`) -- the same column-to-field mapping `admin.py`'s own CRUD
forms already use internally, generalized into something your own
views can call directly.
