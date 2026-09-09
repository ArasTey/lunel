"""FastAPI application assembly for Lunel Core.

Routes:
* Public (unauthenticated, used by health checks / the Console):
    GET /health   GET /ready   GET /version
* Protocol transports (unauthenticated by design; protocol-level credentials):
    WS   /ws/{uuid}            VLESS over WebSocket
    WS   /trojan-ws            Trojan over WebSocket
    WS   /ss-ws                Shadowsocks AEAD over WebSocket
    POST /xhttp-siz10/...      VLESS xHTTP (packet-up / stream-up / downlink)
    POST /txhttp-siz10/...     Trojan xHTTP
* Management API (Bearer token via LUNEL_CORE_API_TOKEN), consumed by the
  Lunel Worker on behalf of the Console:
    /core/api/links            CRUD for links
    /core/api/stats            traffic + connection counters
    /core/api/connections      live connections (grouped)
    /core/api/logs             recent runtime logs
    /core/api/metrics          cpu / memory (psutil when available)
    /core/api/state            persistence flush
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets

import psutil
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response

from . import version
from .config import CoreConfig
from .links import generate_share_link
from .logging import get, setup_logging
from .relay.base import RelayContext
from .relay.shadowsocks import shadowsocks_ws_tunnel
from .relay.trojan import trojan_ws_tunnel
from .relay.vless import vless_ws_tunnel
from .state import ConnectionTracker, Link, LinkStore, RuntimeStats, StateStore

log = get("runtime", "lunel.core")


class Core:
    """Wires config + state + relays together and exposes the ASGI app."""

    def __init__(self, cfg: CoreConfig):
        self.cfg = cfg
        self.links = LinkStore()
        self.connections = ConnectionTracker()
        self.stats = RuntimeStats()
        self.store = StateStore(cfg.state_path)
        self.ring = setup_logging(cfg.log_level, cfg.log_json)
        self.ctx = RelayContext(
            links=self.links,
            connections=self.connections,
            stats=self.stats,
            save_hook=self._schedule_save,
            cfg=cfg,
        )

        from .relay.xhttp import XHttpEngine

        self.vless_xhttp = XHttpEngine(self.ctx, prefix="/xhttp-siz10")
        self.trojan_xhttp = XHttpEngine(self.ctx, prefix="/txhttp-siz10")

        self.app = FastAPI(title="Lunel Core", docs_url=None, redoc_url=None,
                           version=version.version())
        self._register_routes()

    def _schedule_save(self) -> None:
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop().create_task(self.store.schedule_save(self.links, self.stats))

    # ---- auth dependency --------------------------------------------------
    def require_api_token(self, request: Request) -> None:
        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        expected = self.cfg.api_token
        if not expected:
            raise HTTPException(status_code=503, detail="management api disabled (no token configured)")
        if not token or not secrets.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    # ---- routes -----------------------------------------------------------
    def _register_routes(self) -> None:
        app = self.app

        # -- public health -------------------------------------------------
        @app.get("/health")
        async def health():
            return {
                "status": "ok",
                "service": version.APP_NAME,
                "version": version.version(),
                "connections": self.connections.count(),
                "uptime": self.stats.uptime_seconds(),
            }

        @app.get("/ready")
        async def ready():
            return {"ready": True}

        @app.get("/version")
        async def version_endpoint():
            return version.info()

        # -- protocol transports -------------------------------------------
        app.add_api_websocket_route("/ws/{uuid}", self._ws_vless)
        app.add_api_websocket_route("/trojan-ws", self._ws_trojan)
        app.add_api_websocket_route("/ss-ws", self._ws_shadowsocks)

        app.include_router(self.vless_xhttp.router)
        app.include_router(self.trojan_xhttp.router)

        # -- management API --------------------------------------------------
        guard = self.require_api_token

        @app.get("/core/api/stats")
        async def core_stats(_=Depends(guard)):
            totals = await self.links.totals()
            return {
                "active_connections": self.connections.count(),
                "total_bytes": self.stats.total_bytes,
                "total_requests": self.stats.total_requests,
                "total_errors": self.stats.total_errors,
                "uptime": self.stats.uptime_seconds(),
                "hourly": dict(self.stats.hourly),
                "recent_errors": list(self.stats.errors)[-10:],
                "links": totals,
            }

        @app.get("/core/api/connections")
        async def core_connections(_=Depends(guard)):
            grouped = self.connections.grouped_by_ip()
            return {"connections": grouped, "count": len(grouped), "raw_count": self.connections.count()}

        @app.get("/core/api/logs")
        async def core_logs(limit: int = 200, _=Depends(guard)):
            limit = max(1, min(limit, 500))
            return {"logs": self.ring.snapshot(limit)}

        @app.get("/core/api/metrics")
        async def core_metrics(_=Depends(guard)):
            data = await asyncio.to_thread(self._read_process_metrics)
            data["connections"] = self.connections.count()
            data["total_bytes"] = self.stats.total_bytes
            return data

        @app.get("/core/api/links")
        async def core_links_list(_=Depends(guard)):
            cfg_host = self.cfg.public_host or None
            out = []
            for link in self.links.snapshot().values():
                item = link.to_dict(include_secret=False)
                item["share_url"] = None
                if cfg_host:
                    item["share_url"] = generate_share_link(link, cfg_host)
                out.append(item)
            return {"links": out}

        @app.post("/core/api/links")
        async def core_links_create(request: Request, _=Depends(guard)):
            body = await request.json()
            link = self._link_from_body(body)
            await self.links.add(link)
            self._schedule_save()
            return {"ok": True, "uuid": link.uuid}

        @app.patch("/core/api/links/{uuid}")
        async def core_links_update(uuid: str, request: Request, _=Depends(guard)):
            body = await request.json()
            async with self.links.lock:
                link = self.links.get(uuid)
                if link is None:
                    raise HTTPException(status_code=404, detail="link not found")
                self._apply_link_patch(link, body)
            self._schedule_save()
            return {"ok": True}

        @app.delete("/core/api/links/{uuid}")
        async def core_links_delete(uuid: str, _=Depends(guard)):
            removed = await self.links.remove(uuid)
            if removed is None:
                raise HTTPException(status_code=404, detail="link not found")
            self._schedule_save()
            return {"ok": True}

        @app.post("/core/api/share")
        async def core_share(request: Request, _=Depends(guard)):
            """Client import URLs (vless:// / trojan:// / ss://) for all links,
            with an optional public path prefix for single-domain platforms."""
            body = await request.json()
            host = str(body.get("host") or "").strip() or (self.cfg.public_host or None)
            prefix = str(body.get("path_prefix") or "").strip()
            uuids = set(body.get("uuids") or [])
            if not host:
                raise HTTPException(status_code=400, detail="host required")
            from .links import generate_share_link

            out = []
            for link in self.links.snapshot().values():
                if uuids and link.uuid not in uuids:
                    continue
                if not link.is_allowed():
                    continue
                out.append({
                    "uuid": link.uuid,
                    "label": link.label,
                    "protocol": link.protocol,
                    "share_url": generate_share_link(link, host, path_prefix=prefix),
                })
            return {"links": out}

        @app.post("/core/api/state/flush")
        async def core_state_flush(_=Depends(guard)):
            await self.store.save(self.links, self.stats)
            return {"ok": True}

    # ---- helpers -----------------------------------------------------------
    def _link_from_body(self, body: dict) -> Link:
        from .config import DEFAULT_PROTOCOL, PROTOCOLS

        protocol = body.get("protocol") or DEFAULT_PROTOCOL
        if protocol not in PROTOCOLS:
            raise HTTPException(status_code=400, detail=f"unsupported protocol: {protocol}")
        import secrets as _secrets

        uuid = str(body.get("uuid") or _secrets.token_hex(16))
        # normalize into canonical uuid form when hex provided
        if len(uuid) == 32 and "-" not in uuid:
            uuid = f"{uuid[:8]}-{uuid[8:12]}-{uuid[12:16]}-{uuid[16:20]}-{uuid[20:32]}"
        link = Link(
            uuid=uuid,
            label=str(body.get("label") or "Link")[:80],
            protocol=protocol,
            active=bool(body.get("active", True)),
            limit_bytes=int(body.get("limit_bytes") or 0),
            used_bytes=int(body.get("used_bytes") or 0),
            expires_at=body.get("expires_at"),
            note=str(body.get("note") or "")[:300],
            alpn=str(body.get("alpn") or "h2,http/1.1")[:60],
            fingerprint=str(body.get("fingerprint") or "chrome"),
            ss_cipher=body.get("ss_cipher"),
            ss_password=body.get("ss_password"),
        )
        if link.protocol == "shadowsocks":
            link.ss_cipher = link.ss_cipher if link.ss_cipher in (
                "aes-256-gcm", "chacha20-ietf-poly1305") else "chacha20-ietf-poly1305"
            link.ss_password = link.ss_password or _secrets.token_urlsafe(16)
        return link

    def _apply_link_patch(self, link: Link, body: dict) -> None:
        if "label" in body:
            link.label = str(body["label"])[:80]
        if "active" in body:
            link.active = bool(body["active"])
        if "note" in body:
            link.note = str(body["note"])[:300]
        if "reset_usage" in body and body["reset_usage"]:
            link.used_bytes = 0
        if "limit_bytes" in body:
            link.limit_bytes = max(0, int(body["limit_bytes"] or 0))
        if "expires_at" in body:
            link.expires_at = body["expires_at"]

    def _read_process_metrics(self) -> dict:
        proc = psutil.Process()
        with proc.oneshot():
            cpu = proc.cpu_percent(interval=None)
            mem = proc.memory_info()
        vm = psutil.virtual_memory()
        return {
            "cpu_percent": round(cpu, 1),
            "mem_rss_mb": round(mem.rss / (1024 * 1024), 1),
            "mem_percent": round(vm.percent, 1),
            "mem_total_mb": round(vm.total / (1024 * 1024), 1),
        }

    # ---- websocket wrappers ------------------------------------------------
    async def _ws_vless(self, ws: WebSocket, uuid: str) -> None:
        await vless_ws_tunnel(self.ctx, ws, uuid)

    async def _ws_trojan(self, ws: WebSocket) -> None:
        await trojan_ws_tunnel(self.ctx, ws)

    async def _ws_shadowsocks(self, ws: WebSocket) -> None:
        await shadowsocks_ws_tunnel(self.ctx, ws)


def create_app(cfg: CoreConfig | None = None) -> FastAPI:
    core = Core(cfg or CoreConfig())
    return core.app
