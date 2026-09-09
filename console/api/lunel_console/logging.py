"""Logging for the Lunel Console API (structured + redaction, shared pattern)."""
from __future__ import annotations

import logging
import re
import sys
from collections import deque

_REDACTED = "[redacted]"
_SECRET_PATTERNS = [
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
    re.compile(r"\b[A-Za-z0-9+/_=-]{32,}\b"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
        except Exception:
            pass
        return True


def setup_logging(level: str | None = None) -> None:
    import os

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s"))
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel((level or os.environ.get("LUNEL_LOG_LEVEL", "info")).upper())
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get(category: str, name: str) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger(name), {"category": category})
