"""State management for Lunel Core.

Central, explicitly-locked stores replacing RVG's module-level globals:

* ``LinkStore``      — proxied links (credentials, quotas, expiry, enable/disable)
* ``ConnectionTracker`` — live relay connections
* ``RuntimeStats``   — traffic counters, hourly buckets, error ring
* ``StateStore``     — atomic JSON persistence with a debounce so that
  per-connection bookkeeping never blocks the event loop on disk I/O.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .logging import get

log = get("runtime", "lunel.state")

STATE_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_link_uuid() -> str:
    return str(secrets.token_hex(16))


class Link:
    """A single proxied link (credential + policy).

    The wire semantics are inherited from RVG: each link is identified by a
    UUID, carries a traffic quota and expiry, and can be disabled or enabled
    without restarting the runtime.
    """

    __slots__ = (
        "uuid", "label", "protocol", "active", "limit_bytes", "used_bytes",
        "created_at", "expires_at", "note", "alpn", "fingerprint",
        "ss_cipher", "ss_password",
    )

    def __init__(self, uuid: str, label: str, protocol: str, *, active: bool = True,
                 limit_bytes: int = 0, used_bytes: int = 0, created_at: str | None = None,
                 expires_at: str | None = None, note: str = "",
                 alpn: str = "h2,http/1.1", fingerprint: str = "chrome",
                 ss_cipher: str | None = None, ss_password: str | None = None):
        self.uuid = uuid
        self.label = label[:80]
        self.protocol = protocol
        self.active = bool(active)
        self.limit_bytes = int(limit_bytes)
        self.used_bytes = int(used_bytes)
        self.created_at = created_at or _utcnow().isoformat()
        self.expires_at = expires_at
        self.note = note[:300]
        # WebSocket transports need HTTP/1.1: an "h2" ALPN token lets the TLS
        # edge negotiate HTTP/2, where classic WS upgrades fail (RFC 8441 is
        # not spoken by common clients). Strip h2 wherever it appears.
        tokens = [t.strip() for t in alpn.split(",") if t.strip() and t.strip().lower() != "h2"]
        self.alpn = ",".join(tokens)[:60] if tokens else "http/1.1"
        self.fingerprint = fingerprint if fingerprint in ("chrome", "firefox", "ios") else "chrome"
        self.ss_cipher = ss_cipher
        self.ss_password = ss_password

    # ---- policy ---------------------------------------------------------
    def is_expired(self, now: datetime | None = None) -> bool:
        if not self.expires_at:
            return False
        try:
            return (now or _utcnow()) > datetime.fromisoformat(self.expires_at)
        except (TypeError, ValueError):
            return False

    def is_allowed(self) -> bool:
        if not self.active or self.is_expired():
            return False
        if self.limit_bytes > 0 and self.used_bytes >= self.limit_bytes:
            return False
        return True

    def to_dict(self, include_secret: bool = True) -> dict:
        data = {
            "uuid": self.uuid,
            "label": self.label,
            "protocol": self.protocol,
            "active": self.active,
            "limit_bytes": self.limit_bytes,
            "used_bytes": self.used_bytes,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "note": self.note,
            "alpn": self.alpn,
            "fingerprint": self.fingerprint,
            "expired": self.is_expired(),
        }
        if include_secret:
            data["ss_cipher"] = self.ss_cipher
            data["ss_password"] = self.ss_password
        return data


class LinkStore:
    def __init__(self):
        self._links: dict[str, Link] = {}
        self._lock = asyncio.Lock()

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    def snapshot(self) -> dict[str, Link]:
        return dict(self._links)

    def get(self, uuid: str) -> Link | None:
        return self._links.get(uuid)

    def size(self) -> int:
        return len(self._links)

    async def add(self, link: Link) -> Link:
        async with self._lock:
            self._links[link.uuid] = link
        return link

    async def remove(self, uuid: str) -> Link | None:
        async with self._lock:
            return self._links.pop(uuid, None)

    async def get_allowed(self, uuid: str) -> Link | None:
        async with self._lock:
            link = self._links.get(uuid)
        return link if (link and link.is_allowed()) else None

    async def use(self, uuid: str, n: int) -> bool:
        """Quota check + accounting. Returns False when the link is gone,
        disabled, expired, or over quota."""
        async with self._lock:
            link = self._links.get(uuid)
            if link is None or not link.is_allowed():
                return False
            link.used_bytes += n
        return True

    async def totals(self) -> dict:
        async with self._lock:
            links = list(self._links.values())
        return {
            "count": len(links),
            "active": sum(1 for l in links if l.is_allowed()),
            "expired": sum(1 for l in links if l.is_expired()),
            "used_bytes": sum(l.used_bytes for l in links),
            "limit_bytes": sum(l.limit_bytes for l in links),
        }


class ConnectionTracker:
    """Live relay connections. Only non-sensitive metadata is kept: no
    payloads, no credentials — IP, transport, byte count, timestamps."""

    def __init__(self):
        self._conns: dict[str, dict] = {}

    def register(self, conn_id: str, *, uuid: str, ip: str, transport: str) -> dict:
        conn = {
            "conn_id": conn_id,
            "uuid": uuid,
            "ip": ip,
            "transport": transport,
            "connected_at": _utcnow().isoformat(),
            "bytes": 0,
        }
        self._conns[conn_id] = conn
        return conn

    def add_bytes(self, conn_id: str, n: int) -> None:
        conn = self._conns.get(conn_id)
        if conn is not None:
            conn["bytes"] += n

    def remove(self, conn_id: str) -> None:
        self._conns.pop(conn_id, None)

    def count(self) -> int:
        return len(self._conns)

    def grouped_by_ip(self) -> list[dict]:
        grouped: dict[str, dict] = {}
        for c in self._conns.values():
            g = grouped.setdefault(
                c["ip"],
                {"ip": c["ip"], "sessions": 0, "bytes": 0, "transports": set(),
                 "first_connected_at": c["connected_at"], "last_connected_at": c["connected_at"]},
            )
            g["sessions"] += 1
            g["bytes"] += c["bytes"]
            g["transports"].add(c["transport"])
            if c["connected_at"] < g["first_connected_at"]:
                g["first_connected_at"] = c["connected_at"]
            if c["connected_at"] > g["last_connected_at"]:
                g["last_connected_at"] = c["connected_at"]
        out = []
        for g in grouped.values():
            out.append({
                "ip": g["ip"],
                "sessions": g["sessions"],
                "bytes": g["bytes"],
                "transports": sorted(g["transports"]),
                "first_connected_at": g["first_connected_at"],
                "last_connected_at": g["last_connected_at"],
            })
        out.sort(key=lambda x: x["last_connected_at"], reverse=True)
        return out


class RuntimeStats:
    def __init__(self):
        self.total_bytes = 0
        self.total_requests = 0
        self.total_errors = 0
        self.started_at = time.time()
        self.hourly: defaultdict = defaultdict(int)
        self.errors: deque = deque(maxlen=50)

    def add_traffic(self, n: int) -> None:
        self.total_bytes += n
        self.hourly[datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")] += n

    def add_request(self) -> None:
        self.total_requests += 1

    def add_error(self, message: str) -> None:
        self.total_errors += 1
        self.errors.append({"error": str(message)[:300], "time": _utcnow().isoformat()})

    def uptime_seconds(self) -> int:
        return int(time.time() - self.started_at)

    def prune_hourly(self, keep_hours: int = 48) -> None:
        if len(self.hourly) <= keep_hours:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=keep_hours)).strftime("%Y-%m-%dT%H:00")
        for key in [k for k in self.hourly if k < cutoff]:
            del self.hourly[key]


class StateStore:
    """Atomic JSON persistence with debounced saves (inherited behavior from
    RVG, now encapsulated). Corrupt files are renamed aside, never crash boot."""

    def __init__(self, path: str):
        self.path = Path(path)
        self._save_lock = asyncio.Lock()
        self._pending = False
        self._dirty_again = False
        self._debounce = 2.0

    async def load(self, links: LinkStore, stats: RuntimeStats) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            backup = self.path.with_suffix(".corrupt")
            try:
                self.path.replace(backup)
                log.warning("state file unreadable (%s); moved to %s", exc, backup)
            except OSError:
                log.warning("state file unreadable: %s", exc)
            return
        for data in raw.get("links", []):
            try:
                await links.add(Link(**data))
            except TypeError:
                continue
        stats.total_bytes = int(raw.get("total_bytes", 0))
        for key, value in (raw.get("hourly") or {}).items():
            stats.hourly[key] = int(value)
        log.info("state loaded: %d links", links.size())

    async def save(self, links: LinkStore, stats: RuntimeStats) -> None:
        async with self._save_lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                payload = {
                    "version": STATE_VERSION,
                    "saved_at": _utcnow().isoformat(),
                    "total_bytes": stats.total_bytes,
                    "hourly": dict(stats.hourly),
                    "links": [
                        {slot: getattr(link, slot) for slot in Link.__slots__}
                        for link in links.snapshot().values()
                    ],
                }
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                log.warning("could not persist state: %s", exc)

    async def schedule_save(self, links: LinkStore, stats: RuntimeStats) -> None:
        """Debounced save — many connection-close events collapse into one
        disk write. Same idea as RVG's schedule_save, encapsulated."""
        if self._pending:
            self._dirty_again = True
            return
        self._pending = True
        try:
            while True:
                self._dirty_again = False
                await asyncio.sleep(self._debounce)
                await self.save(links, stats)
                if not self._dirty_again:
                    break
        finally:
            self._pending = False
