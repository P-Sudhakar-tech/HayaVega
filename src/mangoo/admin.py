"""Small secure admin base for inspecting Mangoo users."""
from __future__ import annotations

from html import escape

from .db import connect
from .response import Response
from .routing import path
from .security import superuser_required
from .security import request_user
from .audit import record_audit_event


def admin_routes(database_path, prefix: str = "admin"):
    """Return administrator-only index and user-list routes.

    Applications can extend this base with their own protected management views.
    """
    prefix = prefix.strip("/") or "admin"

    @superuser_required(database_path)
    def index(request):
        user = request_user(request, database_path)
        record_audit_event(database_path, "admin.index_viewed", actor_id=user.id if user else None)
        return Response(f"<h1>Mangoo administration</h1><p><a href='/{prefix}/users'>Users</a></p>")

    @superuser_required(database_path)
    def users(request):
        user = request_user(request, database_path)
        record_audit_event(database_path, "admin.users_viewed", actor_id=user.id if user else None)
        connection = connect(database_path)
        try:
            rows = connection.execute("SELECT id, username, email, is_superuser, is_active FROM mangoo_users ORDER BY id").fetchall()
        finally:
            connection.close()
        items = "".join(
            f"<tr><td>{row['id']}</td><td>{escape(row['username'])}</td><td>{escape(row['email'])}</td><td>{'yes' if row['is_superuser'] else 'no'}</td><td>{'active' if row['is_active'] else 'inactive'}</td></tr>"
            for row in rows
        )
        return Response(f"<h1>Users</h1><table><thead><tr><th>ID</th><th>Username</th><th>Email</th><th>Admin</th><th>Status</th></tr></thead><tbody>{items}</tbody></table>")

    return [path(prefix, index), path(f"{prefix}/users", users)]
