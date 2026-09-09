"""HTTP client for Lunel Workers + scheduler.

The scheduler picks an online, enabled worker in the requested region with
spare capacity. For single-node deployments the local worker is used directly.
"""
from __future__ import annotations

import httpx

from ..config import settings
from ..logging import get

log = get("runtime", "lunel.console.workers")


class WorkerError(RuntimeError):
    pass


async def worker_call(node_url: str, method: str, path: str, json_body: dict | None = None,
                      timeout: float = 30.0) -> dict:
    url = f"{node_url.rstrip('/')}{path}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(
                method, url,
                json=json_body,
                headers={"Authorization": f"Bearer {settings.worker_token}"},
            )
    except (httpx.HTTPError, OSError) as exc:
        raise WorkerError(f"worker unreachable ({type(exc).__name__})") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise WorkerError(f"worker returned {resp.status_code}: {detail}")
    if not resp.content:
        return {}
    return resp.json()


async def pick_worker(pool, region: str | None = None) -> tuple[str, str] | None:
    """Returns (node_id, node_url) for the best worker, or None."""
    rows = await pool.fetch(
        """
        SELECT node_id, region, status, enabled, instances, capacity
        FROM workers
        WHERE enabled = TRUE AND status = 'online'
          AND (instances IS NULL OR capacity IS NULL OR instances < capacity)
        """,
    )
    if region:
        rows = [r for r in rows if r["region"] == region]
    rows = sorted(
        rows,
        key=lambda r: (r["instances"] or 0) / max(r["capacity"] or 1, 1),
    )
    if rows:
        row = rows[0]
        return row["node_id"], worker_url_for(row["node_id"])
    # Fall back to the configured local worker (single-node deployments).
    return settings.default_worker_node, settings.local_worker_url


def worker_url_for(node_id: str) -> str:
    if node_id == settings.default_worker_node:
        return settings.local_worker_url
    # Multi-node deployments register worker URLs in env; documented in DEPLOYMENT.md
    import os

    return os.environ.get(f"LUNEL_WORKER_URL_{node_id.upper().replace('-', '_')}",
                          settings.local_worker_url)
