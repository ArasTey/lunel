"""Share-link generation for proxied links (client import URLs).

Ported from RVG's generate_share_link, restricted to the protocols Lunel
Core supports (MTProto is not part of Lunel Core v1).
"""
from __future__ import annotations

import base64
from urllib.parse import quote

from .relay.shadowsocks import DEFAULT_CIPHER, generate_ss_link
from .state import Link


def _with_prefix(link: Link, prefix: str) -> Link:
    clone = Link(
        uuid=link.uuid, label=link.label, protocol=link.protocol, active=link.active,
        limit_bytes=link.limit_bytes, used_bytes=link.used_bytes,
        created_at=link.created_at, expires_at=link.expires_at, note=link.note,
        alpn=link.alpn, fingerprint=link.fingerprint,
        ss_cipher=link.ss_cipher, ss_password=link.ss_password,
    )
    return clone


def generate_share_link(link: Link, host: str, remark_prefix: str = "Lunel",
                        path_prefix: str = "") -> str:
    """Build a client import URL. ``path_prefix`` (e.g. ``/i/<token>``) is
    prepended to every transport path so the link routes through the
    Console's public endpoint on platforms exposing a single domain."""
    remark = f"{remark_prefix}-{link.label}"
    p = path_prefix.rstrip("/")
    proto = link.protocol
    # ALPN per transport: WebSocket needs HTTP/1.1-only (h2 breaks the WS
    # upgrade through CDN edges); xHTTP is HTTP-native and wants h2 first.
    if "xhttp" in proto:
        alpn = "h2,http/1.1"
    else:
        alpn = "http/1.1"
    from urllib.parse import quote as _qa

    alpn = _qa(alpn, safe="")  # emit as alpn=h2%2Chttp%2F1.1 like real clients

    if proto == "shadowsocks":
        password = link.ss_password or ""
        cipher = link.ss_cipher or DEFAULT_CIPHER
        return generate_ss_link(host, 443, cipher, password, remark, path_prefix=p)

    # xray clients percent-encode the path query value (path=%2F...) — match
    # their emission so strict parsers import cleanly.
    from urllib.parse import quote as _q

    from urllib.parse import quote as _qp

    if proto == "trojan-ws":
        params = {
            "security": "tls", "type": "ws", "host": host,
            "path": _qp(f"{p}/trojan-ws", safe=""), "sni": host, "fp": link.fingerprint, "alpn": alpn,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        port_part = "" if ":" in host else ":443"
        return f"trojan://{link.uuid}@{host}{port_part}?{query}#{quote(remark)}"

    if proto.startswith("trojan-xhttp-"):
        mode = proto.replace("trojan-xhttp-", "")
        path = _qp(f"{p}/txhttp-siz10/{mode}/{link.uuid}", safe="")
        params = {
            "security": "tls", "type": "xhttp", "mode": mode, "host": host,
            "path": path, "sni": host, "fp": link.fingerprint, "alpn": alpn,
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        port_part = "" if ":" in host else ":443"
        return f"trojan://{link.uuid}@{host}{port_part}?{query}#{quote(remark)}"

    if proto == "vless-ws":
        path = _qp(f"{p}/ws/{link.uuid}", safe="")
        params = {
            "encryption": "none", "security": "tls", "type": "ws", "host": host,
            "path": path, "sni": host, "fp": link.fingerprint, "alpn": alpn,
        }
    else:
        mode = proto.replace("xhttp-", "") if proto.startswith("xhttp-") else "packet-up"
        path = _qp(f"{p}/xhttp-siz10/{mode}/{link.uuid}", safe="")
        params = {
            "encryption": "none", "security": "tls", "type": "xhttp", "mode": mode,
            "host": host, "path": path, "sni": host, "fp": link.fingerprint, "alpn": alpn,
        }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    port_part = "" if ":" in host else ":443"
    return f"vless://{link.uuid}@{host}{port_part}?{query}#{quote(remark)}"


def subscription_payload(links: list[Link], host: str, title: str = "Lunel") -> tuple[str, dict]:
    """Base64 subscription body + response headers (profile metadata)."""
    lines = [generate_share_link(link, host) for link in links]
    content = base64.b64encode("\n".join(lines).encode()).decode()
    used = sum(l.used_bytes for l in links)
    total = sum(l.limit_bytes for l in links)
    title_b64 = base64.b64encode(title.encode()).decode()
    headers = {
        "profile-title": f"base64:{title_b64}",
        "subscription-userinfo": f"upload=0; download={used}; total={total}; expire=0",
        "profile-update-interval": "6",
    }
    return content, headers
