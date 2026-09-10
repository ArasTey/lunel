"""Domain management routes.

Endpoints per instance:
* Platform-generated path endpoints (always available): the console URL with
  a private endpoint token — works on any platform (incl. Lucity) with zero
  extra configuration.
* Provider domains (Railway: real generated hostnames; self-hosted: wildcard
  DNS + bundled Caddy). Regenerate deletes and re-creates.
"""
from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import sessions
from ..db import get_pool
from ..services.domains import generate_domain
from ..services import deployments as deploy_svc
from .instances import current_user, owned_instance

router = APIRouter(prefix="/api", tags=["domains"])


@router.get("/instances/{instance_id}/domains")
async def list_domains(instance_id: str, request: Request,
                       user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    rows = await pool.fetch(
        "SELECT id, domain, kind, is_custom, tls, is_active, created_at FROM domains "
        "WHERE instance_id = $1 ORDER BY created_at",
        instance_id,
    )
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = str(item["id"])
        item["created_at"] = item["created_at"].isoformat()
        if item["kind"] == "path":
            item["url"] = f"{_console_origin(request)}/i/{item['domain']}"
        else:
            item["url"] = f"https://{item['domain']}"
        out.append(item)
    return {"domains": out}


@router.post("/instances/{instance_id}/domains")
async def create_domain(instance_id: str, request: Request,
                        user: asyncpg.Record = Depends(current_user)):
    """Regenerate the instance's public endpoint(s)."""
    pool = get_pool(request)
    inst = await owned_instance(pool, user["id"], instance_id)

    # 1. Rotate the path endpoint token (always).
    old = await pool.fetchval(
        "SELECT domain FROM domains WHERE instance_id = $1 AND kind = 'path' AND is_active = TRUE",
        instance_id,
    )
    from ..security.token_codec import encode_token

    new_token = encode_token(instance_id, settings.secret_key)
    if old:
        await pool.execute(
            "UPDATE domains SET is_active = FALSE WHERE instance_id = $1 AND kind = 'path'",
            instance_id,
        )
    await pool.execute(
        "INSERT INTO domains (id, instance_id, domain, kind, tls, created_at) "
        "VALUES ($1, $2, $3, 'path', TRUE, $4)",
        secrets.token_hex(16), instance_id, new_token,
        datetime.now(timezone.utc),
    )

    # 2. Rotate provider hostname when the provider supports it.
    provider_domain = None
    provider = deploy_svc.railway_provider()
    if provider and inst["provider_ref"]:
        row = await pool.fetchrow(
            "SELECT provider_ref FROM domains WHERE instance_id=$1 AND kind='http' "
            "AND is_active=TRUE ORDER BY created_at DESC LIMIT 1",
            instance_id,
        )
        try:
            if row and row["provider_ref"]:
                await provider.delete_domain(row["provider_ref"])
                await pool.execute(
                    "UPDATE domains SET is_active=FALSE WHERE id IN "
                    "(SELECT id FROM domains WHERE instance_id=$1 AND kind='http')",
                    instance_id,
                )
            dom = await provider.create_domain(inst["provider_ref"])
            await pool.execute(
                "INSERT INTO domains (id, instance_id, domain, kind, provider_ref, tls, created_at) "
                "VALUES ($1, $2, $3, 'http', $4, TRUE, $5)",
                secrets.token_hex(16), instance_id, dom["domain"], dom["id"],
                datetime.now(timezone.utc),
            )
            provider_domain = dom["domain"]
        except Exception:
            provider_domain = None  # provider domain rotation failed; path endpoint still rotated

    return {
        "ok": True,
        "path_endpoint": f"{_console_origin(request)}/i/{new_token}",
        "provider_domain": provider_domain,
    }


@router.delete("/instances/{instance_id}/domains/{domain_id}")
async def delete_domain(instance_id: str, domain_id: str, request: Request,
                        user: asyncpg.Record = Depends(current_user)):
    pool = get_pool(request)
    await owned_instance(pool, user["id"], instance_id)
    row = await pool.fetchrow(
        "SELECT id, kind, provider_ref, is_custom FROM domains "
        "WHERE id = $1 AND instance_id = $2",
        domain_id, instance_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="domain not found")
    if row["kind"] == "path":
        raise HTTPException(status_code=400, detail="path endpoints can only be regenerated, not removed")
    await pool.execute("UPDATE domains SET is_active = FALSE WHERE id = $1", domain_id)
    return {"ok": True}


def _console_origin(request: Request) -> str:
    import os

    explicit = os.environ.get("LUNEL_PUBLIC_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "127.0.0.1:8080"
    return f"{proto}://{host}"


