"""A small secure admin: a built-in user list, plus opt-in CRUD for registered models."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

from .audit import audit_events, record_audit_event
from .db import connect
from .models import Decimal, Integer, Toggle
from .response import Response
from .routing import path
from .security import CSRF_COOKIE_NAME, cookie, csrf_protect, csrf_token, permission_required, request_user, superuser_required

if TYPE_CHECKING:
    from .addons.signals import SignalBus
    from .auth import User
    from .models import Entity


@dataclass
class ModelAdmin:
    """Registers an ``Entity`` for admin list/add/edit/delete views.

    ``name`` sets the URL slug and defaults to the entity's class name,
    lowercased. ``list_fields`` selects which columns the list view shows
    and defaults to every declared column. ``can_access`` is a
    ``predicate(user) -> bool`` gate, checked the same way
    ``superuser_required`` is; it defaults to requiring ``user.is_superuser``.
    """

    entity: type[Entity]
    name: str | None = None
    list_fields: tuple[str, ...] | None = None
    page_size: int = 25
    can_access: Callable[[User], bool] | None = None

    def __post_init__(self) -> None:
        if self.page_size < 1:
            raise ValueError("page_size must be at least 1.")

    @property
    def slug(self) -> str:
        return self.name or self.entity.__name__.lower()

    @property
    def fields(self) -> tuple[str, ...]:
        return self.list_fields or tuple(name for name in self.entity._columns if name != "id")


def admin_routes(database_path, prefix: str = "admin", *, signals: SignalBus | None = None, pulse: SignalBus | None = None, models: list[ModelAdmin] | None = None):
    """Return administrator-only index, user-list, and registered-model CRUD routes.

    Applications can extend this base with their own protected management views.
    ``signals``, when given a ``SignalBus``, publishes ``admin.action`` for each
    view, distinguished by ``data["action"]``. ``pulse`` is a deprecated alias
    for ``signals``. Pass ``models=[ModelAdmin(...)]``
    to add paginated list/add/edit/delete views -- with validation, a
    per-model permission check, and change history built on the audit log --
    for your own entities.
    """
    if pulse is not None:
        warnings.warn("`pulse=` is deprecated; use `signals=` instead.", DeprecationWarning, stacklevel=2)
        signals = pulse if signals is None else signals
    prefix = prefix.strip("/") or "admin"

    @superuser_required(database_path)
    def index(request):
        user = request_user(request, database_path)
        record_audit_event(database_path, "admin.index_viewed", actor_id=user.id if user else None)
        if signals is not None:
            signals.publish("admin.action", actor_id=user.id if user else None, data={"action": "index_viewed"})
        links = "".join(f"<li><a href='/{prefix}/{model.slug}'>{escape(model.slug)}</a></li>" for model in (models or ()))
        return Response(f"<h1>Mangoo administration</h1><ul><li><a href='/{prefix}/users'>Users</a></li>{links}</ul>")

    @superuser_required(database_path)
    def users(request):
        user = request_user(request, database_path)
        record_audit_event(database_path, "admin.users_viewed", actor_id=user.id if user else None)
        if signals is not None:
            signals.publish("admin.action", actor_id=user.id if user else None, data={"action": "users_viewed"})
        connection = connect(database_path)
        try:
            rows = connection.execute("SELECT id, username, email, is_superuser, is_active FROM mangoo_users ORDER BY id").fetchall()
        finally:
            connection.close()
        items = "".join(f"<tr><td>{row['id']}</td><td>{escape(row['username'])}</td><td>{escape(row['email'])}</td><td>{'yes' if row['is_superuser'] else 'no'}</td><td>{'active' if row['is_active'] else 'inactive'}</td></tr>" for row in rows)
        return Response(f"<h1>Users</h1><table><thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Admin</th><th>Status</th></tr></thead><tbody>{items}</tbody></table>")

    routes = [path(prefix, index), path(f"{prefix}/users", users)]
    for model in models or ():
        routes.extend(_model_admin_routes(database_path, prefix, model, signals))
    return routes


# ---------------------------------------------------------------------------
# Registered-model CRUD: list (paginated), add, edit, delete, change history.
# ---------------------------------------------------------------------------


def _model_admin_routes(database_path, prefix: str, model: ModelAdmin, signals: SignalBus | None):
    slug = model.slug
    base = f"{prefix}/{slug}"
    gate = permission_required(database_path, model.can_access or (lambda user: user.is_superuser), message="Administrator permission required.")

    def _publish(action: str, actor_id, **data) -> None:
        record_audit_event(database_path, f"admin.{slug}.{action}", actor_id=actor_id, metadata={**data})
        if signals is not None:
            signals.publish("admin.action", actor_id=actor_id, data={"action": f"{slug}.{action}", **data})

    @gate
    def list_view(request):
        user = request_user(request, database_path)
        page = _parse_page(request)
        collection = model.entity.collection(database_path)
        total = collection.total()
        page_count = max(1, -(-total // model.page_size))
        page = min(page, page_count)
        rows = collection.browse().arrange("id").window(model.page_size, (page - 1) * model.page_size).fetch()
        _publish("list_viewed", user.id if user else None)
        return Response(_render_list(prefix, slug, model.fields, rows, page=page, page_count=page_count, total=total))

    @gate
    @csrf_protect
    def new_view(request):
        user = request_user(request, database_path)
        token = csrf_token(request)
        if request.method == "GET":
            body = _render_form(prefix, slug, model.entity, model.fields, {}, {}, csrf=token)
            return Response(body, headers=(cookie(CSRF_COOKIE_NAME, token, same_site="Strict"),))
        submitted = request.form()
        values, errors = _coerce_form(model.entity, model.fields, submitted)
        if errors:
            body = _render_form(prefix, slug, model.entity, model.fields, submitted, errors, csrf=token)
            return Response(body, status=400, headers=(cookie(CSRF_COOKIE_NAME, token, same_site="Strict"),))
        record = model.entity.collection(database_path).append(**values)
        _publish("created", user.id if user else None, id=record.id)
        return _redirect(f"/{base}/{record.id}")

    @gate
    @csrf_protect
    def detail_view(request):
        remainder = request.path.strip("/").removeprefix(base).lstrip("/")
        parts = remainder.split("/") if remainder else []
        try:
            record_id = int(parts[0])
        except (IndexError, ValueError):
            return Response("<h1>404 Not Found</h1>", status=404)
        if len(parts) == 2 and parts[1] == "delete":
            return _handle_delete(request, database_path, model, base, record_id, _publish)
        if len(parts) != 1:
            return Response("<h1>404 Not Found</h1>", status=404)
        return _handle_detail(request, database_path, model, prefix, slug, base, record_id, _publish)

    return [
        path(base, list_view),
        path(f"{base}/new", new_view, methods=("GET", "POST")),
        path(f"{base}/<path>", detail_view, methods=("GET", "POST")),
    ]


def _handle_delete(request, database_path, model: ModelAdmin, base: str, record_id: int, publish) -> Response:
    if request.method != "POST":
        return Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", "POST"),))
    user = request_user(request, database_path)
    deleted = model.entity.collection(database_path).seek(id__exact=record_id).erase()
    if deleted:
        publish("deleted", user.id if user else None, id=record_id)
    return _redirect(f"/{base}")


def _handle_detail(request, database_path, model: ModelAdmin, prefix: str, slug: str, base: str, record_id: int, publish) -> Response:
    record = model.entity.collection(database_path).seek(id__exact=record_id).first()
    if record is None:
        return Response("<h1>404 Not Found</h1>", status=404)
    user = request_user(request, database_path)
    token = csrf_token(request)
    if request.method == "GET":
        history = _record_history(database_path, slug, record_id)
        body = _render_form(prefix, slug, model.entity, model.fields, record.as_dict(), {}, csrf=token, record_id=record_id, history=history)
        return Response(body, headers=(cookie(CSRF_COOKIE_NAME, token, same_site="Strict"),))
    submitted = request.form()
    values, errors = _coerce_form(model.entity, model.fields, submitted)
    if errors:
        body = _render_form(prefix, slug, model.entity, model.fields, submitted, errors, csrf=token, record_id=record_id)
        return Response(body, status=400, headers=(cookie(CSRF_COOKIE_NAME, token, same_site="Strict"),))
    before = record.as_dict()
    changed = sorted(name for name, value in values.items() if before.get(name) != value)
    if changed:
        model.entity.collection(database_path).seek(id__exact=record_id).revise(**values)
        publish("updated", user.id if user else None, id=record_id, fields=changed)
    return _redirect(f"/{base}/{record_id}")


def _coerce_form(entity: type[Entity], fields: tuple[str, ...], submitted: dict) -> tuple[dict, dict]:
    """Coerce submitted form strings into column values; collect field errors instead of raising."""
    from .models import EntityError

    values: dict = {}
    errors: dict = {}
    for name in fields:
        column = entity._columns[name]
        if isinstance(column, Toggle):
            values[name] = name in submitted
            continue
        raw = submitted.get(name, "")
        if raw == "":
            if not column.nullable and column.default is None:
                errors[name] = "This field is required."
            continue
        try:
            values[name] = column.coerce(raw)
        except (EntityError, ValueError, TypeError):
            errors[name] = "Enter a valid value."
    return values, errors


def _record_history(database_path, slug: str, record_id: int, *, limit: int = 500) -> list[dict]:
    """Best-effort change history for one record from the audit log.

    Audit metadata is an opaque JSON blob, not indexed columns, so this
    filters the most recent ``limit`` audit events in Python. Fine for a
    small admin site; not a substitute for a dedicated history table under
    heavy audit-log volume.
    """
    event_prefix = f"admin.{slug}."
    return [event for event in audit_events(database_path, limit=limit) if event["event_type"].startswith(event_prefix) and not event["event_type"].endswith(".list_viewed") and event["metadata"].get("id") == record_id]


def _parse_page(request) -> int:
    raw = request.query_params.get("page", ["1"])[0]
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _redirect(location: str) -> Response:
    return Response("", status=303, headers=(("Location", location),))


# ---------------------------------------------------------------------------
# HTML rendering (escaped; no template dependency for this small internal UI).
# ---------------------------------------------------------------------------


def _render_list(prefix: str, slug: str, fields: tuple[str, ...], rows: list, *, page: int, page_count: int, total: int) -> str:
    header = "".join(f"<th>{escape(name)}</th>" for name in fields) + "<th></th>"
    body_rows = []
    for record in rows:
        values = record.as_dict()
        cells = "".join(f"<td>{escape(str(values.get(name, '')))}</td>" for name in fields)
        body_rows.append(f"<tr>{cells}<td><a href='/{prefix}/{slug}/{values['id']}'>Edit</a></td></tr>")
    nav = []
    if page > 1:
        nav.append(f"<a href='/{prefix}/{slug}?page={page - 1}'>Previous</a>")
    if page < page_count:
        nav.append(f"<a href='/{prefix}/{slug}?page={page + 1}'>Next</a>")
    return f"<h1>{escape(slug)}</h1><p><a href='/{prefix}/{slug}/new'>Add new</a></p><table><thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody></table><p>Page {page} of {page_count} ({total} total) {' | '.join(nav)}</p>"


def _render_field(name: str, column, value, error: str | None) -> str:
    label = escape(name)
    required = "" if column.nullable or column.default is not None else " required"
    if isinstance(column, Toggle):
        checked = " checked" if value else ""
        control = f"<input type='checkbox' name='{escape(name)}'{checked}>"
    else:
        input_type = "number" if isinstance(column, (Integer, Decimal)) else "text"
        display = "" if value is None else str(value)
        control = f"<input type='{input_type}' name='{escape(name)}' value='{escape(display)}'{required}>"
    error_html = f"<span class='error'>{escape(error)}</span>" if error else ""
    return f"<p><label>{label}<br>{control}</label>{error_html}</p>"


def _render_history(history: list[dict]) -> str:
    if not history:
        return "<p>No changes recorded yet.</p>"
    items = "".join(f"<li>{escape(str(event['created_at']))} -- {escape(event['event_type'])}" + (f" ({escape(', '.join(event['metadata']['fields']))})" if event["metadata"].get("fields") else "") + "</li>" for event in history)
    return f"<ul>{items}</ul>"


def _render_form(prefix: str, slug: str, entity: type[Entity], fields: tuple[str, ...], values: dict, errors: dict, *, csrf: str, record_id: int | None = None, history: list[dict] | None = None) -> str:
    action = f"/{prefix}/{slug}/{record_id}" if record_id is not None else f"/{prefix}/{slug}/new"
    inputs = "".join(_render_field(name, entity._columns[name], values.get(name), errors.get(name)) for name in fields)
    heading = f"Edit {escape(slug)} #{record_id}" if record_id is not None else f"Add {escape(slug)}"
    form = f"<h1>{heading}</h1><form method='post' action='{action}'><input type='hidden' name='csrf_token' value='{escape(csrf)}'>{inputs}<button type='submit'>Save</button></form>"
    if record_id is not None:
        form += f"<form method='post' action='/{prefix}/{slug}/{record_id}/delete'><input type='hidden' name='csrf_token' value='{escape(csrf)}'><button type='submit'>Delete</button></form><h2>History</h2>{_render_history(history or [])}"
    return form
