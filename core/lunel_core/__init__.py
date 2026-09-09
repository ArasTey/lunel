"""Lunel Core — the multi-protocol proxy runtime.

Lunel Core is the server runtime that Lunel instances run. It relays
VLESS / Trojan / Shadowsocks traffic over WebSocket and xHTTP transports,
exposes health/metrics APIs, and is managed remotely by the Lunel Console
through a Lunel Worker.

Derived from the RVG Gateway relay engine; refactored into a clean,
library-style package with explicit state boundaries.
"""
