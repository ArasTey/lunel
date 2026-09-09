"""Configuration system for Lunel Core.

Sources, in ascending priority:
  1. Defaults defined here.
  2. Optional TOML configuration file (``--config /path/to/lunel-core.toml`` or
     ``LUNEL_CONFIG``). Only a handful of keys are honored; see ``FILE_KEYS``.
  3. Environment variables.
  4. CLI flags (parsed in ``__main__.py``).

The config intentionally contains only the options a runtime actually needs.
Secrets (``LUNEL_CORE_API_TOKEN``) are never echoed in API responses or logs.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None


PROTOCOLS = (
    "vless-ws",
    "xhttp-packet-up",
    "xhttp-stream-up",
    "trojan-ws",
    "trojan-xhttp-packet-up",
    "trojan-xhttp-stream-up",
    "shadowsocks",
)
DEFAULT_PROTOCOL = "vless-ws"

CIPHERS = ("aes-256-gcm", "aes-192-gcm", "aes-128-gcm", "chacha20-ietf-poly1305")
DEFAULT_CIPHER = "aes-256-gcm"

FILE_KEYS = {
    "port": int,
    "log_level": str,
    "state_path": str,
    "quota_default_bytes": int,
}


@dataclasses.dataclass
class CoreConfig:
    # HTTP server (health API + websocket transports + xhttp)
    port: int = 8000
    host: str = "0.0.0.0"

    # Bearer token protecting the management API (/core/api/*).
    # Health/readiness/version stay unauthenticated on purpose.
    api_token: str = ""

    # Persistence for links & counters (JSON file, atomic writes).
    state_path: str = "/data/state.json"

    # Logging
    log_level: str = "info"
    log_json: bool = False

    # Relay tuning
    relay_buf: int = 256 * 1024
    sock_buf: int = 512 * 1024
    write_high_water: int = 128 * 1024
    ws_handshake_timeout: float = 15.0
    upstream_connect_timeout: float = 10.0

    # Public hostname used when rendering share links (informational only;
    # the Console normally injects this per instance).
    public_host: str = ""

    def with_cli_overrides(self, ns: argparse.Namespace) -> "CoreConfig":
        for field in ("port", "host", "config_file", "state_path", "log_level", "log_json", "public_host"):
            value = getattr(ns, field, None)
            if value is not None:
                if field == "log_json":
                    self.log_json = bool(value)
                else:
                    setattr(self, field, value)
        return self


def _load_toml(path: str | None) -> dict:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    if tomllib is None:  # pragma: no cover
        raise RuntimeError("tomllib unavailable; use Python 3.11+")
    with p.open("rb") as fh:
        raw = tomllib.load(fh)
    core = raw.get("core", raw)
    out = {}
    for key, typ in FILE_KEYS.items():
        if key in core:
            out[key] = typ(core[key])
    return out


def build_config(argv: list[str] | None = None) -> tuple[CoreConfig, argparse.Namespace]:
    parser = argparse.ArgumentParser(prog="lunel-core", description="Lunel Core runtime")
    parser.add_argument("--config", dest="config_file", default=os.environ.get("LUNEL_CONFIG"))
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--log-json", action="store_true", default=None)
    parser.add_argument("--public-host", default=None)
    ns = parser.parse_args(argv)

    cfg = CoreConfig()

    for key, value in _load_toml(ns.config_file).items():
        setattr(cfg, key, value)

    env_map = {
        "port": ("PORT", int),
        "host": ("LUNEL_HOST", str),
        "api_token": ("LUNEL_CORE_API_TOKEN", str),
        "state_path": ("LUNEL_STATE_PATH", str),
        "log_level": ("LUNEL_LOG_LEVEL", str),
        "log_json": ("LUNEL_LOG_JSON", lambda v: v.lower() in ("1", "true", "yes")),
        "public_host": ("LUNEL_PUBLIC_HOST", str),
        "relay_buf": ("LUNEL_RELAY_BUF", int),
        "sock_buf": ("LUNEL_SOCK_BUF", int),
        "write_high_water": ("LUNEL_WRITE_HIGH_WATER", int),
        "ws_handshake_timeout": ("LUNEL_WS_HANDSHAKE_TIMEOUT", float),
        "upstream_connect_timeout": ("LUNEL_UPSTREAM_CONNECT_TIMEOUT", float),
    }
    for field, (env, cast) in env_map.items():
        raw = os.environ.get(env)
        if raw is not None and raw != "":
            try:
                setattr(cfg, field, cast(raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid value for {env}: {raw!r} ({exc})") from exc

    if cfg.port < 1 or cfg.port > 65535:
        raise ValueError(f"port out of range: {cfg.port}")
    if cfg.log_level.lower() not in ("debug", "info", "warning", "error"):
        raise ValueError(f"invalid log level: {cfg.log_level}")

    cfg.with_cli_overrides(ns)
    return cfg, ns
