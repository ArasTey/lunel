"""Instance endpoint gateway.

On platforms that expose a single public HTTP endpoint per deployment
(e.g. Lucity's workload domain with Gateway API HTTPRoutes), every Lunel
instance is reached through the Console's public URL under a private
endpoint token:

    https://<console-host>/i/<endpoint-token>/<core-path>

The gateway authenticates by endpoint token (un guessable, rotatable),
strips the prefix, and proxies HTTP and WebSocket traffic to the instance's
Lunel Core through the Worker. On self-hosted deployments with wildcard DNS
the same instances can additionally be exposed as real hostnames via the
bundled Caddy — both modes share this proxy path.

WebSocket proxying is implemented frame-by-frame (client ⇄ gateway ⇄ core)
because the standard HTTP client stack cannot pass an Upgrade through.
"""
from __future__ import annotations

import asyncio
import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from ..db import get_pool
from ..logging import get

log = get("network", "lunel.console.gateway")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "authorization",  # replaced with the worker token below
}

router = APIRouter(include_in_schema=False)


async def _resolve_endpoint(request: Request, token: str) -> dict | None:
    """endpoint token -> {instance_id, worker_url, status}"""
    pool = get_pool(request)
    row = await pool.fetchrow(
        """
        SELECT i.id, i.status, d.domain, w.node_id
        FROM instances i
        JOIN domains d ON d.instance_id = i.id AND d.is_active = TRUE
        LEFT JOIN LATERAL (
            SELECT node_id FROM deployments WHERE instance_id = i.id
            ORDER BY started_at DESC LIMIT 1
        ) dep ON TRUE
        LEFT JOIN workers w ON w.node_id = COALESCE(dep.node_id, $2)
        WHERE d.kind = 'path' AND d.domain = $1
        """,
        token, "local",
    )
    if row is None or row["status"] != "running":
        return None
    from ..services.workers import worker_url_for

    return {
        "instance_id": str(row["id"]),
        "worker_url": worker_url_for(row["node_id"] or "local"),
        "upstream": f"/worker/api/instances/{row['id']}/proxy",
    }


@router.api_route("/i/{token}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def instance_http_gateway(token: str, path: str, request: Request):
    target = await _resolve_endpoint(request, token)
    if target is None:
        raise HTTPException(status_code=404, detail="unknown or inactive instance endpoint")

    worker_url = target["worker_url"].rstrip("/")
    url = f"{worker_url}{target['upstream']}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP]
    headers.append(("X-Lunel-Endpoint", token))
    from ..config import settings as _cfg

    headers.append(("Authorization", f"Bearer {_cfg.worker_token}"))

    client = httpx.AsyncClient(timeout=None)
    try:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            upstream_req = client.build_request(
                request.method, url, headers=headers, params=None,
            )
            upstream_resp = await client.send(upstream_req, stream=True)
            return StreamingResponse(
                upstream_resp.aiter_raw(),
                status_code=upstream_resp.status_code,
                headers={k: v for k, v in upstream_resp.headers.items()
                         if k.lower() not in HOP_BY_HOP},
                background=_close_client(client, upstream_resp),
            )

        body = await request.body()
        upstream_resp = await client.request(request.method, url, headers=headers, content=body)
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers={k: v for k, v in upstream_resp.headers.items()
                     if k.lower() not in HOP_BY_HOP},
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="instance upstream unavailable")


def _close_client(client: httpx.AsyncClient, resp):
    from starlette.background import BackgroundTask

    async def _cleanup() -> None:
        await resp.aclose()
        await client.aclose()

    return BackgroundTask(_cleanup)


@router.websocket("/i/{token}/{path:path}")
async def instance_ws_gateway(ws: WebSocket, token: str, path: str):
    """Frame-level WebSocket relay into the instance's Lunel Core.

    Accept the client up front (so failures produce proper close codes, not
    Starlette's HTTP 403 rejection), then open the upstream through the
    worker's ws-proxy and pump frames in both directions.
    """
    await ws.accept()
    try:
        target = await _resolve_endpoint(ws, token)
    except Exception:
        target = None
    if target is None:
        await ws.close(code=1008, reason="unknown or inactive instance endpoint")
        return

    # Build the upstream ws URL through the worker's websocket proxy
    # (separate route from the HTTP /proxy path).
    worker_ws = target["worker_url"].replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    from ..config import settings as _settings

    upstream_url = (
        f"{worker_ws}/worker/api/instances/{target['instance_id']}/ws-proxy/{path}"
        f"?token={_settings.worker_token}"
    )

    # Forward selected client headers so Core sees the real client IP etc.
    client_headers = {}
    for key in ("x-forwarded-for", "x-real-ip", "user-agent"):
        val = ws.headers.get(key)
        if val:
            client_headers[key] = val
    client_headers["x-lunel-endpoint"] = token

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=client_headers,  # type: ignore[arg-type]
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as upstream:
            async def client_to_upstream() -> None:
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            return
                        data = msg.get("bytes")
                        if data is not None:
                            await upstream.send(data)
                        else:
                            text = msg.get("text")
                            if text is not None:
                                await upstream.send(text)
                except (WebSocketDisconnect, Exception):
                    return

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, (bytes, bytearray)):
                            await ws.send_bytes(bytes(message))
                        else:
                            await ws.send_text(message)
                except Exception:
                    return

            done, pending = await asyncio.wait(
                {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except (websockets.exceptions.WebSocketException, OSError) as exc:
        log.info("gateway ws upstream failed: %s", type(exc).__name__)
        await ws.close(code=1014, reason="upstream unavailable")
        return
    finally:
        try:
            await ws.close()
        except Exception:
            pass
