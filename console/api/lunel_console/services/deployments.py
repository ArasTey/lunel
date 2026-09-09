"""Deployment pipeline with pluggable providers.

Providers:
* ``railway`` — one Railway service per Lunel instance (per-instance public
  domain via Railway's API, WebSocket-capable, TLS at the edge). Selected
  automatically when LUNEL_RAILWAY_TOKEN + project/environment are configured.
* ``local``   — Lunel Worker on a node running instances as containers
  (Docker driver) or dev processes. Selected for self-hosted single-node.

Lifecycle (real transitions only — no simulated states):
QUEUED → PREPARING → BUILDING → STARTING → HEALTH_CHECK → RUNNING / FAILED
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import asyncpg
import httpx

from ..config import settings
from ..logging import get
from . import workers as worker_svc
from .railway import RailwayError, RailwayProvider

log = get("runtime", "lunel.console.deploy")

HEALTH_CHECK_ATTEMPTS = 30
HEALTH_CHECK_INTERVAL = 1.0
RAILWAY_POLL_ATTEMPTS = 90
RAILWAY_POLL_INTERVAL = 2.0


def railway_provider() -> RailwayProvider | None:
    try:
        return RailwayProvider.from_env()
    except RailwayError:
        return None


async def _log(pool: asyncpg.Pool, deployment_id: str, message: str, level: str = "info") -> None:
    await pool.execute(
        "INSERT INTO deployment_logs (deployment_id, level, message) VALUES ($1, $2, $3)",
        deployment_id, level, message,
    )
    log.info("deploy[%s] %s", deployment_id[:8], message)


async def _set_deployment(pool: asyncpg.Pool, deployment_id: str, status: str,
                          error: str | None = None, finish: bool = False) -> None:
    if finish:
        await pool.execute(
            """
            UPDATE deployments
            SET status = $2, error = $3,
                finished_at = now(),
                duration_ms = (EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int
            WHERE id = $1
            """,
            deployment_id, status, error,
        )
    else:
        await pool.execute(
            "UPDATE deployments SET status = $2, error = $3 WHERE id = $1",
            deployment_id, status, error,
        )
    await pool.execute(
        "UPDATE instances SET status = $2, updated_at = now() WHERE id = "
        "(SELECT instance_id FROM deployments WHERE id = $1)",
        deployment_id, status,
    )


async def _set_instance(pool: asyncpg.Pool, instance_id: str, status: str) -> None:
    await pool.execute(
        "UPDATE instances SET status=$2, last_active_at=now(), updated_at=now() WHERE id=$1",
        instance_id, status,
    )


async def _instance_row(pool: asyncpg.Pool, instance_id: str) -> asyncpg.Record:
    return await pool.fetchrow(
        """
        SELECT i.id, i.slug, i.name, i.region, i.provider, i.provider_ref, i.core_api_token,
               c.core_version, c.cpu_limit, c.memory_mb, c.max_processes
        FROM instances i JOIN instance_configs c ON c.instance_id = i.id
        WHERE i.id = $1
        """,
        instance_id,
    )


async def deploy_instance(pool: asyncpg.Pool, instance_id: str, *, is_redeploy: bool = False) -> str:
    """Queue a deployment and run the pipeline as a background task."""
    row = await pool.fetchrow(
        "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM deployments WHERE instance_id = $1",
        instance_id,
    )
    version = row["v"]
    inst = await _instance_row(pool, instance_id)
    if inst is None:
        raise ValueError("instance not found")

    deployment_id = str((await pool.fetchrow(
        """
        INSERT INTO deployments (instance_id, version, core_version, status)
        VALUES ($1, $2, $3, 'queued')
        RETURNING id
        """,
        instance_id, version, inst["core_version"],
    ))["id"])

    asyncio.create_task(_run_pipeline(pool, deployment_id, dict(inst), is_redeploy))
    return deployment_id


async def _run_pipeline(pool: asyncpg.Pool, deployment_id: str, inst: dict,
                        is_redeploy: bool) -> None:
    try:
        if inst.get("provider") == "railway" or railway_provider() is not None:
            await _pipeline_railway(pool, deployment_id, inst, is_redeploy)
        else:
            await _pipeline_local(pool, deployment_id, inst)
    except Exception as exc:  # final safety net
        log.exception("pipeline crashed: %s", exc)
        await _log(pool, deployment_id, f"Internal pipeline error: {exc}", "error")
        await _set_deployment(pool, deployment_id, "failed", error=str(exc)[:500], finish=True)
        await _set_instance(pool, inst["id"], "failed")


# ---------------------------------------------------------------------------
# Railway provider pipeline
# ---------------------------------------------------------------------------
async def _pipeline_railway(pool: asyncpg.Pool, deployment_id: str, inst: dict,
                            is_redeploy: bool) -> None:
    provider = railway_provider()
    if provider is None:
        raise RuntimeError("railway provider requested but not configured")
    instance_id = inst["id"]
    service_id = inst.get("provider_ref") or ""

    await _set_deployment(pool, deployment_id, "preparing")
    if not service_id:
        await _log(pool, deployment_id, "Creating Railway service…")
        service_id = await provider.create_service(f"lunel-inst-{instance_id[:8]}")
        await pool.execute(
            "UPDATE instances SET provider='railway', provider_ref=$2, updated_at=now() WHERE id=$1",
            instance_id, service_id,
        )
    else:
        await _log(pool, deployment_id, f"Reusing Railway service {service_id[:12]}…")

    # Domain: reuse the active generated domain, or create one.
    domain_row = await pool.fetchrow(
        "SELECT id, domain, provider_ref FROM domains WHERE instance_id=$1 AND is_custom=FALSE "
        "ORDER BY created_at DESC LIMIT 1",
        instance_id,
    )
    if domain_row is None:
        await _log(pool, deployment_id, "Generating public domain…")
        dom = await provider.create_domain(service_id)
        await pool.execute(
            """
            INSERT INTO domains (instance_id, domain, provider_ref, tls)
            VALUES ($1, $2, $3, TRUE)
            """,
            instance_id, dom["domain"], dom["id"],
        )
        domain = dom["domain"]
    else:
        domain = domain_row["domain"]

    # Configure source, health check and secrets (skipDeploys — we deploy explicitly).
    await _set_deployment(pool, deployment_id, "building")
    await _log(pool, deployment_id, f"Configuring {provider.core_image}…")
    await provider.configure_service(service_id, inst["core_api_token"], domain)

    await _set_deployment(pool, deployment_id, "starting")
    await _log(pool, deployment_id, "Triggering Railway deployment…")
    if is_redeploy and inst.get("provider_ref"):
        await provider.restart_service(service_id)
    else:
        await provider.start_service(service_id)

    # Wait for Railway to report the deployment healthy.
    for attempt in range(1, RAILWAY_POLL_ATTEMPTS + 1):
        await asyncio.sleep(RAILWAY_POLL_INTERVAL)
        dep = await provider.latest_deployment(service_id)
        if dep is None:
            continue
        status = STATUS_MAP.get(dep.get("status"), "starting")
        if status == "running":
            break
        if status in ("failed", "stopped"):
            logs = await provider.deployment_logs(dep["id"], 40)
            tail = " | ".join(logs[-5:])
            raise RuntimeError(f"railway deployment {dep['status']}: {tail[:400]}")
        if attempt % 5 == 0:
            await _log(pool, deployment_id, f"railway status: {dep['status']}")

    # Real end-to-end health check through Railway's public edge.
    await _set_deployment(pool, deployment_id, "health_check")
    await _log(pool, deployment_id, "Health checking public endpoint…")
    healthy = False
    async with httpx.AsyncClient(timeout=5.0) as client:
        for attempt in range(1, HEALTH_CHECK_ATTEMPTS + 1):
            try:
                resp = await client.get(f"https://{domain}/health")
                if resp.status_code == 200:
                    healthy = True
                    await _log(pool, deployment_id, f"Healthy after {attempt} probe(s)")
                    break
            except (httpx.HTTPError, OSError):
                pass
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)
    if not healthy:
        raise RuntimeError("public health check failed after deployment reported success")

    await _set_deployment(pool, deployment_id, "running", finish=True)
    await _set_instance(pool, instance_id, "running")
    await _log(pool, deployment_id, "Instance is running", "ok")


# ---------------------------------------------------------------------------
# Local worker pipeline
# ---------------------------------------------------------------------------
async def _pipeline_local(pool: asyncpg.Pool, deployment_id: str, inst: dict) -> None:
    instance_id = inst["id"]
    await _set_deployment(pool, deployment_id, "preparing")
    await _log(pool, deployment_id, "Selecting worker node…")
    picked = await worker_svc.pick_worker(pool, inst.get("region"))
    if picked is None:
        raise RuntimeError("no online worker available")
    node_id, node_url = picked
    await pool.execute("UPDATE deployments SET node_id = $2 WHERE id = $1", deployment_id, node_id)
    await _log(pool, deployment_id, f"Worker node selected: {node_id}")

    await _set_deployment(pool, deployment_id, "building")
    await _log(pool, deployment_id, f"Preparing Lunel Core {inst['core_version']}…")

    await _set_deployment(pool, deployment_id, "starting")
    launch = await worker_svc.worker_call(
        node_url, "POST", f"/worker/api/instances/{instance_id}/launch",
        json_body={
            "deployment_id": deployment_id,
            "core_version": inst["core_version"],
            "core_api_token": inst["core_api_token"],
            "cpu_limit": inst["cpu_limit"],
            "memory_mb": inst["memory_mb"],
            "max_processes": inst["max_processes"],
        },
    )
    await _log(pool, deployment_id, f"Launched on {launch.get('driver')} (port {launch.get('port')})")

    await _set_deployment(pool, deployment_id, "health_check")
    await _log(pool, deployment_id, "Running health checks…")
    healthy = False
    for attempt in range(1, HEALTH_CHECK_ATTEMPTS + 1):
        await asyncio.sleep(HEALTH_CHECK_INTERVAL)
        try:
            status = await worker_svc.worker_call(
                node_url, "GET", f"/worker/api/instances/{instance_id}/status", timeout=10.0
            )
        except worker_svc.WorkerError as exc:
            await _log(pool, deployment_id, f"probe {attempt}: {exc}", "warn")
            continue
        if status.get("healthy"):
            healthy = True
            await _log(pool, deployment_id, f"Healthy after {attempt} probe(s)")
            break
        if not status.get("running"):
            raise RuntimeError("instance process exited during health check")
    if not healthy:
        raise RuntimeError("health check did not pass in time")

    await _set_deployment(pool, deployment_id, "running", finish=True)
    await _set_instance(pool, instance_id, "running")
    await _log(pool, deployment_id, "Instance is running", "ok")


# ---------------------------------------------------------------------------
# Control operations (provider-aware)
# ---------------------------------------------------------------------------
async def _provider_for_instance(pool: asyncpg.Pool, instance_id: str) -> tuple[str, asyncpg.Record]:
    inst = await _instance_row(pool, instance_id)
    if inst is None:
        raise ValueError("instance not found")
    if inst["provider"] == "railway" or (inst["provider"] is None and railway_provider() is not None):
        return "railway", inst
    return "local", inst


async def stop_instance(pool: asyncpg.Pool, instance_id: str) -> None:
    kind, inst = await _provider_for_instance(pool, instance_id)
    if kind == "railway":
        provider = railway_provider()
        if inst["provider_ref"] and provider:
            await provider.stop_service(inst["provider_ref"])
    else:
        inst2 = await pool.fetchrow(
            """
            SELECT d.node_id FROM deployments d WHERE instance_id = $1
            ORDER BY started_at DESC LIMIT 1
            """,
            instance_id,
        )
        node_url = worker_svc.worker_url_for((inst2["node_id"] if inst2 else None) or settings.default_worker_node)
        await worker_svc.worker_call(node_url, "POST", f"/worker/api/instances/{instance_id}/stop")
    await pool.execute(
        "UPDATE instances SET status='stopped', updated_at=now() WHERE id=$1", instance_id
    )


async def restart_instance(pool: asyncpg.Pool, instance_id: str) -> None:
    kind, inst = await _provider_for_instance(pool, instance_id)
    if kind == "railway":
        provider = railway_provider()
        if inst["provider_ref"] and provider:
            await provider.restart_service(inst["provider_ref"])
        await pool.execute(
            "UPDATE instances SET status='starting', updated_at=now() WHERE id=$1", instance_id
        )
    else:
        await deploy_instance(pool, instance_id, is_redeploy=True)


async def delete_from_provider(pool: asyncpg.Pool, instance_id: str) -> None:
    kind, inst = await _provider_for_instance(pool, instance_id)
    if kind == "railway":
        provider = railway_provider()
        if inst["provider_ref"] and provider:
            try:
                await provider.delete_service(inst["provider_ref"])
            except RailwayError as exc:
                log.warning("railway service delete failed for %s: %s", instance_id, exc)
    else:
        inst2 = await pool.fetchrow(
            "SELECT node_id FROM deployments WHERE instance_id = $1 ORDER BY started_at DESC LIMIT 1",
            instance_id,
        )
        if inst2 and inst2["node_id"]:
            node_url = worker_svc.worker_url_for(inst2["node_id"])
            try:
                await worker_svc.worker_call(
                    node_url, "POST", f"/worker/api/instances/{instance_id}/remove", timeout=20.0
                )
            except worker_svc.WorkerError as exc:
                log.warning("worker remove failed for %s: %s", instance_id, exc)
