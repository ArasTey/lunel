"""Persistent instance registry for the Lunel Worker.

Worker restarts (platform redeploys, node reboots) kill managed Core
processes. This registry persists each instance's launch parameters to the
worker data dir so startup can relaunch them with identical credentials —
links survive too, because Core persists its own state alongside.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def _path() -> Path:
    base = Path(os.environ.get("LUNEL_WORKER_DATA", "/data/instances"))
    base.mkdir(parents=True, exist_ok=True)
    return base / "registry.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_instance(instance_id: str, data: dict) -> None:
    reg = load()
    reg[instance_id] = data
    try:
        _path().write_text(json.dumps(reg, indent=1), encoding="utf-8")
    except OSError as exc:
        from .logging import get

        get("runtime", "lunel.worker.registry").warning("registry save failed: %s", exc)


def remove(instance_id: str) -> None:
    reg = load()
    if instance_id in reg:
        reg.pop(instance_id)
        try:
            _path().write_text(json.dumps(reg, indent=1), encoding="utf-8")
        except OSError:
            pass
