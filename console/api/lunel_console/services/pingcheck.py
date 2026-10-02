"""External reachability check for a public domain.

Asks check-host.net to ping the domain from several regions and reports
whether Iran can reach it. The domain comes from the operator's own
instance configuration, so it is treated as untrusted input: only plain
public DNS names are accepted and anything that resolves to a loopback,
private, link-local or otherwise reserved address is refused before any
outbound request is made.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket

import httpx

CHECK_HOST = "https://check-host.net"
JSON_HEADERS = {"Accept": "application/json", "User-Agent": "Lunel/1.0"}
TIMEOUT = 12.0

# A bare public DNS name: letters, digits, dots and hyphens, at least one dot,
# no scheme, no credentials, no port, no path. Anything else is rejected.
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)"
    r"(?:\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))+$"
)


class PingCheckError(ValueError):
    """Raised when the requested host cannot be probed."""


def validate_public_host(raw: str) -> str:
    """Return a normalised public hostname or refuse it.

    Rejects loopback, private, link-local, multicast and reserved targets so
    the check can never be pointed at internal infrastructure.
    """
    host = (raw or "").strip().lower().rstrip(".")
    if not host:
        raise PingCheckError("host is required")
    if "://" in host or "/" in host or "@" in host:
        raise PingCheckError("send a bare domain name, not a URL")
    if _HOST_RE.match(host) is None:
        raise PingCheckError("not a valid domain name")
    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        raise PingCheckError("local hostnames cannot be checked")
    # Numeric lookalikes are ambiguous: some resolvers read 0177.0.0.1 as
    # 127.0.0.1 (octal) and others as 177.0.0.1. Only a strictly parseable
    # literal is allowed through this path.
    if host.replace(".", "").isdigit():
        raise PingCheckError("use a domain name, not a numeric address")
    if any(label.startswith("0x") for label in host.split(".")):
        raise PingCheckError("numeric host forms are not accepted")

    # A literal address is never a public target we should probe.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global:
            raise PingCheckError("only public IP addresses can be checked")
        return host

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise PingCheckError(f"domain does not resolve: {exc.strerror or exc}") from exc

    for info in infos:
        try:
            resolved = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not resolved.is_global:
            raise PingCheckError("domain resolves to a non-public address")
    return host


async def _start_check(client: httpx.AsyncClient, host: str) -> dict:
    resp = await client.get(f"{CHECK_HOST}/check-ping",
                            params={"host": host, "max_nodes": 5},
                            headers=JSON_HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("request_id"):
        raise PingCheckError("the check service did not return a request id")
    return data


async def _fetch_result(client: httpx.AsyncClient, request_id: str) -> dict:
    resp = await client.get(f"{CHECK_HOST}/check-result/{request_id}",
                            headers=JSON_HEADERS)
    resp.raise_for_status()
    return resp.json()


def _summarise(nodes: dict, results: dict) -> tuple[bool, list[dict], bool]:
    """Turn check-host's raw payload into per-node verdicts."""
    verdicts = []
    iran_ok = False
    pending = False
    for node, meta in (nodes or {}).items():
        country = (meta[0] if isinstance(meta, (list, tuple)) and meta else "") or "?"
        country_name = (meta[1] if isinstance(meta, (list, tuple)) and len(meta) > 1 else "") or ""
        entry = (results or {}).get(node)
        ok_count = 0
        total = 0
        best = None
        if entry is None:
            pending = True
        else:
            rows = entry[0] if isinstance(entry, (list, tuple)) and entry else []
            for row in rows if isinstance(rows, (list, tuple)) else []:
                if not isinstance(row, (list, tuple)) or not row:
                    continue
                total += 1
                if row[0] == "OK" and len(row) > 1 and isinstance(row[1], (int, float)):
                    ok_count += 1
                    best = row[1] if best is None else min(best, row[1])
        reachable = ok_count > 0
        if country.lower() == "ir" and reachable:
            iran_ok = True
        verdicts.append({
            "node": node,
            "country": country,
            "country_name": country_name,
            "ok": ok_count,
            "total": total,
            "reachable": reachable,
            "best_ms": round(best * 1000, 1) if best is not None else None,
        })
    if not iran_ok:
        # No Iranian probe reported yet: keep waiting rather than claiming failure.
        for v in verdicts:
            if v["country"].lower() == "ir" and v["total"] == 0:
                pending = True
    return iran_ok, verdicts, pending


async def check_reachability(host: str) -> dict:
    """Start a multi-region ping check and report the current verdict."""
    target = validate_public_host(host)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        started = await _start_check(client, target)
        nodes = started.get("nodes") or {}
        request_id = str(started["request_id"])
        # check-host answers results progressively; give the nodes a moment.
        results: dict = {}
        for attempt in range(3):
            results = await _fetch_result(client, request_id)
            _, verdicts, pending = _summarise(nodes, results)
            if not pending or attempt == 2:
                break
            await asyncio.sleep(1.5)
        iran_ok, verdicts, pending = _summarise(nodes, results)
    return {
        "host": target,
        "request_id": request_id,
        "reachable_from_iran": iran_ok,
        "pending": pending,
        "report_url": started.get("permanent_link") or "",
        "nodes": verdicts,
    }