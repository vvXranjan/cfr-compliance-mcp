"""Logging configuration for cfr-compliance-mcp.

Every module in this codebase should obtain its logger via
`get_logger(__name__)` rather than calling `logging.getLogger` directly,
so log level/format stay centrally controlled by `Settings`.

Two formats are supported:
  - "text": human-readable, for local development.
  - "json": one JSON object per line, for log aggregation in production
    (fields: timestamp, level, logger, message, plus any `extra=` passed
    at the call site).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from cfr_compliance_mcp.config import get_settings

_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """Renders each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Include any structured `extra=` fields the caller attached,
        # skipping the standard LogRecord attributes.
        standard_attrs = logging.LogRecord(
            "", 0, "", 0, "", None, None
        ).__dict__.keys()
        for key, value in record.__dict__.items():
            if key not in standard_attrs and key not in payload:
                payload[key] = value

        return json.dumps(payload, default=str)


def configure_logging() -> None:
    """Configure the root logger once, based on current settings.

    Safe to call multiple times — subsequent calls are no-ops. This is
    called explicitly from `server.py` at process startup, rather than
    relying on import-time side effects, so behavior is predictable and
    testable.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()

    handler = logging.StreamHandler(stream=sys.stderr)
    if settings.log_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a module-scoped logger, ensuring logging is configured first."""
    configure_logging()
    return logging.getLogger(name)
