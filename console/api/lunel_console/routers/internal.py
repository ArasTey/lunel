"""Internal routes — worker heartbeat ingestion.

Authenticated with the shared worker token; not exposed to browser users.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

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

    now_iso = datetime.now(timezone.utc)
    existing = await pool.fetchrow(
        "SELECT id, enabled FROM workers WHERE node_id = $1", node_id
    )
    if existing is None:
        await pool.execute(
            """
            INSERT INTO workers (id, node_id, region, driver, status, enabled, cpu_percent,
                                 mem_used_mb, mem_total_mb, disk_used_gb, disk_total_gb,
                                 instances, capacity, last_heartbeat, created_at)
            VALUES ($1, $2, $3, $4, 'online', TRUE, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            secrets.token_hex(16), node_id,
            str(data.get("region") or "local")[:40],
            str(data.get("driver") or "process")[:20],
            _num("cpu_percent"),
            int(_num("mem_used_mb") or 0) or None,
            int(_num("mem_total_mb") or 0) or None,
            _num("disk_used_gb"),
            _num("disk_total_gb"),
            int(_num("instances") or 0),
            int(_num("capacity_instances") or 20),
            now_iso, now_iso,
        )
    else:
        await pool.execute(
            """
            UPDATE workers SET region = $2, driver = $3, status = 'online',
                cpu_percent = $4, mem_used_mb = $5, mem_total_mb = $6,
                disk_used_gb = $7, disk_total_gb = $8, instances = $9,
                capacity = $10, last_heartbeat = $11
            WHERE node_id = $1
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
            now_iso,
        )
    # Mark workers that stopped heartbeating as offline.
    await pool.execute(
        "UPDATE workers SET status = 'offline' WHERE enabled = TRUE "
        "AND last_heartbeat < $1",
        (datetime.now(timezone.utc) - timedelta(seconds=60)),
    )
    return {"ok": True}
