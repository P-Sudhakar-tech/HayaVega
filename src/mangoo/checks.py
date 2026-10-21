"""Proactive diagnostics for a Mangoo project.

`mango diagnose` / `mango.py diagnose` reports unsafe settings, use of
deprecated (Pulse-era) APIs, and pending migrations before a developer
hits any of them at runtime -- the same category of tool Django's own
`manage.py check` provides, under a name that doesn't collide with it
(see `management/commands.py`'s `_DEPRECATED_ALIASES` for why every
Django-named command in this project got its own, distinct name).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import crypto
from .migrations import migration_directories
from .migrations import status as migration_status

# Each entry: (pattern, what it found, the actionable next step).
_DEPRECATED_PATTERNS: tuple[tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"\bmangoo\.addons\.pulse\b"), "imports from the deprecated `mangoo.addons.pulse` module", "Import from `mangoo.addons.signals` instead."),
    (re.compile(r"\bPulseBus\b"), "uses the deprecated `PulseBus` name", "Use `SignalBus` instead (`from mangoo.addons.signals import SignalBus`)."),
    (re.compile(r"\bPulseEvent\b"), "uses the deprecated `PulseEvent` name", "Use `SignalEvent` instead."),
    (re.compile(r"\bPulseSubscriber\b"), "uses the deprecated `PulseSubscriber` name", "Use `SignalSubscriber` instead."),
    (re.compile(r"\bpulse\s*="), "passes the deprecated `pulse=` keyword argument", "Use `signals=` instead."),
)

# Directories never worth scanning: version control, virtualenvs, caches,
# and migrations (generated SQL/DDL strings, not framework API usage).
_SCAN_EXCLUDE_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "migrations", "site-packages", "build", "dist"}


@dataclass(frozen=True)
class CheckMessage:
    """One diagnostic finding: a level, what was found, and what to do about it."""

    level: str  # "error", "warning", or "info"
    message: str
    hint: str = ""

    def __str__(self) -> str:
        line = f"[{self.level.upper()}] {self.message}"
        return f"{line}\n    -> {self.hint}" if self.hint else line


def _scan_deprecated_usage(base_dir: Path) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    if not base_dir.is_dir():
        return messages
    for path in sorted(base_dir.rglob("*.py")):
        if any(part in _SCAN_EXCLUDE_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern, description, hint in _DEPRECATED_PATTERNS:
            if pattern.search(text):
                try:
                    location = path.relative_to(base_dir)
                except ValueError:
                    location = path
                messages.append(CheckMessage("warning", f"{location}: {description}.", hint))
    return messages


def _settings_checks(settings) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    module_name = getattr(settings, "module_name", "your settings module")
    debug = bool(getattr(settings, "debug", False))
    if debug:
        messages.append(CheckMessage("warning", "DEBUG is enabled.", f"Set Settings.debug = False in {module_name} before deploying to production."))
    secret_key = getattr(settings, "secret_key", "")
    if not secret_key or secret_key in {"change-this-in-production", "changeme", "secret"}:
        messages.append(CheckMessage("error" if not debug else "warning", "SECRET_KEY is unset or a known placeholder value.", f"Set Settings.secret_key to a unique, environment-provided value in {module_name}."))
    allowed_hosts = getattr(settings, "allowed_hosts", ())
    if not debug and (not allowed_hosts or "*" in allowed_hosts):
        messages.append(CheckMessage("error", "ALLOWED_HOSTS is empty or allows any host.", f"Set Settings.allowed_hosts to your real domain(s) in {module_name}."))
    security = getattr(settings, "security", {})
    if not debug and not security.get("FORCE_HTTPS", False):
        messages.append(CheckMessage("warning", "SECURITY['FORCE_HTTPS'] is not enabled.", f"Set Settings.security.force_https = True in {module_name} for production."))
    if not debug and not security.get("SECURE_COOKIES", False):
        messages.append(CheckMessage("warning", "SECURITY['SECURE_COOKIES'] is not enabled.", f"Set Settings.security.secure_cookies = True in {module_name} for production."))
    return messages


def _crypto_checks() -> list[CheckMessage]:
    if crypto.available():
        return []
    return [CheckMessage("info", "`cryptography` is not installed; any `enable_totp()` secret is stored in plaintext instead of encrypted.", "Run `uv add cryptography` (or `pip install cryptography`) to encrypt TOTP secrets at rest.")]


def _pending_migration_checks(settings) -> list[CheckMessage]:
    try:
        records = migration_status(settings.database, migration_directories(settings))
    except Exception as error:
        return [CheckMessage("error", f"Could not determine migration status: {error}", "Verify Settings.database in your settings module is reachable.")]
    pending = [record.name for record in records if not record.applied]
    if not pending:
        return []
    return [CheckMessage("warning", f"{len(pending)} pending migration(s): {', '.join(pending)}.", "Run `mango.py applymigrations` to apply them.")]


def run_checks(settings) -> list[CheckMessage]:
    """Run every proactive diagnostic and return the findings, most severe first."""
    messages = [*_settings_checks(settings), *_crypto_checks(), *_pending_migration_checks(settings), *_scan_deprecated_usage(Path(getattr(settings, "base_dir", ".")))]
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(messages, key=lambda message: order.get(message.level, 3))
