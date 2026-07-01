"""Structured JSON logging for Intelli-Credit.

Emits single-line JSON records so logs are ingestible by observability
platforms (CloudWatch, ELK, Databricks audit tables).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Attach any structured extras passed via logging `extra=`.
        for key, value in getattr(record, "context", {}).items():
            payload[key] = value
        return json.dumps(payload, default=str)


_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Configure root logging once with the JSON formatter."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for the given name."""
    configure_logging()
    return logging.getLogger(name)


def log_context(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    """Log a message with structured context fields."""
    logger.log(level, message, extra={"context": context})
