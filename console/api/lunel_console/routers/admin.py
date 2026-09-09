"""Admin routes: users, instances, workers, deployments, system health.

Every route requires the admin flag. Actions are audited into activity_events.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import sessions
from ..config import settings
from ..db import get_pool
from ..services import deployments as deploy_svc
from .instances import current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


async def admin_user(request: Request) -> asyncpg.Record:
    pool = get_pool(request)
    user = await sessions.get_session_user(pool, request)
    sessions.require_admin(user)
    sessions.check_csrf(request, user)
    return user


@router.get("/overview")
async def overview(request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24))
    stats = await pool.fetchrow(
        """
        SELECT
          (SELECT COUNT(*) FROM users) AS users,
          (SELECT COUNT(*) FROM instances WHERE status <> 'deleted') AS instances,
          (SELECT COUNT(*) FROM instances WHERE status = 'running') AS instances_running,
          (SELECT COUNT(*) FROM instances WHERE status = 'failed') AS instances_failed,
          (SELECT COUNT(*) FROM deployments WHERE started_at > $1) AS deployments_24h,
          (SELECT COUNT(*) FROM workers WHERE status = 'online') AS workers_online
        """,
        cutoff,
    )
    return dict(stats)


@router.get("/users")
async def list_users(request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    rows = await pool.fetch(
        """
        SELECT u.id, u.github_id, u.login, u.name, u.email, u.is_admin, u.is_disabled,
               u.created_at, u.last_login_at,
               (SELECT COUNT(*) FROM instances i WHERE i.user_id = u.id AND i.status <> 'deleted') AS instance_count
        FROM users u ORDER BY u.created_at DESC LIMIT 500
        """
    )
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = str(item["id"])
        item["created_at"] = item["created_at"].isoformat() if item["created_at"] else None
        item["last_login_at"] = item["last_login_at"].isoformat() if item["last_login_at"] else None
        out.append(item)
    return {"users": out}


@router.patch("/users/{user_id}")
async def update_user(user_id: str, request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    body = await request.json()
    sets, args = [], []
    if "is_disabled" in body:
        sets.append("is_disabled = $" + str(len(args) + 2))
        args.append(bool(body["is_disabled"]))
    if "is_admin" in body:
        sets.append("is_admin = $" + str(len(args) + 2))
        args.append(bool(body["is_admin"]))
    if not sets:
        raise HTTPException(status_code=400, detail="nothing to update")
    row = await pool.fetchrow(f"UPDATE users SET {', '.join(sets)} WHERE id = $1 RETURNING id", user_id, *args)
    if row is None:
        raise HTTPException(status_code=404, detail="user not found")
    if body.get("is_disabled"):
        await sessions.destroy_all_sessions(pool, user_id)
    return {"ok": True}


@router.get("/instances")
async def all_instances(request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    rows = await pool.fetch(
        """
        SELECT i.id, i.name, i.slug, i.status, i.provider, i.region, i.created_at,
               u.login AS owner_login, u.id AS owner_id
        FROM instances i JOIN users u ON u.id = i.user_id
        WHERE i.status <> 'deleted'
        ORDER BY i.created_at DESC LIMIT 500
        """
    )
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = str(item["id"])
        item["owner_id"] = str(item["owner_id"])
        item["created_at"] = item["created_at"].isoformat()
        out.append(item)
    return {"instances": out}


@router.get("/workers")
async def list_workers(request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    rows = await pool.fetch(
        "SELECT id, node_id, region, driver, status, enabled, cpu_percent, mem_used_mb, "
        "mem_total_mb, disk_used_gb, disk_total_gb, instances, capacity, last_heartbeat "
        "FROM workers ORDER BY node_id"
    )
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = str(item["id"])
        item["last_heartbeat"] = item["last_heartbeat"].isoformat() if item["last_heartbeat"] else None
        out.append(item)
    return {"workers": out}


@router.patch("/workers/{worker_id}")
async def update_worker(worker_id: str, request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    body = await request.json()
    if "enabled" not in body:
        raise HTTPException(status_code=400, detail="nothing to update")
    row = await pool.fetchrow(
        "UPDATE workers SET enabled = $2 WHERE id = $1 RETURNING id", worker_id, bool(body["enabled"])
    )
    if row is None:
        raise HTTPException(status_code=404, detail="worker not found")
    return {"ok": True}


@router.post("/instances/{instance_id}/actions/{action}")
async def instance_action(instance_id: str, action: str, request: Request,
                          admin: asyncpg.Record = Depends(admin_user)):
    if action not in ("restart", "stop", "redeploy"):
        raise HTTPException(status_code=400, detail="action must be restart|stop|redeploy")
    pool = get_pool(request)
    inst = await pool.fetchrow(
        "SELECT id, name, user_id FROM instances WHERE id = $1 AND status <> 'deleted'",
        instance_id,
    )
    if inst is None:
        raise HTTPException(status_code=404, detail="instance not found")
    if action == "restart":
        await deploy_svc.restart_instance(pool, instance_id)
    elif action == "stop":
        await deploy_svc.stop_instance(pool, instance_id)
    else:
        await deploy_svc.deploy_instance(pool, instance_id, is_redeploy=True)
    await pool.execute(
        "INSERT INTO activity_events (user_id, instance_id, kind, level, message, created_at) "
        "VALUES ($1, $2, 'admin', 'warn', $3, $4)",
        admin["id"], instance_id, f"Admin '{admin['login']}' issued {action} on '{inst['name']}'",
        datetime.now(timezone.utc),
    )
    return {"ok": True}


@router.delete("/instances/{instance_id}")
async def admin_delete_instance(instance_id: str, request: Request,
                                admin: asyncpg.Record = Depends(admin_user)):
    pool = get_pool(request)
    inst = await pool.fetchrow(
        "SELECT id, name FROM instances WHERE id = $1 AND status <> 'deleted'", instance_id
    )
    if inst is None:
        raise HTTPException(status_code=404, detail="instance not found")
    await deploy_svc.delete_from_provider(pool, instance_id)
    await pool.execute("UPDATE instances SET status='deleted', updated_at=$2 WHERE id=$1", instance_id, datetime.now(timezone.utc))
    await pool.execute(
        "INSERT INTO activity_events (user_id, instance_id, kind, level, message, created_at) "
        "VALUES ($1, $2, 'admin', 'warn', $3, $4)",
        admin["id"], instance_id, f"Admin '{admin['login']}' deleted '{inst['name']}'",
        datetime.now(timezone.utc),
    )
    return {"ok": True}


@router.get("/deployments")
async def recent_deployments(request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    rows = await pool.fetch(
        """
        SELECT d.id, d.version, d.status, d.error, d.started_at, d.duration_ms,
               i.name AS instance_name, i.slug
        FROM deployments d JOIN instances i ON i.id = d.instance_id
        ORDER BY d.started_at DESC LIMIT 100
        """
    )
    out = []
    for r in rows:
        item = dict(r)
        item["id"] = str(item["id"])
        item["started_at"] = item["started_at"].isoformat()
        out.append(item)
    return {"deployments": out}


@router.get("/system")
async def system_info(request: Request, _=Depends(admin_user)):
    pool = get_pool(request)
    db_ok = bool(await pool.fetchval("SELECT 1"))
    return {
        "database": {"ok": db_ok, "provider": "postgresql"},
        "provider": {
            "railway": deploy_svc.railway_provider() is not None,
            "local_worker": settings.local_worker_url,
        },
        "github_oauth": bool(settings.github_client_id and settings.github_client_secret),
    }
