"""Shared relay plumbing for all transports.

Ported from RVG's vless.py/trojan.py common machinery, now parameterized by
an explicit ``RelayContext`` instead of importing panel globals from main.py.
"""
from __future__ import annotations

import asyncio
import socket
import time

from fastapi import WebSocket

from ..logging import get
from ..state import ConnectionTracker, LinkStore, RuntimeStats

log = get("network", "lunel.relay")

SOCK_BUF = 512 * 1024

# Adaptive quota batching (per-connection EWMA), ported from RVG's _QuotaGate.
QUOTA_MIN_BATCH = 32 * 1024
QUOTA_MAX_BATCH = 2 * 1024 * 1024
QUOTA_START_BATCH = 128 * 1024
QUOTA_CHECK_INTERVAL = 0.25


class RelayContext:
    """Everything a relay handler is allowed to touch. No panel globals."""

    def __init__(self, *, links: LinkStore, connections: ConnectionTracker,
                 stats: RuntimeStats, save_hook, cfg):
        self.links = links
        self.connections = connections
        self.stats = stats
        self._save_hook = save_hook
        self.cfg = cfg

    def schedule_save(self) -> None:
        if self._save_hook is not None:
            self._save_hook()


class QuotaGate:
    """Adaptive batched quota accounting.

    Takes the per-link lock once per time window / batch size instead of once
    per WebSocket frame, which is what keeps throughput high on fast links.
    """

    __slots__ = ("ctx", "uuid", "conn_id", "pending", "last_check", "ok",
                 "batch_bytes", "rate_ewma")

    def __init__(self, ctx: RelayContext, uuid: str, conn_id: str | None = None):
        self.ctx = ctx
        self.uuid = uuid
        self.conn_id = conn_id
        self.pending = 0
        self.last_check = time.monotonic()
        self.ok = True
        self.batch_bytes = QUOTA_START_BATCH
        self.rate_ewma = 0.0

    async def _account(self, n: int) -> bool:
        self.ctx.stats.add_traffic(n)
        if self.conn_id:
            self.ctx.connections.add_bytes(self.conn_id, n)
        return await self.ctx.links.use(self.uuid, n)

    async def add(self, nbytes: int) -> bool:
        if not self.ok:
            return False
        self.pending += nbytes
        now = time.monotonic()
        elapsed = now - self.last_check
        if self.pending >= self.batch_bytes or elapsed >= QUOTA_CHECK_INTERVAL:
            flush, self.pending = self.pending, 0
            if elapsed > 0:
                rate = flush / elapsed
                self.rate_ewma = rate if self.rate_ewma == 0 else (0.7 * self.rate_ewma + 0.3 * rate)
                target = int(self.rate_ewma * QUOTA_CHECK_INTERVAL)
                self.batch_bytes = max(QUOTA_MIN_BATCH, min(QUOTA_MAX_BATCH, target or QUOTA_MIN_BATCH))
            self.last_check = now
            try:
                self.ok = await self._account(flush)
            except Exception as exc:
                log.error("QuotaGate.add failed uuid=%s: %s", self.uuid[:8], exc)
                self.ok = False
            return self.ok
        return True

    async def flush(self) -> bool:
        if self.pending:
            flush, self.pending = self.pending, 0
            try:
                self.ok = self.ok and await self._account(flush)
            except Exception as exc:
                log.error("QuotaGate.flush failed uuid=%s: %s", self.uuid[:8], exc)
                self.ok = False
        return self.ok


def tune_socket(writer: asyncio.StreamWriter, sock_buf: int = SOCK_BUF) -> None:
    """TCP_NODELAY + OS socket buffers to shave latency (ported from RVG)."""
    try:
        sock = writer.transport.get_extra_info("socket")
        if sock is None:
            return
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, sock_buf)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, sock_buf)
        if hasattr(socket, "TCP_QUICKACK"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
        if hasattr(socket, "TCP_USER_TIMEOUT"):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 20000)
    except Exception as exc:
        log.debug("tune_socket failed: %s", exc)


def ws_client_ip(ws: WebSocket) -> str:
    fwd = ws.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real_ip = ws.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return ws.client.host if ws.client else "unknown"


def write_ws_error(ctx: RelayContext, message: str) -> None:
    ctx.stats.add_error(message)


async def pump_ws_to_tcp(ctx: RelayContext, ws: WebSocket, writer: asyncio.StreamWriter,
                         gate: QuotaGate, high_water: int) -> None:
    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            data = msg.get("bytes") or (msg.get("text") or "").encode()
            if not data:
                continue
            if not await gate.add(len(data)):
                await ws.close(code=1008, reason="quota exceeded or link disabled")
                break
            ctx.stats.add_request()
            writer.write(data)
            if writer.transport.get_write_buffer_size() > high_water:
                await writer.drain()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await gate.flush()
        try:
            writer.write_eof()
        except Exception:
            pass


async def pump_tcp_to_ws(ctx: RelayContext, ws: WebSocket, reader: asyncio.StreamReader,
                         gate: QuotaGate, buf_size: int,
                         first_payload_prefix: bytes | None = None) -> None:
    first = True
    try:
        while True:
            data = await reader.read(buf_size)
            if not data:
                break
            if not await gate.add(len(data)):
                await ws.close(code=1008, reason="quota exceeded or link disabled")
                break
            if first and first_payload_prefix is not None:
                await ws.send_bytes(first_payload_prefix + data)
                first = False
            else:
                await ws.send_bytes(data)
    except Exception:
        pass
    finally:
        await gate.flush()
