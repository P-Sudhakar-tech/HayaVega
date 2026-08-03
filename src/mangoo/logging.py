"""Safe, optional structured logging for Mangoo applications."""
from __future__ import annotations

import json
import logging


class JsonFormatter(logging.Formatter):
    """Serialize selected log-record metadata without dumping request data."""
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        for name in ("request_id", "method", "path", "status", "duration_ms"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_logging(*, json_output: bool = False, level: int = logging.INFO, handler: logging.Handler | None = None) -> logging.Handler:
    """Configure the ``mangoo`` logger; callers may provide their own handler."""
    logger = logging.getLogger("mangoo")
    active_handler = handler or logging.StreamHandler()
    active_handler.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(active_handler)
    logger.setLevel(level)
    return active_handler
