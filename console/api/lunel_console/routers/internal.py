"""Internal routes — worker heartbeat ingestion.

Authenticated with the shared worker token; not exposed to browser users.
"""
from __future__ import annotations

import secrets

import asyncpg
from fastapi import APIRouter, HTTPException, Request

from ..config import settings
from ..db import get_pool

router = APIRouter(prefix="/api/internal", tags=["internal"])


def require_worker_token(request: Request) -> None:
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not token or not secrets.compare_digest(token, settings.heartbeat_token or settings.worker_token):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.post("/heartbeat")
async def heartbeat(request: Request):
    require_worker_token(request)
    pool = get_pool(request)
    data = await request.json()
    node_id = str(data.get("node_id") or "").strip()[:64]
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id required")

    def _num(key):
        try:
            return float(data.get(key)) if data.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    await pool.execute(
        """
        INSERT INTO workers (node_id, region, driver, status, enabled, cpu_percent,
                             mem_used_mb, mem_total_mb, disk_used_gb, disk_total_gb,
                             instances, capacity, last_heartbeat)
        VALUES ($1, $2, $3, 'online', TRUE, $4, $5, $6, $7, $8, $9, $10, now())
        ON CONFLICT (node_id) DO UPDATE SET
            region = EXCLUDED.region,
            driver = EXCLUDED.driver,
            status = 'online',
            cpu_percent = EXCLUDED.cpu_percent,
            mem_used_mb = EXCLUDED.mem_used_mb,
            mem_total_mb = EXCLUDED.mem_total_mb,
            disk_used_gb = EXCLUDED.disk_used_gb,
            disk_total_gb = EXCLUDED.disk_total_gb,
            instances = EXCLUDED.instances,
            capacity = EXCLUDED.capacity,
            last_heartbeat = now()
        """,
        node_id,
        str(data.get("region") or "local")[:40],
        str(data.get("driver") or "process")[:20],
        _num("cpu_percent"),
        int(_num("mem_used_mb") or 0) or None,
        int(_num("mem_total_mb") or 0) or None,
        _num("disk_used_gb"),
        _num("disk_total_gb"),
        int(_num("instances") or 0),
        int(_num("capacity_instances") or 20),
    )
    # Mark workers that stopped heartbeating as offline.
    await pool.execute(
        "UPDATE workers SET status = 'offline' WHERE enabled = TRUE "
        "AND last_heartbeat < now() - interval '60 seconds'"
    )
    return {"ok": True}
