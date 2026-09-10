"""Instance management routes — the core of the Lunel Console API.

All routes require an authenticated session; every query is scoped to the
owning user (authorization). Secrets (core API token) never leave the server.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import sessions
from ..config import settings
from ..db import get_pool
from ..services import deployments as deploy_svc
from ..services import workers as worker_svc
from ..services.domains import generate_domain, slugify, validate_slug

router = APIRouter(prefix="/api", tags=["instances"])

def _utcnow() -> str:
    return datetime.now(timezone.utc)


PROTOCOLS = ("vless-ws", "trojan-ws", "shadowsocks", "xhttp-packet-up", "xhttp-stream-up")


async def current_user(request: Request) -> asyncpg.Record:
    pool = get_pool(request)
    user = await sessions.get_session_user(pool, request)
    sessions.require_user(user)
    sessions.check_csrf(request, user)
    return user


async def owned_instance(pool: asyncpg.Pool, user_id: str, instance_id: str) -> asyncpg.Record:
    inst = await pool.fetchrow(
        "SELECT * FROM instances WHERE id = $1 AND user_id = $2 AND status <> 'deleted'",
        instance_id, user_id,
    )
    if inst is None:
        raise HTTPException(status_code=404, detail="instance not found")
    return inst


# ---------------------------------------------------------------------------
# Listing / detail
# ---------------------------------------------------------------------------
@router.get("/instances")
async def list_instances(request: Request, user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    rows = await pool.fetch(
        """
        SELECT i.id, i.name, i.slug, i.region, i.status, i.provider, i.created_at,
               i.last_active_at,
               (SELECT d.domain FROM domains d
                WHERE d.instance_id = i.id AND d.is_active = TRUE
                ORDER BY (CASE WHEN d.kind = 'path' THEN 0 ELSE 1 END), d.created_at DESC LIMIT 1) AS domain,
               (SELECT d.kind FROM domains d
                WHERE d.instance_id = i.id AND d.is_active = TRUE
                ORDER BY (CASE WHEN d.kind = 'path' THEN 0 ELSE 1 END), d.created_at DESC LIMIT 1) AS domain_kind,
               (SELECT COUNT(*) FROM deployments dep WHERE dep.instance_id = i.id) AS deployments_count
        FROM instances i
        WHERE i.user_id = $1 AND i.status <> 'deleted'
        ORDER BY i.created_at DESC
        """,
        user["id"],
    )
    origin = _console_origin(request)
    out = []
    for r in rows:
        item = _instance_out(r)
        if item.get("domain"):
            item["endpoint_url"] = (
                f"{origin}/i/{item['domain']}" if item.get("domain_kind") == "path"
                else f"https://{item['domain']}"
            )
        out.append(item)
    return {"instances": out}


def _console_origin(request: Request) -> str:
    """Public origin of this console, trusting the edge proxy's headers
    (X-Forwarded-Proto/Host) when present, else the explicit setting."""
    import os

    explicit = os.environ.get("LUNEL_PUBLIC_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "127.0.0.1:8080"
    return f"{proto}://{host}"


@router.get("/instances/{instance_id}")
async def get_instance(instance_id: str, request: Request,
                       user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    inst = await owned_instance(pool, user["id"], instance_id)
    cfg = await pool.fetchrow("SELECT * FROM instance_configs WHERE instance_id = $1", instance_id)
    domains = await pool.fetch(
        "SELECT domain, kind, is_custom, tls, is_active FROM domains "
        "WHERE instance_id = $1 AND is_active = TRUE ORDER BY created_at",
        instance_id,
    )
    origin = _console_origin(request)
    domains = [
        dict(d) | {"url": (f"{origin}/i/{d['domain']}" if d["kind"] == "path" else f"https://{d['domain']}")}
        for d in domains
    ]
    latest_dep = await pool.fetchrow(
        "SELECT id, version, status, error, started_at, finished_at, duration_ms "
        "FROM deployments WHERE instance_id = $1 ORDER BY started_at DESC LIMIT 1",
        instance_id,
    )
    out = _instance_out(inst)
    path_dom = next((d for d in domains if d["kind"] == "path"), None)
    if path_dom is not None:
        out["endpoint_url"] = path_dom["url"]
        out["endpoint_path"] = f"/i/{path_dom['domain']}"
    out.update({
        "config": {
            "protocol": cfg["protocol"],
            "protocols": (cfg["protocols"].split(",") if cfg["protocols"] else [cfg["protocol"]]),
            "cpu_limit": cfg["cpu_limit"],
            "memory_mb": cfg["memory_mb"], "max_processes": cfg["max_processes"],
            "core_version": cfg["core_version"],
        } if cfg else None,
        "domains": [dict(d) for d in domains],
        "latest_deployment": dict(latest_dep) if latest_dep else None,
    })
    return out


def _instance_out(row: asyncpg.Record) -> dict:
    data = dict(row)
    data["id"] = str(data["id"])
    for key in ("created_at", "updated_at", "last_active_at"):
        if key in data and data[key] is not None:
            data[key] = data[key].isoformat()
    return data


# ---------------------------------------------------------------------------
# Create (wizard) + delete
# ---------------------------------------------------------------------------
class CreateInstanceBody:
    def __init__(self, body: dict):
        self.name = str(body.get("name") or "").strip()
        self.region = str(body.get("region") or "local").strip()[:40]
        config = body.get("config") or {}
        if not isinstance(config, dict):
            raise HTTPException(status_code=400, detail="config must be an object")
        self.protocol = str(config.get("protocol") or "vless-ws")
        protos = config.get("protocols")
        if isinstance(protos, list):
            cleaned = [p for p in protos if p in PROTOCOLS]
            self.protocols = cleaned or ["vless-ws"]
        else:
            self.protocols = None  # fall back to the primary protocol
        self.cpu_limit = float(config.get("cpu_limit") or 0.5)
        self.memory_mb = int(config.get("memory_mb") or 256)
        self.core_version = str(config.get("core_version") or "latest")[:40]


@router.post("/instances", status_code=201)
async def create_instance(request: Request, user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    body = CreateInstanceBody(await request.json())

    if not (2 <= len(body.name) <= 60):
        raise HTTPException(status_code=400, detail="name must be 2-60 characters")
    if body.protocol not in PROTOCOLS:
        raise HTTPException(status_code=400, detail=f"protocol must be one of {PROTOCOLS}")
    if not (0.1 <= body.cpu_limit <= 8):
        raise HTTPException(status_code=400, detail="cpu_limit must be 0.1-8")
    if not (128 <= body.memory_mb <= 8192):
        raise HTTPException(status_code=400, detail="memory_mb must be 128-8192")

    slug = slugify(body.name)
    if not validate_slug(slug):
        raise HTTPException(status_code=400, detail="name produces an invalid slug")

    # Enforce a sane per-user instance cap.
    count = await pool.fetchval(
        "SELECT COUNT(*) FROM instances WHERE user_id = $1 AND status <> 'deleted'", user["id"]
    )
    if count >= 25:
        raise HTTPException(status_code=409, detail="instance limit reached (25)")

    dup = await pool.fetchval(
        "SELECT 1 FROM instances WHERE user_id = $1 AND slug = $2 AND status <> 'deleted'",
        user["id"], slug,
    )
    if dup:
        raise HTTPException(status_code=409, detail="you already have an instance with this name")

    instance_id = secrets.token_hex(16)
    now_iso = _utcnow()
    await pool.execute(
        """
        INSERT INTO instances (id, user_id, name, slug, region, status, provider,
                               core_api_token, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, 'stopped', $6, $7, $8, $8)
        """,
        instance_id, user["id"], body.name, slug, body.region,
        "railway" if deploy_svc.railway_provider() else "local",
        secrets.token_urlsafe(24), now_iso,
    )
    await pool.execute(
        "INSERT INTO instance_configs (instance_id, protocol, cpu_limit, memory_mb, core_version, protocols, updated_at) "
        "VALUES ($1, $2, $3, $4, $5, $6, $7)",
        instance_id, body.protocol, body.cpu_limit, body.memory_mb, body.core_version,
        ",".join(body.protocols) if body.protocols else body.protocol, now_iso,
    )
    # Every instance gets a private path endpoint immediately (works on every
    # platform incl. Lucity; real hostnames come from the provider where supported).
    # The endpoint token is an AES-GCM ciphertext of the instance id — opaque,
    # non-enumerable, and tamper-proof (see security/token_codec.py).
    from ..security.token_codec import encode_token

    await pool.execute(
        "INSERT INTO domains (id, instance_id, domain, kind, tls, created_at) "
        "VALUES ($1, $2, $3, 'path', TRUE, $4)",
        secrets.token_hex(16), instance_id,
        encode_token(instance_id, settings.secret_key), _utcnow(),
    )
    await _record_activity(pool, user["id"], instance_id, "instance",
                           f"Instance '{body.name}' created")
    return await get_instance(instance_id, request, user)


@router.delete("/instances/{instance_id}")
async def delete_instance(instance_id: str, request: Request,
                          user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    inst = await owned_instance(pool, user["id"], instance_id)
    try:
        await deploy_svc.delete_from_provider(pool, instance_id)
    except Exception:
        pass  # provider cleanup is best-effort; the record is tombstoned regardless
    await pool.execute(
        "UPDATE instances SET status='deleted', updated_at=$2 WHERE id=$1",
        instance_id, _utcnow(),
    )
    await _record_activity(pool, user["id"], instance_id, "instance",
                           f"Instance '{inst['name']}' deleted", level="warn")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
@router.post("/instances/{instance_id}/deploy")
async def deploy(instance_id: str, request: Request,
                 user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    inst = await owned_instance(pool, user["id"], instance_id)
    if inst["status"] in ("queued", "preparing", "building", "starting", "health_check"):
        raise HTTPException(status_code=409, detail="a deployment is already in progress")
    try:
        deployment_id = await deploy_svc.deploy_instance(pool, instance_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _record_activity(pool, user["id"], instance_id, "deployment",
                           f"Deployment queued for '{inst['name']}'")
    return {"ok": True, "deployment_id": deployment_id}


@router.post("/instances/{instance_id}/restart")
async def restart(instance_id: str, request: Request,
                  user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    inst = await owned_instance(pool, user["id"], instance_id)
    if inst["status"] != "running":
        raise HTTPException(status_code=409, detail="instance is not running")
    await deploy_svc.restart_instance(pool, instance_id)
    await _record_activity(pool, user["id"], instance_id, "instance",
                           f"Instance '{inst['name']}' restarted")
    return {"ok": True}


@router.post("/instances/{instance_id}/stop")
async def stop(instance_id: str, request: Request,
               user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    inst = await owned_instance(pool, user["id"], instance_id)
    await deploy_svc.stop_instance(pool, instance_id)
    await _record_activity(pool, user["id"], instance_id, "instance",
                           f"Instance '{inst['name']}' stopped", level="warn")
    return {"ok": True}


@router.post("/instances/{instance_id}/redeploy")
async def redeploy(instance_id: str, request: Request,
                   user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    inst = await owned_instance(pool, user["id"], instance_id)
    try:
        deployment_id = await deploy_svc.deploy_instance(pool, instance_id, is_redeploy=True)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    await _record_activity(pool, user["id"], instance_id, "deployment",
                           f"Redeploy queued for '{inst['name']}'")
    return {"ok": True, "deployment_id": deployment_id}


# ---------------------------------------------------------------------------
# Runtime data (via worker)
# ---------------------------------------------------------------------------
@router.get("/instances/{instance_id}/status")
async def status(instance_id: str, request: Request,
                 user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    node_url, node_id = await _worker_for(pool, instance_id)
    if node_url is None:
        return {"running": False, "known": False}
    try:
        return await worker_svc.worker_call(
            node_url, "GET", f"/worker/api/instances/{instance_id}/status", timeout=10.0
        )
    except worker_svc.WorkerError as exc:
        return {"running": False, "known": True, "error": str(exc)}


@router.get("/instances/{instance_id}/logs")
async def logs(instance_id: str, request: Request,
               user: asyncpg.Record = Depends(current_user), tail: int = 200):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    node_url, node_id = await _worker_for(pool, instance_id)
    if node_url is None:
        return {"logs": []}
    try:
        return await worker_svc.worker_call(
            node_url, "GET", f"/worker/api/instances/{instance_id}/logs", timeout=10.0
        )
    except worker_svc.WorkerError as exc:
        return {"logs": [f"[console] worker error: {exc}"]}


@router.get("/instances/{instance_id}/metrics")
async def metrics(instance_id: str, request: Request,
                  user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    node_url, _ = await _worker_for(pool, instance_id)
    if node_url is None:
        return {"available": False}
    try:
        return await worker_svc.worker_call(
            node_url, "GET", f"/worker/api/instances/{instance_id}/metrics", timeout=10.0
        )
    except worker_svc.WorkerError as exc:
        return {"available": False, "error": str(exc)}


async def _worker_for(pool: asyncpg.Pool, instance_id: str) -> tuple[str | None, str | None]:
    row = await pool.fetchrow(
        "SELECT node_id FROM deployments WHERE instance_id = $1 ORDER BY started_at DESC LIMIT 1",
        instance_id,
    )
    node_id = (row["node_id"] if row else None) or settings.default_worker_node
    return worker_svc.worker_url_for(node_id), node_id


@router.get("/instances/{instance_id}/config")
async def instance_config(instance_id: str, request: Request,
                          user: asyncpg.Record = Depends(current_user)):
    """The actual proxy configs (vless:// etc.) for this instance, routed
    through its public endpoint path. Host comes from the user's request so
    the panel works on any platform domain."""
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    dom = await pool.fetchrow(
        "SELECT domain FROM domains WHERE instance_id = $1 AND kind = 'path' "
        "AND is_active = TRUE ORDER BY created_at DESC LIMIT 1",
        instance_id,
    )
    if dom is None:
        raise HTTPException(status_code=404, detail="no endpoint provisioned yet")
    # Host precedence: browser-announced public host (the panel tells us the
    # real domain; platform edges hide it behind internal names) -> forwarded
    # headers -> Host -> request hostname.
    inst_row = await pool.fetchrow("SELECT public_host FROM instances WHERE id = $1", instance_id)
    host = ((inst_row["public_host"] if inst_row else None)
            or (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
            or request.headers.get("host")
            or request.url.hostname or "localhost")
    host = host.split(":")[0]  # strip internal ports
    path_prefix = f"/i/{dom['domain']}"

    link_rows = await pool.fetch(
        "SELECT link_uuid, label FROM instance_links WHERE instance_id = $1 ORDER BY created_at",
        instance_id,
    )
    node_url, _ = await _worker_for(pool, instance_id)
    configs = []
    error = None
    if node_url:
        try:
            import httpx as _httpx
            from ..config import settings as _settings

            async with _httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{node_url.rstrip('/')}/worker/api/instances/{instance_id}/proxy/core/api/share",
                    json={"host": host, "path_prefix": path_prefix,
                          "uuids": [r["link_uuid"] for r in link_rows]},
                    headers={"Authorization": f"Bearer {_settings.worker_token}",
                             "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                configs = resp.json().get("links", [])
        except Exception as exc:
            error = str(exc)[:200]
    return {"endpoint_path": path_prefix, "hostname": host,
            "configs": configs, "error": error}


@router.post("/instances/{instance_id}/announce-host")
async def announce_host(instance_id: str, request: Request,
                        user: asyncpg.Record = Depends(current_user)):
    """The panel tells us the public host it was browsed on (the server can't
    see it behind platform edges). Used for subscription link contents."""
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    body = await request.json()
    host = str(body.get("host") or "").strip()[:253]
    if host and ("." in host or host.startswith("localhost")):
        await pool.execute(
            "UPDATE instances SET public_host = $2 WHERE id = $1", instance_id, host
        )
    return {"ok": True}


@router.post("/instances/{instance_id}/qr")
async def instance_qr(instance_id: str, request: Request,
                      user: asyncpg.Record = Depends(current_user)):
    """QR code (SVG) for any config string. No external services."""
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    body = await request.json()
    text = str(body.get("text") or "")[:4096]
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage,
                      box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf)
    from fastapi.responses import Response as _Response

    return _Response(content=buf.getvalue(), media_type="image/svg+xml")


# ---------------------------------------------------------------------------
# Deployments & activity
# ---------------------------------------------------------------------------
@router.get("/instances/{instance_id}/deployments")
async def deployments(instance_id: str, request: Request,
                      user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    rows = await pool.fetch(
        """
        SELECT d.id, d.version, d.status, d.error, d.node_id, d.started_at, d.finished_at, d.duration_ms,
               (SELECT COUNT(*) FROM deployment_logs l WHERE l.deployment_id = d.id) AS log_count
        FROM deployments d WHERE d.instance_id = $1
        ORDER BY d.started_at DESC LIMIT 50
        """,
        instance_id,
    )
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = str(item["id"])
        for key in ("started_at", "finished_at"):
            if item[key] is not None:
                item[key] = item[key].isoformat()
        out.append(item)
    return {"deployments": out}


@router.get("/instances/{instance_id}/deployments/{deployment_id}/logs")
async def deployment_logs(instance_id: str, deployment_id: str, request: Request,
                          user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    rows = await pool.fetch(
        """
        SELECT level, message, ts FROM deployment_logs
        WHERE deployment_id = $1 AND deployment_id IN
          (SELECT id FROM deployments WHERE instance_id = $2)
        ORDER BY ts ASC LIMIT 500
        """,
        deployment_id, instance_id,
    )
    return {"logs": [{"level": r["level"], "message": r["message"],
                      "ts": r["ts"].isoformat()} for r in rows]}


@router.get("/instances/{instance_id}/activity")
async def activity(instance_id: str, request: Request,
                   user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    rows = await pool.fetch(
        "SELECT kind, level, message, created_at FROM activity_events "
        "WHERE instance_id = $1 ORDER BY created_at DESC LIMIT 100",
        instance_id,
    )
    return {"activity": [{"kind": r["kind"], "level": r["level"], "message": r["message"],
                          "ts": r["created_at"].isoformat()} for r in rows]}


@router.get("/activity")
async def my_activity(request: Request, user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    rows = await pool.fetch(
        "SELECT kind, level, message, instance_id, created_at FROM activity_events "
        "WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50",
        user["id"],
    )
    return {"activity": [{"kind": r["kind"], "level": r["level"], "message": r["message"],
                          "instance_id": str(r["instance_id"]) if r["instance_id"] else None,
                          "ts": r["created_at"].isoformat()} for r in rows]}


async def _record_activity(pool, user_id, instance_id, kind, message, level="info"):
    await pool.execute(
        "INSERT INTO activity_events (user_id, instance_id, kind, level, message, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        user_id, instance_id, kind, level, message, _utcnow(),
    )
