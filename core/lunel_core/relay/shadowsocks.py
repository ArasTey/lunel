"""Shadowsocks AEAD over WebSocket (``/ss-ws``).

Crypto and tunnel logic ported from RVG's protocol/shadowsocks:
EVP_BytesToKey password derivation, HKDF-SHA1 subkey, streaming AEAD chunks
with little-endian nonce counters, and SOCKS5-like target addressing. Link
identification is by successful decryption (SS sends no header credential).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from fastapi import WebSocket, WebSocketDisconnect

from ..logging import get
from .base import QuotaGate, RelayContext, tune_socket, ws_client_ip

log = get("network", "lunel.relay.shadowsocks")

CIPHERS: dict[str, dict] = {
    "chacha20-ietf-poly1305": {"key_len": 32, "salt_len": 32, "nonce_len": 12, "cls": ChaCha20Poly1305},
    "aes-256-gcm": {"key_len": 32, "salt_len": 32, "nonce_len": 12, "cls": AESGCM},
}
DEFAULT_CIPHER = "chacha20-ietf-poly1305"


def derive_key(password: str, key_len: int) -> bytes:
    """EVP_BytesToKey (shadowsocks-compatible master key derivation)."""
    md5 = lambda b: hashlib.md5(b).digest()  # noqa: E731 - protocol requirement
    d = d_prev = b""
    while len(d) < key_len:
        d_prev = md5(d_prev + password.encode())
        d += d_prev
    return d[:key_len]


def _hkdf_sha1(key: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    prk = hmac.HMAC(salt, key, hashlib.sha1).digest()
    okm, t, i = b"", b"", 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha1).digest()
        okm += t
        i += 1
    return okm[:length]


def _subkey(master_key: bytes, salt: bytes, key_len: int) -> bytes:
    return _hkdf_sha1(master_key, salt, b"ss-subkey", key_len)


class AEADStream:
    """Streaming AEAD chunk codec: chunk = enc(2B length)+tag + enc(payload)+tag."""

    def __init__(self, master_key: bytes, cipher_name: str):
        info = CIPHERS[cipher_name]
        self.key_len = info["key_len"]
        self.salt_len = info["salt_len"]
        self.nonce_len = info["nonce_len"]
        self.cipher_cls = info["cls"]

        self.enc_salt = secrets.token_bytes(self.salt_len)
        self.enc_key = _subkey(master_key, self.enc_salt, self.key_len)
        self.enc_aead = self.cipher_cls(self.enc_key)
        self.enc_nonce = 0
        self.enc_salt_sent = False

        self.dec_aead = None
        self.dec_nonce = 0
        self._buf = bytearray()

    def _nonce(self, counter: int) -> bytes:
        return counter.to_bytes(self.nonce_len, "little")

    def encrypt_chunk(self, payload: bytes) -> bytes:
        out = bytearray()
        if not self.enc_salt_sent:
            out += self.enc_salt
            self.enc_salt_sent = True
        enc_len = self.enc_aead.encrypt(self._nonce(self.enc_nonce), struct.pack(">H", len(payload)), None)
        self.enc_nonce += 1
        enc_payload = self.enc_aead.encrypt(self._nonce(self.enc_nonce), payload, None)
        self.enc_nonce += 1
        out += enc_len + enc_payload
        return bytes(out)

    def feed(self, data: bytes) -> None:
        self._buf += data

    def _ensure_dec_key(self) -> bool:
        if self.dec_aead is not None:
            return True
        if len(self._buf) < self.salt_len:
            return False
        dec_salt = bytes(self._buf[: self.salt_len])
        del self._buf[: self.salt_len]
        self.dec_aead = self.cipher_cls(_subkey(derive_key_from(self.master_key_ref, dec_salt), dec_salt, self.key_len)) \
            if False else self.cipher_cls(_subkey(self._master_key, dec_salt, self.key_len))
        return True

    def try_decrypt_chunks(self):
        if not self._ensure_dec_key():
            return
        tag = 16
        while True:
            if len(self._buf) < 2 + tag:
                return
            try:
                length_bytes = self.dec_aead.decrypt(
                    self._nonce(self.dec_nonce), bytes(self._buf[: 2 + tag]), None
                )
            except Exception:
                raise ValueError("AEAD decrypt (length) failed - bad key/tag")
            self.dec_nonce += 1
            length = struct.unpack(">H", length_bytes)[0]
            if length > 0x3FFF:
                raise ValueError("invalid SS AEAD chunk length (exceeds spec limit)")
            need = 2 + tag + length + tag
            if len(self._buf) < need:
                return
            del self._buf[: 2 + tag]
            enc_payload = bytes(self._buf[: length + tag])
            del self._buf[: length + tag]
            try:
                payload = self.dec_aead.decrypt(self._nonce(self.dec_nonce), enc_payload, None)
            except Exception:
                raise ValueError("AEAD decrypt (payload) failed - bad key/tag")
            self.dec_nonce += 1
            yield payload

    # master key storage --------------------------------------------------
    def set_master_key(self, key: bytes) -> None:
        self._master_key = key


def derive_key_from(password: str, salt: bytes) -> bytes:  # pragma: no cover - removed helper
    raise NotImplementedError


def parse_socks5_addr(buf: bytes) -> tuple[str, int, int]:
    """ATYP(1)+ADDR+PORT(2) -> (address, port, consumed)."""
    if len(buf) < 2:
        raise ValueError("too short")
    atyp = buf[0]
    pos = 1
    if atyp == 1:
        if len(buf) < pos + 4 + 2:
            raise ValueError("short ipv4")
        address = ".".join(str(b) for b in buf[pos:pos + 4])
        pos += 4
    elif atyp == 3:
        dlen = buf[pos]
        pos += 1
        if len(buf) < pos + dlen + 2:
            raise ValueError("short domain")
        address = buf[pos:pos + dlen].decode("utf-8", errors="ignore")
        pos += dlen
    elif atyp == 4:
        if len(buf) < pos + 16 + 2:
            raise ValueError("short ipv6")
        ab = buf[pos:pos + 16]
        pos += 16
        address = ":".join(f"{ab[i]:02x}{ab[i + 1]:02x}" for i in range(0, 16, 2))
    else:
        raise ValueError(f"unknown atyp {atyp}")
    port = int.from_bytes(buf[pos:pos + 2], "big")
    pos += 2
    return address, port, pos


def generate_ss_link(host: str, port: int, cipher: str, password: str, remark: str,
                     path_prefix: str = "") -> str:
    """ss://base64(method:password)@host:port with v2ray-plugin (ws+tls)."""
    import base64
    from urllib.parse import quote

    userinfo = base64.urlsafe_b64encode(f"{cipher}:{password}".encode()).decode().rstrip("=")
    p = path_prefix.rstrip("/")
    plugin = quote(f"v2ray-plugin;tls;mux=0;path={p}/ss-ws;host={host}")
    return f"ss://{userinfo}@{host}:{port}/?plugin={plugin}#{quote(remark)}"


async def _find_matching_ss_link(ctx: RelayContext, first_bytes: bytes):
    """SS carries no header credential; identify the link by successful AEAD
    decryption against each active shadowsocks link (same as RVG)."""
    candidates = ctx.links.snapshot()
    for link in candidates.values():
        if link.protocol != "shadowsocks" or not link.is_allowed():
            continue
        cipher_name = link.ss_cipher or DEFAULT_CIPHER
        info = CIPHERS.get(cipher_name)
        if not info:
            continue
        master_key = derive_key(link.ss_password or "", info["key_len"])
        stream = AEADStream(master_key, cipher_name)
        stream.set_master_key(master_key)
        stream.feed(first_bytes)
        try:
            chunks = list(stream.try_decrypt_chunks())
        except ValueError:
            continue
        if chunks:
            return link, stream, chunks
    return None, None, None


async def shadowsocks_ws_tunnel(ctx: RelayContext, ws: WebSocket) -> None:
    await ws.accept()
    ip = ws_client_ip(ws)
    conn_id = secrets.token_urlsafe(6)
    writer = None
    link = None

    try:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + ctx.cfg.ws_handshake_timeout
        raw = bytearray()

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                log.info("ss handshake timeout [%s] ip=%s", conn_id, ip)
                await ws.close(code=1008, reason="handshake timeout")
                return
            msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
            if msg["type"] == "websocket.disconnect":
                return
            chunk = msg.get("bytes") or (msg.get("text") or "").encode()
            if not chunk:
                continue
            raw += chunk
            if len(raw) > 64 * 1024:
                await ws.close(code=1008, reason="handshake too large")
                return
            link, stream, chunks = await _find_matching_ss_link(ctx, bytes(raw))
            if link is not None:
                break

        if link is None:
            log.info("ss rejected [%s] ip=%s (no matching key)", conn_id, ip)
            await ws.close(code=1008, reason="not authorized")
            return

        first_chunk = bytes(raw)
        ctx.connections.register(conn_id, uuid=link.uuid, ip=ip, transport="shadowsocks-ws")
        log.info("ss open [%s] uuid=%s ip=%s", conn_id, link.uuid[:8], ip)

        first_payload = chunks[0]
        address, port, hlen = parse_socks5_addr(first_payload)
        initial_data = first_payload[hlen:]
        extra_chunks = chunks[1:]

        gate = QuotaGate(ctx, link.uuid, conn_id)
        if not await gate.add(len(first_chunk)):
            await ws.close(code=1008, reason="quota exceeded or link disabled")
            return
        ctx.stats.add_request()
        log.info("[%s] ss -> %s:%d", conn_id, address, port)

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(address, port), timeout=ctx.cfg.upstream_connect_timeout
        )
        tune_socket(writer, ctx.cfg.sock_buf)

        if initial_data:
            writer.write(initial_data)
        for c in extra_chunks:
            if c:
                writer.write(c)
        if initial_data or extra_chunks:
            await writer.drain()

        async def pump_decrypt_to_tcp():
            try:
                while True:
                    msg = await ws.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    data = msg.get("bytes") or (msg.get("text") or "").encode()
                    if not data:
                        continue
                    stream.feed(data)
                    try:
                        for payload in stream.try_decrypt_chunks():
                            if not payload:
                                continue
                            if not await gate.add(len(payload)):
                                await ws.close(code=1008, reason="quota exceeded or link disabled")
                                return
                            ctx.stats.add_request()
                            writer.write(payload)
                    except ValueError:
                        await ws.close(code=1008, reason="bad aead frame")
                        return
                    if writer.transport.get_write_buffer_size() > ctx.cfg.write_high_water:
                        await writer.drain()
            except (WebSocketDisconnect, Exception):
                pass
            finally:
                await gate.flush()
                try:
                    writer.write_eof()
                except Exception:
                    pass

        async def pump_encrypt_to_ws():
            try:
                while True:
                    data = await reader.read(ctx.cfg.relay_buf)
                    if not data:
                        break
                    if not await gate.add(len(data)):
                        await ws.close(code=1008, reason="quota exceeded or link disabled")
                        break
                    await ws.send_bytes(stream.encrypt_chunk(data))
            except Exception:
                pass
            finally:
                await gate.flush()

        done, pending = await asyncio.wait(
            {
                asyncio.create_task(pump_decrypt_to_tcp()),
                asyncio.create_task(pump_encrypt_to_ws()),
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
        ctx.stats.add_error("shadowsocks connection timeout")
    except Exception as exc:
        ctx.stats.add_error(str(exc))
        log.error("ss error [%s]: %s", conn_id, exc)
    finally:
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        ctx.connections.remove(conn_id)
        log.info("ss closed [%s] active=%d", conn_id, ctx.connections.count())
