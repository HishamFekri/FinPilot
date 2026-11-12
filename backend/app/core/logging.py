"""Structured JSON logs. Never log tokens, emails, amounts, or request bodies (SECURITY.md §8)."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Only these extra fields are emitted, so a stray log argument can't leak data.
_ALLOWED_EXTRA = ("route", "method", "status", "duration_ms", "error_type", "reason")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        for key in _ALLOWED_EXTRA:
            value = getattr(record, key, None)
            if value is not None:
                entry[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # These record full URLs, including query strings. Our middleware logs route templates instead.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("httpx", "httpcore", "httpx2", "httpcore2"):
        logging.getLogger(name).setLevel(logging.WARNING)
