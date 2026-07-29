"""Small, safe HTML template renderer for server-rendered views."""
from __future__ import annotations

from html import escape
from pathlib import Path
import re


class TemplateNotFound(FileNotFoundError):
    pass


_VARIABLE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


def render_template(template_name: str, context: dict | None = None, *, directories=()) -> str:
    """Render ``{{ name }}`` variables with HTML escaping and no code execution."""
    if Path(template_name).is_absolute() or ".." in Path(template_name).parts:
        raise ValueError("Template paths must stay inside a template directory.")
    for directory in map(Path, directories):
        candidate = directory / template_name
        if candidate.is_file():
            source = candidate.read_text(encoding="utf-8")
            values = context or {}
            return _VARIABLE.sub(lambda match: escape(str(values.get(match.group(1), ""))), source)
    raise TemplateNotFound(template_name)
