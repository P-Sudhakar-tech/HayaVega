"""Safe, optional structured logging for Hayavega applications."""

from __future__ import annotations

import logging

import msgspec


class JsonFormatter(logging.Formatter):
    """Serialize selected log-record metadata without dumping request data."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for name in ("request_id", "method", "path", "status", "duration_ms"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        return msgspec.json.encode(payload, enc_hook=str).decode("utf-8")


def configure_logging(*, json_output: bool = False, level: int = logging.INFO, handler: logging.Handler | None = None) -> logging.Handler:
    """Configure the ``hayavega`` logger; callers may provide their own handler."""
    logger = logging.getLogger("hayavega")
    active_handler = handler or logging.StreamHandler()
    active_handler.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(active_handler)
    logger.setLevel(level)
    return active_handler
