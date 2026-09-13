"""Schema-driven API presentation backed by msgspec JSON encoding."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import msgspec


class SerializationError(ValueError):
    pass


def _plain(value: Any) -> Any:
    if hasattr(value, "as_dict"):
        return {key: _plain(item) for key, item in value.as_dict().items()}
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


class Serializer:
    """Validate external data and present entities without coupling to transport code."""

    fields: dict[str, type | tuple[type, ...]] = {}

    def __init__(self, fields: dict[str, type | tuple[type, ...]] | None = None):
        if fields is not None:
            self.fields = fields

    def present(self, value: Any) -> dict[str, Any]:
        data = _plain(value)
        if not isinstance(data, dict):
            raise SerializationError("Serializer expects an entity or mapping.")
        return {name: data.get(name) for name in self.fields} if self.fields else data

    def present_many(self, values) -> list[dict[str, Any]]:
        return [self.present(value) for value in values]

    def accept(self, payload: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise SerializationError("Payload must be a mapping.")
        output, errors = {}, {}
        for name, expected in self.fields.items():
            if name not in payload:
                if not partial:
                    errors[name] = "This value is required."
                continue
            value = payload[name]
            if value is not None and not isinstance(value, expected):
                errors[name] = f"Expected {getattr(expected, '__name__', str(expected))}."
            else:
                output[name] = value
        unexpected = set(payload) - set(self.fields)
        if unexpected:
            errors["_fields"] = f"Unknown values: {', '.join(sorted(unexpected))}."
        if errors:
            raise SerializationError(errors)
        return output

    def json(self, value: Any) -> str:
        data = self.present(value)
        return msgspec.json.encode(data).decode("utf-8")


class EntitySerializer(Serializer):
    def __init__(self, entity, fields: tuple[str, ...] | None = None):
        names = fields or tuple(entity._columns)
        super().__init__({name: object for name in names})

    def accept(self, payload: Mapping[str, Any], *, partial: bool = False) -> dict[str, Any]:
        # Entity fields perform their own type coercion on construction/revision.
        if not isinstance(payload, Mapping):
            raise SerializationError("Payload must be a mapping.")
        unknown = set(payload) - set(self.fields)
        if unknown:
            raise SerializationError({"_fields": f"Unknown values: {', '.join(sorted(unknown))}."})
        return dict(payload)
