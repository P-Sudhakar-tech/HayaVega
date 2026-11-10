"""A small, declarative forms layer -- field validation plus HTML rendering.

`Field`/`Form` are the input-side counterpart to `models.Column`/`Entity`:
not a database column, just one submitted value cleaned into a Python
value, or a validation error collected for display. `entity_form()`
generalizes the per-field coercion `admin.py`'s own hand-rolled CRUD
forms already do into a reusable, public primitive -- `admin.py` keeps
its own implementation for now (a later consolidation is a natural
fast-follow, not required here).
"""

from __future__ import annotations

import re
from collections import OrderedDict
from html import escape
from typing import Any, ClassVar, cast

from .security import csrf_token

_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class FieldError(ValueError):
    """Raised by a `Field.clean()` when the submitted value is invalid."""


class Field:
    """Base field: validates and converts one submitted string value.

    Subclasses override `_convert(raw)` (called only when a non-empty
    value was submitted) and, optionally, `widget()` to change how the
    field renders.
    """

    def __init__(self, *, required: bool = True, label: str | None = None, initial: Any = None):
        self.required = required
        self.label = label
        self.initial = initial
        self.name = ""

    def __set_name__(self, owner, name):
        self.name = name

    def clean(self, raw: str) -> Any:
        if raw == "" or raw is None:
            if self.required:
                raise FieldError("This field is required.")
            return None
        return self._convert(raw)

    def _convert(self, raw: str) -> Any:
        return raw

    def widget(self, name: str, value: Any) -> str:
        display = "" if value is None else str(value)
        required = " required" if self.required else ""
        return f"<input type='text' name='{escape(name)}' value='{escape(display)}'{required}>"


class CharField(Field):
    def __init__(self, *, max_length: int | None = None, **kwargs):
        super().__init__(**kwargs)
        self.max_length = max_length

    def _convert(self, raw: str) -> str:
        if self.max_length is not None and len(raw) > self.max_length:
            raise FieldError(f"Ensure this value has at most {self.max_length} characters.")
        return raw


class EmailField(CharField):
    def _convert(self, raw: str) -> str:
        raw = super()._convert(raw)
        if not _EMAIL_PATTERN.fullmatch(raw):
            raise FieldError("Enter a valid email address.")
        return raw

    def widget(self, name: str, value: Any) -> str:
        display = "" if value is None else str(value)
        required = " required" if self.required else ""
        return f"<input type='email' name='{escape(name)}' value='{escape(display)}'{required}>"


class IntegerField(Field):
    def _convert(self, raw: str) -> int:
        try:
            return int(raw)
        except ValueError:
            raise FieldError("Enter a valid integer.") from None

    def widget(self, name: str, value: Any) -> str:
        display = "" if value is None else str(value)
        required = " required" if self.required else ""
        return f"<input type='number' name='{escape(name)}' value='{escape(display)}'{required}>"


class DecimalField(Field):
    def _convert(self, raw: str) -> float:
        try:
            return float(raw)
        except ValueError:
            raise FieldError("Enter a valid number.") from None

    def widget(self, name: str, value: Any) -> str:
        display = "" if value is None else str(value)
        required = " required" if self.required else ""
        return f"<input type='number' step='any' name='{escape(name)}' value='{escape(display)}'{required}>"


class BooleanField(Field):
    """Checkbox semantics: unchecked submits nothing at all, so this is
    never "required" in the usual sense -- absence just means `False`."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def clean(self, raw: str) -> bool:
        return bool(raw)

    def widget(self, name: str, value: Any) -> str:
        checked = " checked" if value else ""
        return f"<input type='checkbox' name='{escape(name)}'{checked}>"


class FormMeta(type):
    def __new__(mcls, name, bases, namespace):
        fields: OrderedDict[str, Field] = OrderedDict()
        for base in bases:
            fields.update(getattr(base, "_fields", {}))
        for key, value in namespace.items():
            if isinstance(value, Field):
                value.name = key
                fields[key] = value
        cls = super().__new__(mcls, name, bases, namespace)
        cls._fields = fields
        return cls


class Form(metaclass=FormMeta):
    """A declarative form: field validation plus HTML rendering.

    Unbound (``data=None``) for a fresh "add" page; bound (``data={...}``,
    typically ``request.form()``) to validate a submission. Pass
    ``request=`` to have `render()` include a hidden CSRF field
    automatically (`security.csrf_token`) -- omit it for a form that
    doesn't need one, or where the caller renders its own token.
    """

    _fields: ClassVar[OrderedDict[str, Field]]

    def __init__(self, data: dict[str, str] | None = None, *, request=None):
        self.data = data or {}
        self.request = request
        self.is_bound = data is not None
        self.errors: dict[str, str] = {}
        self.cleaned_data: dict[str, Any] = {}

    def is_valid(self) -> bool:
        if not self.is_bound:
            return False
        self.errors = {}
        self.cleaned_data = {}
        for name, field in self._fields.items():
            try:
                self.cleaned_data[name] = field.clean(self.data.get(name, ""))
            except FieldError as error:
                self.errors[name] = str(error)
        return not self.errors

    def render(self) -> str:
        parts = []
        if self.request is not None:
            parts.append(f"<input type='hidden' name='csrf_token' value='{escape(csrf_token(self.request))}'>")
        for name, field in self._fields.items():
            value = self.cleaned_data.get(name) if name in self.cleaned_data else self.data.get(name, field.initial)
            label = escape(field.label or name)
            error_html = f"<span class='error'>{escape(self.errors[name])}</span>" if name in self.errors else ""
            parts.append(f"<p><label>{label}<br>{field.widget(name, value)}</label>{error_html}</p>")
        return "".join(parts)


_COLUMN_FIELDS = {
    "Toggle": lambda required: BooleanField(required=False),
    "Integer": lambda required: IntegerField(required=required),
    "Decimal": lambda required: DecimalField(required=required),
}


def entity_form(entity_class, fields: tuple[str, ...] | None = None) -> type[Form]:
    """Build a `Form` subclass from an `Entity`'s declared columns --
    the `ModelForm` equivalent, for simple CRUD without hand-declaring
    every field. Pass `fields=` to include only specific columns;
    defaults to every column except `id`.
    """
    names = fields or tuple(name for name in entity_class._columns if name != "id")
    namespace: dict[str, Field] = {}
    for name in names:
        column = entity_class._columns[name]
        required = not column.nullable and column.default is None
        factory = _COLUMN_FIELDS.get(type(column).__name__)
        namespace[name] = factory(required) if factory else CharField(required=required)
    return cast("type[Form]", FormMeta(f"{entity_class.__name__}Form", (Form,), namespace))
