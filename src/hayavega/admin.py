"""A small secure admin: a built-in user list, plus opt-in CRUD for registered models."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from .audit import audit_events, record_audit_event
from .db import connect
from .jobs import list_jobs, retry_job
from .models import Decimal, EntityError, Integer, Toggle
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

    ``list_filter`` adds an exact-match dropdown per named column, its
    options populated from the distinct values actually present.
    ``search_fields`` adds a search box that ORs a substring match
    across the named columns (``Selection.search``) -- both apply
    together with AND. ``actions`` registers named bulk operations
    (``{"name": callable(list[Entity])}``), offered as a dropdown next
    to a checkbox per row on the list view.
    """

    entity: type[Entity]
    name: str | None = None
    list_fields: tuple[str, ...] | None = None
    page_size: int = 25
    can_access: Callable[[User], bool] | None = None
    list_filter: tuple[str, ...] = ()
    search_fields: tuple[str, ...] = ()
    actions: dict[str, Callable[[list[Entity]], None]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.page_size < 1:
            raise ValueError("page_size must be at least 1.")
        for field_name in (*self.list_filter, *self.search_fields):
            if field_name not in self.entity._columns:
                raise ValueError(f"{field_name!r} is not a column on {self.entity.__name__}.")

    @property
    def slug(self) -> str:
        return self.name or self.entity.__name__.lower()

    @property
    def fields(self) -> tuple[str, ...]:
        return self.list_fields or tuple(name for name in self.entity._columns if name != "id")


def admin_routes(database_path, prefix: str = "admin", *, signals: SignalBus | None = None, pulse: SignalBus | None = None, models: list[ModelAdmin] | None = None, jobs: bool = False):
    """Return administrator-only index, user-list, and registered-model CRUD routes.

    Applications can extend this base with their own protected management views.
    ``signals``, when given a ``SignalBus``, publishes ``admin.action`` for each
    view, distinguished by ``data["action"]``. ``pulse`` is a deprecated alias
    for ``signals``. Pass ``models=[ModelAdmin(...)]``
    to add paginated list/add/edit/delete views -- with validation, a
    per-model permission check, and change history built on the audit log --
    for your own entities. Pass ``jobs=True`` to add a read-only, paginated
    view of the background job queue (``hayavega.jobs``), filterable by status,
    with a retry action for dead-lettered jobs.
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
        jobs_link = f"<li><a href='/{prefix}/jobs'>Jobs</a></li>" if jobs else ""
        return Response(f"<h1>Hayavega administration</h1><ul><li><a href='/{prefix}/users'>Users</a></li>{jobs_link}{links}</ul>")

    @superuser_required(database_path)
    def users(request):
        user = request_user(request, database_path)
        record_audit_event(database_path, "admin.users_viewed", actor_id=user.id if user else None)
        if signals is not None:
            signals.publish("admin.action", actor_id=user.id if user else None, data={"action": "users_viewed"})
        connection = connect(database_path)
        try:
            rows = connection.execute("SELECT id, username, email, is_superuser, is_active FROM hayavega_users ORDER BY id").fetchall()
        finally:
            connection.close()
        items = "".join(f"<tr><td>{row['id']}</td><td>{escape(row['username'])}</td><td>{escape(row['email'])}</td><td>{'yes' if row['is_superuser'] else 'no'}</td><td>{'active' if row['is_active'] else 'inactive'}</td></tr>" for row in rows)
        return Response(f"<h1>Users</h1><table><thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Admin</th><th>Status</th></tr></thead><tbody>{items}</tbody></table>")

    routes = [path(prefix, index), path(f"{prefix}/users", users)]
    if jobs:
        routes.extend(_jobs_admin_routes(database_path, prefix, signals))
    for model in models or ():
        routes.extend(_model_admin_routes(database_path, prefix, model, signals))
    return routes


# ---------------------------------------------------------------------------
# Job-queue admin: paginated list view (hayavega.jobs), filterable by status,
# plus a retry action for dead-lettered jobs.
# ---------------------------------------------------------------------------

_JOB_STATUSES = ("pending", "running", "succeeded", "dead")


def _jobs_admin_routes(database_path, prefix: str, signals: SignalBus | None):
    base = f"{prefix}/jobs"

    @superuser_required(database_path)
    def jobs_list(request):
        user = request_user(request, database_path)
        status = request.query_params.get("status", [""])[0] or None
        if status is not None and status not in _JOB_STATUSES:
            status = None
        page = _parse_page(request)
        rows, total = list_jobs(database_path, status=status, page=page, page_size=25)
        page_count = max(1, -(-total // 25))
        page = min(page, page_count)
        record_audit_event(database_path, "admin.jobs.list_viewed", actor_id=user.id if user else None)
        if signals is not None:
            signals.publish("admin.action", actor_id=user.id if user else None, data={"action": "jobs.list_viewed"})
        token = csrf_token(request)
        body = _render_jobs_list(prefix, rows, status=status, page=page, page_count=page_count, total=total, csrf=token)
        return Response(body, headers=(cookie(CSRF_COOKIE_NAME, token, same_site="Strict"),))

    @superuser_required(database_path)
    @csrf_protect
    def jobs_retry(request):
        if request.method != "POST":
            return Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", "POST"),))
        remainder = request.path.strip("/").removeprefix(base).lstrip("/")
        if not remainder.endswith("/retry"):
            return Response("<h1>404 Not Found</h1>", status=404)
        try:
            job_id = int(remainder.removesuffix("/retry"))
        except ValueError:
            return Response("<h1>404 Not Found</h1>", status=404)
        user = request_user(request, database_path)
        reset = retry_job(database_path, job_id, signals=signals)
        if reset:
            record_audit_event(database_path, "admin.jobs.retried", actor_id=user.id if user else None, metadata={"id": job_id})
            if signals is not None:
                signals.publish("admin.action", actor_id=user.id if user else None, data={"action": "jobs.retried", "id": job_id})
        return _redirect(f"/{base}")

    return [path(base, jobs_list), path(f"{base}/<path>", jobs_retry, methods=("POST",))]


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
    @csrf_protect
    def list_view(request):
        if request.method == "POST":
            return _handle_bulk_action(request, database_path, model, base, _publish)
        user = request_user(request, database_path)
        page = _parse_page(request)
        query = (request.query_params.get("q", [""])[0] or "").strip()
        active_filters = {}
        for field_name in model.list_filter:
            raw = request.query_params.get(f"filter_{field_name}", [""])[0]
            if not raw:
                continue
            try:
                active_filters[field_name] = model.entity._columns[field_name].coerce(raw)
            except (EntityError, ValueError, TypeError):
                pass  # an invalid filter value in the URL is silently ignored, not a 400
        selection = model.entity.collection(database_path).browse()
        if active_filters:
            selection = selection.match(**active_filters)
        if query and model.search_fields:
            selection = selection.search(*model.search_fields, query=query)
        total = selection.total()
        page_count = max(1, -(-total // model.page_size))
        page = min(page, page_count)
        rows = selection.arrange("id").window(model.page_size, (page - 1) * model.page_size).fetch()
        filter_options = {field_name: _distinct_values(database_path, model.entity, field_name) for field_name in model.list_filter}
        _publish("list_viewed", user.id if user else None)
        token = csrf_token(request)
        body = _render_list(
            prefix,
            slug,
            model.fields,
            rows,
            page=page,
            page_count=page_count,
            total=total,
            list_filter=model.list_filter,
            active_filters=active_filters,
            filter_options=filter_options,
            search_fields=model.search_fields,
            query=query,
            actions=model.actions,
            csrf=token,
        )
        return Response(body, headers=(cookie(CSRF_COOKIE_NAME, token, same_site="Strict"),))

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
        path(base, list_view, methods=("GET", "POST")),
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


def _distinct_values(database_path, entity: type[Entity], field_name: str) -> list:
    """Populate a `list_filter` dropdown's options. `field_name` is
    validated against `entity._columns` in `ModelAdmin.__post_init__`
    before this is ever called, and `entity.__table__` the same way by
    every `Collection` -- both safe to interpolate here."""
    connection = connect(database_path)
    try:
        rows = connection.execute(f"SELECT DISTINCT {field_name} FROM {entity.__table__} ORDER BY {field_name}").fetchall()
        return [row[field_name] for row in rows if row[field_name] is not None]
    finally:
        connection.close()


def _handle_bulk_action(request, database_path, model: ModelAdmin, base: str, publish) -> Response:
    """Handle the list view's own bulk-action POST (same URL as the
    list GET). Reads `selected` directly from the raw urlencoded body
    rather than `request.form()`, which collapses a repeated field name
    (every row's `selected` checkbox) down to its last value -- exactly
    wrong for "every checkbox the user ticked"."""
    if request.method != "POST":
        return Response("<h1>405 Method Not Allowed</h1>", status=405, headers=(("Allow", "POST"),))
    user = request_user(request, database_path)
    submitted = parse_qs(request.body.decode("utf-8"))
    action_name = (submitted.get("action", [""])[0] or "").strip()
    action = model.actions.get(action_name)
    try:
        selected_ids = [int(value) for value in submitted.get("selected", [])]
    except ValueError:
        selected_ids = []
    if action is None or not selected_ids:
        return _redirect(f"/{base}")
    collection = model.entity.collection(database_path)
    records = []
    for record_id in selected_ids:
        record = collection.seek(id__exact=record_id).first()
        if record is not None:
            records.append(record)
    if records:
        action(records)
        publish("bulk_action", user.id if user else None, name=action_name, ids=[record.id for record in records])
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


def _render_list(
    prefix: str,
    slug: str,
    fields: tuple[str, ...],
    rows: list,
    *,
    page: int,
    page_count: int,
    total: int,
    list_filter: tuple[str, ...] = (),
    active_filters: dict | None = None,
    filter_options: dict[str, list] | None = None,
    search_fields: tuple[str, ...] = (),
    query: str = "",
    actions: dict[str, Callable] | None = None,
    csrf: str = "",
) -> str:
    active_filters = active_filters or {}
    filter_options = filter_options or {}
    actions = actions or {}

    controls = []
    if search_fields:
        controls.append(f"<input type='text' name='q' value='{escape(query)}' placeholder='Search'>")
    for field_name in list_filter:
        current = active_filters.get(field_name)
        options = "".join(f"<option value='{escape(str(value))}'{' selected' if current is not None and str(current) == str(value) else ''}>{escape(str(value))}</option>" for value in filter_options.get(field_name, []))
        controls.append(f"<select name='filter_{escape(field_name)}'><option value=''>All {escape(field_name)}</option>{options}</select>")
    filter_bar = f"<form method='get'>{''.join(controls)}<button type='submit'>Filter</button></form>" if controls else ""

    header = ("<th></th>" if actions else "") + "".join(f"<th>{escape(name)}</th>" for name in fields) + "<th></th>"
    body_rows = []
    for record in rows:
        values = record.as_dict()
        checkbox = f"<td><input type='checkbox' name='selected' value='{values['id']}'></td>" if actions else ""
        cells = "".join(f"<td>{escape(str(values.get(name, '')))}</td>" for name in fields)
        body_rows.append(f"<tr>{checkbox}{cells}<td><a href='/{prefix}/{slug}/{values['id']}'>Edit</a></td></tr>")
    table = f"<table><thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
    if actions:
        action_options = "".join(f"<option value='{escape(name)}'>{escape(name)}</option>" for name in actions)
        action_bar = f"<select name='action'><option value=''>Choose an action</option>{action_options}</select><button type='submit'>Apply</button>"
        table = f"<form method='post'><input type='hidden' name='csrf_token' value='{escape(csrf)}'>{action_bar}{table}</form>"

    nav = []
    if page > 1:
        nav.append(f"<a href='/{prefix}/{slug}?page={page - 1}'>Previous</a>")
    if page < page_count:
        nav.append(f"<a href='/{prefix}/{slug}?page={page + 1}'>Next</a>")
    return f"<h1>{escape(slug)}</h1>{filter_bar}<p><a href='/{prefix}/{slug}/new'>Add new</a></p>{table}<p>Page {page} of {page_count} ({total} total) {' | '.join(nav)}</p>"


def _render_jobs_list(prefix: str, rows: list[dict], *, status: str | None, page: int, page_count: int, total: int, csrf: str) -> str:
    bold = " style='font-weight:bold'"
    filters = "".join(f"<a href='/{prefix}/jobs?status={escape(candidate)}'{bold if candidate == status else ''}>{escape(candidate)}</a>" for candidate in _JOB_STATUSES)
    filters = f"<a href='/{prefix}/jobs'{bold if status is None else ''}>all</a> | {filters}"
    body_rows = []
    for row in rows:
        error = f"<td>{escape(row['last_error'])}</td>" if row["last_error"] else "<td></td>"
        retry = f"<form method='post' action='/{prefix}/jobs/{row['id']}/retry'><input type='hidden' name='csrf_token' value='{escape(csrf)}'><button type='submit'>Retry</button></form>" if row["status"] == "dead" else ""
        body_rows.append(f"<tr><td>{row['id']}</td><td>{escape(row['name'])}</td><td>{escape(row['status'])}</td><td>{row['attempts']}/{row['max_attempts']}</td><td>{escape(str(row['run_at']))}</td>{error}<td>{escape(str(row['updated_at']))}</td><td>{retry}</td></tr>")
    nav = []
    status_suffix = f"&status={escape(status)}" if status else ""
    if page > 1:
        nav.append(f"<a href='/{prefix}/jobs?page={page - 1}{status_suffix}'>Previous</a>")
    if page < page_count:
        nav.append(f"<a href='/{prefix}/jobs?page={page + 1}{status_suffix}'>Next</a>")
    header = "<th>ID</th><th>Name</th><th>Status</th><th>Attempts</th><th>Run at</th><th>Last error</th><th>Updated</th><th></th>"
    return f"<h1>Jobs</h1><p>{filters}</p><table><thead><tr>{header}</tr></thead><tbody>{''.join(body_rows)}</tbody></table><p>Page {page} of {page_count} ({total} total) {' | '.join(nav)}</p>"


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
