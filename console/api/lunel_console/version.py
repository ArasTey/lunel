"""Lunel Console API version info."""
from __future__ import annotations

import os

APP_NAME = "Lunel Console"


def version() -> str:
    return os.environ.get("LUNEL_CONSOLE_VERSION", "1.0.0")


def info() -> dict:
    return {
        "name": APP_NAME,
        "version": version(),
        "build": os.environ.get("LUNEL_CONSOLE_BUILD", "dev"),
    }
