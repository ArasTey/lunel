"""VLESS over WebSocket (``/ws/{uuid}``).

Protocol handling ported from RVG's protocol/vless (vless.py + websocket.py);
logic unchanged, boundaries cleaned up.
"""
from __future__ import annotations

import asyncio
import secrets

from fastapi import WebSocket, WebSocketDisconnect

from ..logging import get
from ..version import info as version_info
from .base import (
    QuotaGate,
    RelayContext,
    pump_tcp_to_ws,
    pump_ws_to_tcp,
    tune_socket,
    ws_client_ip,
)

log = get("network", "lunel.relay.vless")


def parse_vless_header(chunk: bytes) -> tuple[int, str, int, bytes]:
    """Parse a VLESS request header. Returns (command, address, port, rest)."""
    if len(chunk) < 24:
        raise ValueError("chunk too small")
    pos = 1 + 16  # version byte + 16-byte uuid (validated by lookup)
    addon_len = chunk[pos]
    pos += 1 + addon_len
    command = chunk[pos]
    pos += 1
    port = int.from_bytes(chunk[pos:pos + 2], "big")
    pos += 2
    addr_type = chunk[pos]
    pos += 1
    if addr_type == 1:
        address = ".".join(str(b) for b in chunk[pos:pos + 4])
        pos += 4
    elif addr_type == 2:
        dlen = chunk[pos]
        pos += 1
        address = chunk[pos:pos + dlen].decode("utf-8", errors="ignore")
        pos += dlen
    elif addr_type == 3:
        ab = chunk[pos:pos + 16]
        pos += 16
        address = ":".join(f"{ab[i]:02x}{ab[i + 1]:02x}" for i in range(0, 16, 2))
    else:
        raise ValueError(f"unknown address type: {addr_type}")
    return command, address, port, chunk[pos:]


def build_vless_header(uuid: str, address: str, port: int, command: int = 1) -> bytes:
    """Inverse of ``parse_vless_header`` — used by the integration tests to
    speak the protocol as a real client would."""
    try:
        import ipaddress

        ip = ipaddress.ip_address(address)
        if ip.version == 4:
            addr = b"\x01" + ip.packed
        else:
            addr = b"\x03" + ip.packed
    except ValueError:
        host = address.encode()
        addr = b"\x02" + bytes([len(host)]) + host
    return (
        b"\x00"
        + bytes.fromhex(uuid.replace("-", ""))  # 16-byte credential
        + b"\x00"            # addon length
        + bytes([command])
        + port.to_bytes(2, "big")
        + addr
    )


async def vless_ws_tunnel(ctx: RelayContext, ws: WebSocket, uuid: str) -> None:
    await ws.accept()

    link = await ctx.links.get_allowed(uuid)
    if link is None:
        log.warning("ws rejected uuid=%s (unknown, disabled, expired or over quota)", uuid[:8])
        await ws.close(code=1008, reason="not authorized")
        return

    ip = ws_client_ip(ws)
    conn_id = secrets.token_urlsafe(6)
    ctx.connections.register(conn_id, uuid=uuid, ip=ip, transport="vless-ws")
    log.info("ws open [%s] uuid=%s ip=%s active=%d", conn_id, uuid[:8], ip, ctx.connections.count())

    writer = None
    try:
        first_msg = await asyncio.wait_for(ws.receive(), timeout=ctx.cfg.ws_handshake_timeout)
        if first_msg["type"] == "websocket.disconnect":
            return
        first_chunk = first_msg.get("bytes") or (first_msg.get("text") or "").encode()
        if not first_chunk:
            return

        # The 16 bytes after the version byte must equal the link UUID.
        if len(first_chunk) >= 17 and first_chunk[1:17] != bytes.fromhex(uuid.replace("-", "")):
            await ws.close(code=1008, reason="credential mismatch")
            return

        command, address, port, payload = parse_vless_header(first_chunk)

        gate = QuotaGate(ctx, uuid, conn_id)
        if not await gate.add(len(first_chunk)):
            await ws.close(code=1008, reason="quota exceeded or link disabled")
            return
        ctx.stats.add_request()
        log.info("[%s] -> %s:%d cmd=%d", conn_id, address, port, command)

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
                asyncio.create_task(
                    pump_tcp_to_ws(ctx, ws, reader, gate, ctx.cfg.relay_buf, first_payload_prefix=b"\x00\x00")
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

    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        ctx.stats.add_error("connection timeout (handshake or upstream connect)")
    except (ValueError, OSError) as exc:
        ctx.stats.add_error(str(exc))
        log.info("ws error [%s]: %s", conn_id, exc)
    except Exception as exc:
        ctx.stats.add_error(str(exc))
        log.error("ws error [%s]: %s", conn_id, exc)
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        ctx.connections.remove(conn_id)
        log.info("ws closed [%s] active=%d", conn_id, ctx.connections.count())
