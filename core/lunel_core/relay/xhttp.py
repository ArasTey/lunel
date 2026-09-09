"""XHTTP transport engine (packet-up / stream-up) for VLESS and Trojan.

RVG shipped two ~95%-identical engines (one under protocol/vless, one under
protocol/trojan). Lunel Core merges them into this single engine with the
following security fixes over the original:

1. Sessions are keyed by (uuid, session_id) — a session can never be
   attached to by a different (valid) link. In RVG, session_id was a bare
   bearer capability and any authenticated link could read/write another
   link's session stream.
2. Per-link and global session caps (memory-DoS protection).
3. packet-up seq_buf is capped in both entries and bytes; overflow tears
   the session down instead of buffering unboundedly.
4. Request bodies are size-capped.

Wire behavior (paths, fingerprints, response prefix rules, AIMD flow,
adaptive quota batching) is preserved so existing xHTTP clients keep working.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import socket
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect

from ..logging import get
from .base import QuotaGate, RelayContext, tune_socket
from .trojan import trojan_password_hash
from .vless import parse_vless_header

log = get("network", "lunel.relay.xhttp")

XHTTP_BUF = 1024 * 1024
DOWNLINK_QUEUE_MAX = 512
SESSION_IDLE_TIMEOUT = 30
SESSION_IDLE_TIMEOUT_ACTIVE = 90
REAPER_INTERVAL = 10
TCP_CONNECT_TIMEOUT = 10.0

SOCK_BUF_SIZE = 4 * 1024 * 1024

FLOW_MIN_HW = 256 * 1024
FLOW_MAX_HW = 32 * 1024 * 1024
FLOW_START_HW = 2 * 1024 * 1024
FLOW_FAST_DRAIN_MS = 12.0
FLOW_SLOW_DRAIN_MS = 40.0

PACKET_UP_HIGH_WATER = 2 * 1024 * 1024

# Security limits (new in Lunel Core)
MAX_SESSIONS_GLOBAL = 2048
MAX_SESSIONS_PER_LINK = 64
MAX_SEQ_BUF_ENTRIES = 256
MAX_SEQ_BUF_BYTES = 4 * 1024 * 1024
MAX_PACKET_BODY = 8 * 1024 * 1024
MAX_STREAM_CHUNK = 2 * 1024 * 1024

UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SESSION_ID_MAX = 128

FINGERPRINTS = {
    "chrome": {
        "content-type": "application/grpc",
        "cache-control": "no-cache, no-store",
        "x-accel-buffering": "no",
        "server": "cloudflare",
    },
    "plain": {
        "content-type": "application/octet-stream",
        "cache-control": "no-store",
        "x-accel-buffering": "no",
    },
}


def _resp_headers(fp: str) -> dict:
    return dict(FINGERPRINTS.get(fp, FINGERPRINTS["chrome"]))


def req_client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else "unknown"


class _AdaptiveFlow:
    """AIMD-style adaptive drain high-water (ported from RVG)."""

    __slots__ = ("high_water",)

    def __init__(self):
        self.high_water = FLOW_START_HW

    def should_drain(self, buf_size: int) -> bool:
        return buf_size > self.high_water

    async def drain(self, writer: asyncio.StreamWriter) -> None:
        t0 = time.monotonic()
        await writer.drain()
        elapsed_ms = (time.monotonic() - t0) * 1000
        if elapsed_ms < FLOW_FAST_DRAIN_MS:
            self.high_water = min(FLOW_MAX_HW, int(self.high_water * 2.0) + 65536)
        elif elapsed_ms > FLOW_SLOW_DRAIN_MS:
            self.high_water = max(FLOW_MIN_HW, self.high_water // 2)


class XHttpEngine:
    def __init__(self, ctx: RelayContext, prefix: str):
        self.ctx = ctx
        self.prefix = prefix
        self.sessions: dict[tuple[str, str], dict] = {}
        self._lock = asyncio.Lock()
        self._reaper_started = False
        self.router = APIRouter()
        self.router.add_api_route(
            f"{prefix}/stream-up/{{uuid}}/{{session_id}}", self.stream_up_upload, methods=["POST"]
        )
        self.router.add_api_route(
            f"{prefix}/packet-up/{{uuid}}/{{session_id}}/{{seq}}", self.packet_up_upload, methods=["POST"]
        )
        self.router.add_api_route(
            f"{prefix}/{{mode}}/{{uuid}}/{{session_id}}", self.xhttp_downlink, methods=["GET"]
        )

    # ---- session management ---------------------------------------------
    def _per_link_count(self, uuid: str) -> int:
        return sum(1 for (u, _) in self.sessions if u == uuid)

    async def get_or_create_session(self, uuid: str, mode: str, session_id: str, ip: str) -> dict:
        # FIX 1: sessions are keyed by (uuid, session_id) so a valid link can
        # never attach to another link's session.
        key = (uuid, session_id)
        async with self._lock:
            sess = self.sessions.get(key)
            if sess is not None:
                sess["last_seen"] = time.time()
                return sess
            if len(self.sessions) >= MAX_SESSIONS_GLOBAL or self._per_link_count(uuid) >= MAX_SESSIONS_PER_LINK:
                raise HTTPException(status_code=429, detail="too many sessions")
            conn_id = secrets.token_urlsafe(6)
            self.ctx.connections.register(conn_id, uuid=uuid, ip=ip, transport=f"xhttp-{mode}")
            sess = {
                "uuid": uuid, "mode": mode, "writer": None,
                "downlink_task": None,
                "down_q": asyncio.Queue(maxsize=DOWNLINK_QUEUE_MAX),
                "last_seen": time.time(),
                "conn_id": conn_id, "tcp_open": False, "closed": False,
                "seq_buf": {}, "seq_buf_bytes": 0, "next_seq": 0,
                "gate": None,
                "flow": None,
            }
            self.sessions[key] = sess
            log.info("new xhttp[%s] session [%s] uuid=%s ip=%s total=%d",
                     mode, session_id[:8], uuid[:8], ip, len(self.sessions))
            return sess

    async def teardown(self, uuid: str, session_id: str, reason: str = "") -> None:
        key = (uuid, session_id)
        async with self._lock:
            sess = self.sessions.pop(key, None)
        if not sess:
            return
        sess["closed"] = True
        task = sess.get("downlink_task")
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        writer = sess.get("writer")
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self.ctx.connections.remove(sess.get("conn_id"))
        dq = sess.get("down_q")
        if dq:
            try:
                dq.put_nowait(None)
            except Exception:
                pass
        log.info("closed xhttp[%s] [%s] total=%d %s", sess.get("mode"), session_id[:8],
                 len(self.sessions), reason)

    async def _reaper(self) -> None:
        while True:
            await asyncio.sleep(REAPER_INTERVAL)
            now = time.time()
            async with self._lock:
                stale = []
                for (uuid, sid), s in self.sessions.items():
                    idle = now - s["last_seen"]
                    limit = SESSION_IDLE_TIMEOUT_ACTIVE if s.get("tcp_open") else SESSION_IDLE_TIMEOUT
                    if idle > limit:
                        stale.append((uuid, sid))
            for uuid, sid in stale:
                await self.teardown(uuid, sid, reason="idle-timeout")
            self.ctx.stats.prune_hourly()

    def ensure_reaper(self) -> None:
        if not self._reaper_started:
            self._reaper_started = True
            asyncio.create_task(self._reaper())

    # ---- TCP open --------------------------------------------------------
    async def _open_tcp_for_session(self, uuid: str, session_id: str, sess: dict,
                                    first_chunk: bytes) -> None:
        link = self.ctx.links.get(uuid)
        if link is None or not link.is_allowed():
            raise HTTPException(status_code=403, detail="not authorized")
        proto = link.protocol
        if proto.startswith("trojan"):
            is_trojan = True
            vless_prefix = False
        else:
            is_trojan = False
            vless_prefix = True

        if is_trojan:
            # Trojan header: 56-byte hex sha224(password) + CRLF + request
            if len(first_chunk) < 58 + 2:
                raise ValueError("trojan header too small")
            pw_hash = first_chunk[:56].decode("ascii", errors="ignore").lower()
            if not secrets.compare_digest(pw_hash, trojan_password_hash(link.uuid)):
                raise ValueError("trojan auth failed")
            from .trojan import parse_trojan_request

            address, port, payload = parse_trojan_request(first_chunk, trojan_password_hash(link.uuid))
        else:
            if not UUID_RE.match(uuid):
                raise ValueError("invalid link id")
            _command, address, port, payload = parse_vless_header(first_chunk)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(address, port), timeout=TCP_CONNECT_TIMEOUT
            )
        except asyncio.TimeoutError:
            log.error("xhttp tcp connect timeout -> %s:%d", address, port)
            raise
        except OSError as exc:
            log.error("xhttp tcp connect failed -> %s:%d: %s", address, port, exc)
            raise

        tune_socket(writer, SOCK_BUF_SIZE)
        if payload:
            writer.write(payload)
            await writer.drain()

        log.info("connect xhttp[%s] [%s] -> %s:%d", sess["mode"], session_id[:8], address, port)
        sess["writer"] = writer
        sess["tcp_open"] = True
        sess["downlink_task"] = asyncio.create_task(
            self._pump_tcp_to_queue(uuid, session_id, reader, sess, vless_prefix=vless_prefix)
        )

    async def _pump_tcp_to_queue(self, uuid: str, session_id: str, reader: asyncio.StreamReader,
                                 sess: dict, vless_prefix: bool) -> None:
        gate = QuotaGate(self.ctx, uuid, sess["conn_id"])
        close_reason = "remote-eof"
        first = True
        try:
            while True:
                try:
                    data = await reader.read(XHTTP_BUF)
                except (ConnectionResetError, OSError) as exc:
                    close_reason = f"tcp-read-error: {type(exc).__name__}"
                    log.info("xhttp downlink read error: %s", close_reason)
                    break
                if not data:
                    break
                if not await gate.add(len(data)):
                    close_reason = "quota-exceeded"
                    break
                if vless_prefix and first:
                    await sess["down_q"].put(b"\x00\x00" + data)
                    first = False
                else:
                    await sess["down_q"].put(data)
        except asyncio.CancelledError:
            close_reason = "cancelled"
        except Exception as exc:
            close_reason = f"unexpected: {type(exc).__name__}"
            log.error("xhttp downlink pump crashed: %s", exc)
        finally:
            await gate.flush()
            await self.teardown(uuid, session_id, reason=close_reason)

    # ---- routes ----------------------------------------------------------
    async def stream_up_upload(self, uuid: str, session_id: str, request: Request):
        self.ensure_reaper()
        if len(session_id) > SESSION_ID_MAX or not UUID_RE.match(uuid):
            raise HTTPException(status_code=400, detail="bad identifiers")
        sess = await self.get_or_create_session(uuid, "stream-up", session_id, req_client_ip(request))
        if sess.get("closed"):
            raise HTTPException(status_code=404, detail="session closed")

        gate = sess.get("gate")
        if gate is None:
            gate = QuotaGate(self.ctx, uuid, sess["conn_id"])
            sess["gate"] = gate
        flow = sess.get("flow")
        if flow is None:
            flow = _AdaptiveFlow()
            sess["flow"] = flow

        writer = sess["writer"]
        try:
            async for chunk in request.stream():
                if not chunk:
                    continue
                if len(chunk) > MAX_STREAM_CHUNK:
                    raise HTTPException(status_code=413, detail="chunk too large")
                sess["last_seen"] = time.time()
                if not await gate.add(len(chunk)):
                    raise HTTPException(status_code=403, detail="quota exceeded or link disabled")
                self.ctx.stats.add_request()

                if writer is None:
                    await self._open_tcp_for_session(uuid, session_id, sess, chunk)
                    writer = sess["writer"]
                    continue
                if writer.is_closing():
                    raise ConnectionError("transport closing")
                writer.write(chunk)
                if flow.should_drain(writer.transport.get_write_buffer_size()):
                    await flow.drain(writer)
        except ClientDisconnect:
            await gate.flush()
            await self.teardown(uuid, session_id, reason="client-disconnect")
            return {"ok": True, "aborted": True}
        except HTTPException:
            await gate.flush()
            await self.teardown(uuid, session_id, reason="quota/http")
            raise
        except Exception:
            await gate.flush()
            await self.teardown(uuid, session_id, reason="stream-error")
            raise HTTPException(status_code=502, detail="stream error")

        await gate.flush()
        return {"ok": True}

    async def packet_up_upload(self, uuid: str, seq: int, session_id: str, request: Request):
        self.ensure_reaper()
        if len(session_id) > SESSION_ID_MAX or not UUID_RE.match(uuid):
            raise HTTPException(status_code=400, detail="bad identifiers")
        if seq < 0 or seq > 10_000_000:
            raise HTTPException(status_code=400, detail="bad seq")
        sess = await self.get_or_create_session(uuid, "packet-up", session_id, req_client_ip(request))
        if sess.get("closed"):
            raise HTTPException(status_code=404, detail="session closed")

        sess["last_seen"] = time.time()
        try:
            body = await request.body()
        except ClientDisconnect:
            log.info("xhttp[packet-up] client disconnected mid-body (seq=%s), session kept", seq)
            return {"ok": True, "aborted": True}

        # FIX 4: hard cap on packet body size.
        if len(body) > MAX_PACKET_BODY:
            await self.teardown(uuid, session_id, reason="body-too-large")
            raise HTTPException(status_code=413, detail="packet too large")
        if not body:
            return {"ok": True}

        gate = sess.get("gate")
        if gate is None:
            gate = QuotaGate(self.ctx, uuid, sess["conn_id"])
            sess["gate"] = gate
        if not await gate.add(len(body)):
            await self.teardown(uuid, session_id, reason="quota-exceeded")
            raise HTTPException(status_code=403, detail="quota exceeded or link disabled")
        self.ctx.stats.add_request()

        try:
            if sess["writer"] is None:
                if seq != 0:
                    # FIX 3: cap out-of-order buffering.
                    if (len(sess["seq_buf"]) >= MAX_SEQ_BUF_ENTRIES
                            or sess["seq_buf_bytes"] + len(body) > MAX_SEQ_BUF_BYTES):
                        raise ConnectionError("seq buffer overflow")
                    sess["seq_buf"][seq] = body
                    sess["seq_buf_bytes"] += len(body)
                    return {"ok": True, "buffered": True}
                await self._open_tcp_for_session(uuid, session_id, sess, body)
                nxt = 1
                while nxt in sess["seq_buf"]:
                    pending = sess["seq_buf"].pop(nxt)
                    if sess["writer"].is_closing():
                        raise ConnectionError("transport closing")
                    sess["writer"].write(pending)
                    nxt += 1
                sess["next_seq"] = nxt
                return {"ok": True, "connected": True}

            if seq == sess["next_seq"]:
                if sess["writer"].is_closing():
                    raise ConnectionError("transport closing")
                sess["writer"].write(body)
                sess["next_seq"] += 1
                while sess["next_seq"] in sess["seq_buf"]:
                    pending = sess["seq_buf"].pop(sess["next_seq"])
                    if sess["writer"].is_closing():
                        raise ConnectionError("transport closing")
                    sess["writer"].write(pending)
                    sess["next_seq"] += 1
            else:
                if (len(sess["seq_buf"]) >= MAX_SEQ_BUF_ENTRIES
                        or sess["seq_buf_bytes"] + len(body) > MAX_SEQ_BUF_BYTES):
                    raise ConnectionError("seq buffer overflow")
                sess["seq_buf"][seq] = body
                sess["seq_buf_bytes"] += len(body)

            if sess["writer"].transport.get_write_buffer_size() > PACKET_UP_HIGH_WATER:
                await sess["writer"].drain()
        except ConnectionError as exc:
            await self.teardown(uuid, session_id, reason=f"write-failed: {exc}")
            raise HTTPException(status_code=502, detail="write failed")
        except Exception:
            await self.teardown(uuid, session_id, reason="write-failed")
            raise HTTPException(status_code=502, detail="write failed")

        return {"ok": True}

    async def xhttp_downlink(self, mode: str, uuid: str, session_id: str, request: Request):
        self.ensure_reaper()
        if len(session_id) > SESSION_ID_MAX or not UUID_RE.match(uuid):
            raise HTTPException(status_code=400, detail="bad identifiers")
        if mode not in ("packet-up", "stream-up"):
            raise HTTPException(status_code=404, detail="unknown mode")
        link = self.ctx.links.get(uuid)
        if link is None or not link.is_allowed():
            raise HTTPException(status_code=403, detail="not authorized")

        sess = await self.get_or_create_session(uuid, mode, session_id, req_client_ip(request))
        if sess.get("closed"):
            raise HTTPException(status_code=404, detail="session closed")

        headers = _resp_headers("chrome")

        async def gen():
            while True:
                chunk = await sess["down_q"].get()
                if chunk is None:
                    break
                sess["last_seen"] = time.time()
                yield chunk

        return StreamingResponse(gen(), media_type=headers.pop("content-type", "application/grpc"), headers=headers)
