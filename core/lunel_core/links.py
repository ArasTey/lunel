"""Share-link generation for proxied links (client import URLs).

Ported from RVG's generate_share_link, restricted to the protocols Lunel
Core supports (MTProto is not part of Lunel Core v1).
"""
from __future__ import annotations

import base64
from urllib.parse import quote

from .relay.shadowsocks import DEFAULT_CIPHER, generate_ss_link
from .state import Link


def generate_share_link(link: Link, host: str, remark_prefix: str = "Lunel") -> str:
    remark = f"{remark_prefix}-{link.label}"
    proto = link.protocol

    if proto == "shadowsocks":
        password = link.ss_password or ""
        cipher = link.ss_cipher or DEFAULT_CIPHER
        return generate_ss_link(host, 443, cipher, password, remark)

    if proto == "trojan-ws":
        params = {
            "security": "tls", "type": "ws", "host": host,
            "path": "/trojan-ws", "sni": host, "fp": link.fingerprint, "alpn": link.alpn,
        }
        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return f"trojan://{link.uuid}@{host}:443?{query}#{quote(remark)}"

    if proto.startswith("trojan-xhttp-"):
        mode = proto.replace("trojan-xhttp-", "")
        path = f"/txhttp-siz10/{mode}/{link.uuid}"
        params = {
            "security": "tls", "type": "xhttp", "mode": mode, "host": host,
            "path": path, "sni": host, "fp": link.fingerprint, "alpn": link.alpn,
        }
        query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
        return f"trojan://{link.uuid}@{host}:443?{query}#{quote(remark)}"

    if proto == "vless-ws":
        path = f"/ws/{link.uuid}"
        params = {
            "encryption": "none", "security": "tls", "type": "ws", "host": host,
            "path": path, "sni": host, "fp": link.fingerprint, "alpn": link.alpn,
        }
    else:
        mode = proto.replace("xhttp-", "") if proto.startswith("xhttp-") else "packet-up"
        path = f"/xhttp-siz10/{mode}/{link.uuid}"
        params = {
            "encryption": "none", "security": "tls", "type": "xhttp", "mode": mode,
            "host": host, "path": path, "sni": host, "fp": link.fingerprint, "alpn": link.alpn,
        }
    query = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return f"vless://{link.uuid}@{host}:443?{query}#{quote(remark)}"


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
