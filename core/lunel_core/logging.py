"""Structured logging for Lunel Core with automatic secret redaction.

Every log record passes through a redaction filter that scrubs values
which look like secrets (UUIDs used as credentials, SS passwords, API
tokens, MTP secrets) before they reach any handler. The relay code never
logs link credentials; this filter is defense in depth.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time
from collections import deque

_REDACTED = "[redacted]"

# long hex/base64url-ish blobs (uuids, secrets, tokens)
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
            if record.args:
                record.args = tuple(
                    redact(str(a)) if isinstance(a, str) else a for a in record.args
                )
        except Exception:
            pass
        return True


class MemoryRingHandler(logging.Handler):
    """Keeps the last N formatted records for the Console log viewer."""

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self.records: deque = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append(
                {
                    "ts": record.created,
                    "level": record.levelname,
                    "category": getattr(record, "category", "runtime"),
                    "message": self.format(record),
                }
            )
        except Exception:
            pass

    def snapshot(self, limit: int = 200) -> list[dict]:
        return list(self.records)[-limit:]


ring_handler: MemoryRingHandler | None = None


def setup_logging(level: str = "info", json_mode: bool = False) -> MemoryRingHandler:
    global ring_handler
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler: logging.Handler
    if json_mode:
        handler = logging.StreamHandler(sys.stdout)

        class _JsonFmt(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                payload = {
                    "ts": round(record.created, 3),
                    "level": record.levelname,
                    "logger": record.name,
                    "category": getattr(record, "category", "runtime"),
                    "message": record.getMessage(),
                }
                return json.dumps(payload, ensure_ascii=False)

        handler.setFormatter(_JsonFmt())
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
        )
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())

    ring_handler = MemoryRingHandler()
    ring_handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    ring_handler.addFilter(RedactingFilter())
    root.addHandler(ring_handler)

    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return ring_handler


def get(category: str, name: str) -> logging.LoggerAdapter:
    """Logger with a ``category`` attribute used by the ring buffer."""
    base = logging.getLogger(name)
    return logging.LoggerAdapter(base, {"category": category})


def monotonic() -> float:
    return time.monotonic()
