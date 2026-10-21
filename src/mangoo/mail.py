"""Email backends -- plain console/file/SMTP senders behind one `send()` interface.

A backend is any object with a ``send(message: EmailMessage) -> None``
method -- duck-typed, the same "no ABC needed" style `mangoo.middleware`
already uses for its factory contract. `backend_from_settings()` reads
the same `_section()`-derived uppercase-keyed dict convention
`HardeningOptions.from_settings` already reads for `security`.
"""

from __future__ import annotations

import logging
import secrets
import smtplib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage as MimeMessage
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .auth import User

MAIL_LOGGER = logging.getLogger("mangoo.mail")

_NOTIFICATION_SUBJECTS = {
    "password_reset": "Reset your password",
    "email_verification": "Verify your email address",
}


@dataclass(frozen=True)
class EmailMessage:
    to: tuple[str, ...]
    subject: str
    body: str
    html_body: str | None = None
    from_email: str | None = None


def _mime_message(message: EmailMessage) -> MimeMessage:
    mime = MimeMessage()
    mime["Subject"] = message.subject
    mime["From"] = message.from_email or ""
    mime["To"] = ", ".join(message.to)
    mime.set_content(message.body)
    if message.html_body:
        mime.add_alternative(message.html_body, subtype="html")
    return mime


class ConsoleEmailBackend:
    """Logs each message instead of sending it -- the same thing
    `auth_api.log_mail_notifier` already does as the default `notify`,
    now reusable for any mail, not only password-reset/email-verification.
    """

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or MAIL_LOGGER

    def send(self, message: EmailMessage) -> None:
        self.logger.info("mangoo.mail to=%s subject=%s\n%s", ", ".join(message.to), message.subject, message.body)


class FileEmailBackend:
    """Writes each message as a small ``.eml``-ish text file under
    `directory` -- useful for local development without a real mail
    provider configured."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def send(self, message: EmailMessage) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        destination = self.directory / f"{timestamp}-{secrets.token_hex(4)}.eml"
        destination.write_text(str(_mime_message(message)), encoding="utf-8")


class SMTPEmailBackend:
    """Sends over real SMTP via the stdlib `smtplib` -- no new dependency."""

    def __init__(self, host: str, *, port: int = 587, username: str | None = None, password: str | None = None, use_tls: bool = True, timeout: float = 10.0):
        self.host, self.port, self.username, self.password, self.use_tls, self.timeout = host, port, username, password, use_tls, timeout

    def send(self, message: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as connection:
            if self.use_tls:
                connection.starttls()
            if self.username:
                connection.login(self.username, self.password or "")
            connection.send_message(_mime_message(message))


def backend_from_settings(email: dict):
    """Build the backend `Settings.email` configures.

    ``email["BACKEND"]`` selects ``"console"`` (default), ``"file"``
    (needs ``"DIRECTORY"``), or ``"smtp"`` (needs ``"HOST"``, optionally
    ``"PORT"``/``"USERNAME"``/``"PASSWORD"``/``"USE_TLS"``).
    """
    backend = str(email.get("BACKEND", "console")).lower()
    if backend == "console":
        return ConsoleEmailBackend()
    if backend == "file":
        directory = email.get("DIRECTORY")
        if not directory:
            raise ValueError("Settings.email.directory is required when Settings.email.backend = 'file'.")
        return FileEmailBackend(directory)
    if backend == "smtp":
        host = email.get("HOST")
        if not host:
            raise ValueError("Settings.email.host is required when Settings.email.backend = 'smtp'.")
        return SMTPEmailBackend(host, port=int(email.get("PORT", 587)), username=email.get("USERNAME"), password=email.get("PASSWORD"), use_tls=bool(email.get("USE_TLS", True)))
    raise ValueError(f"Settings.email.backend must be 'console', 'file', or 'smtp' -- got {backend!r}.")


def send_mail(message: EmailMessage, backend) -> None:
    """Send `message` through `backend` -- a thin, explicit call (no
    hidden global-settings lookup, matching every other entry point in
    this codebase) so callers build the backend once, the same way
    `HardeningOptions`/`SignalBus` are built once and passed in."""
    backend.send(message)


def mail_notifier(backend) -> Callable[[str, User, str], None]:
    """Adapt a mail backend into the ``notify(kind, user, token)``
    callable `auth_routes` already expects, instead of the default
    `auth_api.log_mail_notifier` (which only logs the token)."""

    def notify(kind: str, user: User, token: str) -> None:
        subject = _NOTIFICATION_SUBJECTS.get(kind, "Mangoo notification")
        send_mail(EmailMessage(to=(user.email,), subject=subject, body=f"Your {kind.replace('_', ' ')} code: {token}"), backend)

    return notify
