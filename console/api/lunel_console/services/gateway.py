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


FRIENDLY_404 = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lunel</title>
<style>body{{background:#0a0c10;color:#e7ebf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.c{{max-width:420px;text-align:center;padding:28px;border:1px solid #1e2430;border-radius:12px;background:#12151c}}
h2{{margin:0 0 8px}}p{{color:#9aa4b8;font-size:13.5px;line-height:1.55}}
code{{background:#10131a;border:1px solid #2a3242;border-radius:6px;padding:1px 6px;font-size:12px}}</style></head>
<body><div class="c"><h2>{title}</h2><p>{body}</p></div></body></html>"""


def _page(title: str, body: str, status: int = 200) -> "HTMLResponse":
    from fastapi.responses import HTMLResponse

    return HTMLResponse(FRIENDLY_404.format(title=title, body=body), status_code=status)


router = APIRouter(include_in_schema=False)


async def _resolve_endpoint(request: Request, token: str) -> dict | None:
    """endpoint token -> {instance_id, worker_url, status}"""
    pool = get_pool(request)
    row = await pool.fetchrow(
        """
        SELECT i.id, i.status,
               (SELECT d.domain FROM domains d WHERE d.instance_id = i.id
                 AND d.is_active = TRUE AND d.kind = 'path'
                 ORDER BY d.created_at DESC LIMIT 1) AS endpoint_token,
               (SELECT dep.node_id FROM deployments dep WHERE dep.instance_id = i.id
                 ORDER BY dep.started_at DESC LIMIT 1) AS node_id
        FROM instances i
        WHERE i.id IN (SELECT instance_id FROM domains
                        WHERE kind = 'path' AND domain = $1 AND is_active = TRUE)
        """,
        token,
    )
    if row is not None and row["endpoint_token"] is None:
        row = None
    if row is None or row["status"] != "running":
        return None
    from ..services.workers import worker_url_for

    return {
        "instance_id": str(row["id"]),
        "worker_url": worker_url_for(row["node_id"] or "local"),
        "upstream": f"/worker/api/instances/{row['id']}/proxy",
    }


@router.get("/i/{token}")
async def instance_status_page(token: str, request: Request):
    """Browser-friendly view of a proxy endpoint (the path itself is for
    proxy clients, not people)."""
    target = await _resolve_endpoint(request, token)
    if target is None:
        return _page(
            "Endpoint not found",
            "This endpoint doesn't exist or its instance was removed. "
            "If you recently redeployed Lunel without a persistent volume, "
            "create a new instance in the panel and copy its fresh config "
            "from the <b>Config</b> tab.",
            status=404,
        )
    return _page(
        "This endpoint is live",
        "This address is the private transport path for your proxy client — "
        "there is no web page here. Open the Lunel panel, choose your "
        "instance, open the <b>Config</b> tab and copy the "
        "<code>vless://</code> link into your client (v2rayNG, NekoBox, "
        "Streisand, …).",
    )


@router.get("/i/{token}/sub")
async def instance_subscription(token: str, request: Request):
    """Subscription: ALL protocols of this instance as a base64 client-import
    body (v2rayNG / NekoBox → import from URL). Auth is the endpoint token
    itself; the content host comes from ?host=, the panel-announced public
    host, or the request host — in that order."""
    import base64 as _b64

    import httpx as _httpx

    from ..config import settings as _settings

    target = await _resolve_endpoint(request, token)
    if target is None:
        return _page(
            "Endpoint not found",
            "This subscription doesn't exist or its instance was removed. "
            "Create a new instance in the Lunel panel and copy its "
            "subscription URL from the Config tab.",
            status=404,
        )
    pool = get_pool(request)
    inst = await pool.fetchrow(
        "SELECT name, public_host, status FROM instances WHERE id = $1", target["instance_id"]
    )
    if inst is None or inst["status"] != "running":
        return _page("Instance not running",
                     "The subscription will work once the instance is running.", status=503)
    host = (request.query_params.get("host")
            or inst["public_host"]
            or (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
            or request.headers.get("host") or "").split(":")[0]
    if not host:
        return _page("Missing host", "Append ?host=<your-domain> to this URL.", status=400)
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{target['worker_url'].rstrip('/')}{target['upstream']}/core/api/share",
                json={"host": host, "path_prefix": f"/i/{token}", "uuids": []},
                headers={"Authorization": f"Bearer {_settings.worker_token}",
                         "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            links = [c["share_url"] for c in resp.json().get("links", []) if c.get("share_url")]
    except Exception as exc:
        return _page("Unavailable", f"Could not read the instance configs: {str(exc)[:160]}",
                     status=502)
    body = _b64.b64encode("\n".join(links).encode()).decode()
    title = _b64.b64encode(f"Lunel · {inst['name']}".encode()).decode()
    from fastapi.responses import Response as _Response

    return _Response(
        content=body, media_type="text/plain",
        headers={
            "profile-title": f"base64:{title}",
            "subscription-userinfo": "upload=0; download=0; total=0; expire=0",
            "profile-update-interval": "24",
        },
    )


@router.api_route("/i/{token}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def instance_http_gateway(token: str, path: str, request: Request):
    target = await _resolve_endpoint(request, token)
    if target is None:
        return _page(
            "Endpoint not found",
            "This endpoint doesn't exist or its instance is not running. "
            "Check the panel — if the instance is Running, copy the fresh "
            "config from its <b>Config</b> tab.",
            status=404,
        )

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
