"""Trojan over WebSocket (``/trojan-ws``).

Protocol handling ported from RVG's protocol/trojan (trojan.py + websocket.py):
SHA-224 hex password authentication followed by a SOCKS5-like request header.
"""
from __future__ import annotations

import asyncio
import hashlib
import secrets

from fastapi import WebSocket, WebSocketDisconnect

from ..logging import get
from .base import (
    QuotaGate,
    RelayContext,
    pump_tcp_to_ws,
    pump_ws_to_tcp,
    tune_socket,
    ws_client_ip,
)

log = get("network", "lunel.relay.trojan")

ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04


def trojan_password_hash(password: str) -> str:
    return hashlib.sha224(password.encode("utf-8")).hexdigest()


def parse_trojan_request(chunk: bytes, expected_hash_hex: str) -> tuple[str, int, bytes]:
    """Validate a Trojan request header. Returns (address, port, payload).

    Layout: 56-byte hex SHA-224 of the password \\r\\n SOCKS5-ish request
    (ATYP, addr, port) \\r\\n payload...
    """
    if len(chunk) < 1 + 56 + 2 + 1 + 1 + 1 + 2 + 2:
        raise ValueError("chunk too small")
    given = chunk[:56].decode("ascii", errors="ignore").lower()
    if not secrets.compare_digest(given, expected_hash_hex.lower()):
        raise PermissionError("authentication failed")
    pos = 56 + 2  # skip CRLF
    pos += 1      # CMD byte (0x01 CONNECT) — real clients always send it
    atyp = chunk[pos]
    pos += 1
    if atyp == ATYP_IPV4:
        address = ".".join(str(b) for b in chunk[pos:pos + 4])
        pos += 4
    elif atyp == ATYP_DOMAIN:
        dlen = chunk[pos]
        pos += 1
        address = chunk[pos:pos + dlen].decode("utf-8", errors="ignore")
        pos += dlen
    elif atyp == ATYP_IPV6:
        ab = chunk[pos:pos + 16]
        pos += 16
        address = ":".join(f"{ab[i]:02x}{ab[i + 1]:02x}" for i in range(0, 16, 2))
    else:
        raise ValueError(f"unknown address type: {atyp}")
    port = int.from_bytes(chunk[pos:pos + 2], "big")
    pos += 2
    if chunk[pos:pos + 2] != b"\r\n":
        raise ValueError("malformed request terminator")
    pos += 2
    return address, port, chunk[pos:]


def build_trojan_request(password: str, address: str, port: int, payload: bytes = b"") -> bytes:
    """Client-side encoder used by the integration tests (wire-correct: CMD byte)."""
    try:
        import ipaddress

        ip = ipaddress.ip_address(address)
        if ip.version == 4:
            addr = b"\x01" + ip.packed
        else:
            addr = b"\x04" + ip.packed
    except ValueError:
        host = address.encode()
        addr = b"\x03" + bytes([len(host)]) + host
    return (
        trojan_password_hash(password).encode()
        + b"\r\n"
        + b"\x01"          # CMD CONNECT
        + addr
        + port.to_bytes(2, "big")
        + b"\r\n"
        + payload
    )


async def trojan_ws_tunnel(ctx: RelayContext, ws: WebSocket) -> None:
    await ws.accept()

    # All trojan links share the /trojan-ws endpoint; the password in the
    # first frame identifies the link (same model as RVG).
    try:
        first_msg = await asyncio.wait_for(ws.receive(), timeout=ctx.cfg.ws_handshake_timeout)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        return
    if first_msg["type"] == "websocket.disconnect":
        return
    first_chunk = first_msg.get("bytes") or (first_msg.get("text") or "").encode()
    if not first_chunk:
        return

    # Resolve the link by password hash. Wire-compatible with RVG: the trojan
    # password is the link UUID, hashed with SHA-224 on the wire (cached).
    link = None
    candidates = ctx.links.snapshot()
    for candidate in candidates.values():
        if candidate.protocol != "trojan-ws" or not candidate.is_allowed():
            continue
        if trojan_password_hash(candidate.uuid) == first_chunk[:56].decode("ascii", errors="ignore").lower():
            link = candidate
            break
    if link is None:
        log.info("trojan handshake rejected (unknown password or disabled link)")
        await ws.close(code=1008, reason="not authorized")
        return

    try:
        address, port, payload = parse_trojan_request(
            first_chunk, trojan_password_hash(link.uuid)
        )
    except (ValueError, PermissionError) as exc:
        ctx.stats.add_error(f"trojan handshake: {exc}")
        await ws.close(code=1008, reason="bad request")
        return

    ip = ws_client_ip(ws)
    conn_id = secrets.token_urlsafe(6)
    ctx.connections.register(conn_id, uuid=link.uuid, ip=ip, transport="trojan-ws")
    log.info("trojan ws open [%s] ip=%s active=%d", conn_id, ip, ctx.connections.count())

    gate = QuotaGate(ctx, link.uuid, conn_id)
    if not await gate.add(len(first_chunk)):
        await ws.close(code=1008, reason="quota exceeded or link disabled")
        return
    ctx.stats.add_request()

    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(address, port), timeout=ctx.cfg.upstream_connect_timeout
        )
        tune_socket(writer, ctx.cfg.sock_buf)
        if payload:
            writer.write(payload)
            await writer.drain()

        done, pending = await asyncio.wait(
            {
                asyncio.create_task(pump_ws_to_tcp(ctx, ws, writer, gate, ctx.cfg.write_high_water)),
                # Trojan has no response prefix (unlike VLESS's \x00\x00).
                asyncio.create_task(
                    pump_tcp_to_ws(ctx, ws, reader, gate, ctx.cfg.relay_buf, first_payload_prefix=None)
                ),
            },
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        ctx.schedule_save()

    except asyncio.TimeoutError:
        ctx.stats.add_error("upstream connect timeout (trojan)")
    except Exception as exc:
        ctx.stats.add_error(str(exc))
        log.error("trojan ws error [%s]: %s", conn_id, exc)
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        ctx.connections.remove(conn_id)
        log.info("trojan ws closed [%s] active=%d", conn_id, ctx.connections.count())
