"""Django-like framework surface for Mangoo."""

from .routing import path
from .response import Response
from .auth import User, authenticate, authenticate_session, create_session, create_superuser, create_user, enable_totp, revoke_session
from .rest import api_route
from .swagger import openapi_document
from .admin import admin_routes
from .security import csrf_protect, csrf_token, login_required, permission_required, superuser_required
from .hardening import HardeningOptions
from .static import static_routes
from .templates import render_template
from .testing import TestClient
from .response import json_response
from .collections import MangoList, mango_list
from .models import Decimal, Entity, Integer, Key, Text, Timestamp, Toggle
from .serializers import EntitySerializer, Serializer

__all__ = ["Decimal", "Entity", "EntitySerializer", "HardeningOptions", "Integer", "Key", "MangoList", "Response", "Serializer", "TestClient", "Text", "Timestamp", "Toggle", "User", "admin_routes", "api_route", "authenticate", "authenticate_session", "create_session", "create_superuser", "create_user", "csrf_protect", "csrf_token", "enable_totp", "json_response", "login_required", "mango_list", "openapi_document", "path", "permission_required", "render_template", "revoke_session", "static_routes", "superuser_required"]
