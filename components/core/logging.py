"""Structured JSON logging, applied once at process start.

The formatter only shapes whatever a log record already carries; it adds no
filtering of its own. Call sites stay responsible for never logging a
prompt, model output or secret value (see ``AnthropicProvider._log``, which
this follows).
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any, Final

# Attributes every ``logging.LogRecord`` has regardless of ``extra=``; any
# other attribute came from a caller's ``extra=`` and is included verbatim.
_STANDARD_RECORD_ATTRS: Final = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
)


class JSONFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger name, message,
    and any ``extra=`` fields the call site supplied."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Replace the root logger's handlers with one JSON stream handler.
    Idempotent: safe to call more than once (tests do)."""
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    root.handlers = [handler]


_SECURITY_LOGGER: Final = logging.getLogger("security")
# The only values a security event may carry: ids and fixed vocabulary, never
# an email, token, password or request body.
_SECURITY_FIELDS: Final = frozenset(
    {"user_id", "client_address", "outcome", "reason"}
)
_SECURITY_VALUE_MAX: Final = 128


def _escaped(value: object) -> str:
    """Printable ASCII only: control characters, newlines and non-ASCII
    become escapes, so a hostile value cannot forge or split a log line."""
    return ascii(str(value))[1:-1][:_SECURITY_VALUE_MAX]


def log_security_event(name: str, **fields: object) -> None:
    """Log one structured security event (``auth.login_failed``, ...).

    Only allowlisted fields are accepted (a ``TypeError`` names an unknown
    one, so a call site cannot start logging a secret by accident); ``None``
    values are left out and the rest are escaped.
    """
    unknown = fields.keys() - _SECURITY_FIELDS
    if unknown:
        raise TypeError(f"not allowed in a security event: {sorted(unknown)}")
    extra = {
        key: _escaped(value)
        for key, value in fields.items()
        if value is not None
    }
    level = (
        logging.INFO
        if fields.get("outcome") == "success"
        else (logging.WARNING)
    )
    _SECURITY_LOGGER.log(level, name, extra={"event": name, **extra})
