"""Lunel Console API — ASGI application.

Serves:
* the Console API under /api and /auth
* the public instance gateway at /i/<token>/... (HTTP + WebSocket)
* the static frontend (console/frontend) for everything else (SPA)
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import version
from .db import close_pool, init_pool
from .logging import get, setup_logging
from .routers import admin, auth, domains, instances, internal
from .security.ratelimit import RULES, client_ip, limiter
from .services.gateway import router as gateway_router

setup_logging()
log = get("runtime", "lunel.console")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="Lunel Console", docs_url=None, redoc_url=None,
              version=version.version())

app.include_router(auth.router)
app.include_router(instances.router)
app.include_router(domains.router)
app.include_router(admin.router)
app.include_router(internal.router)
app.include_router(gateway_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Lunel Console", "version": version.version()}


@app.get("/ready")
async def ready():
    from .db import _pool

    return {"ready": _pool is not None}


@app.get("/version")
async def version_endpoint():
    return version.info()


@app.exception_handler(404)
async def spa_fallback(request, exc):
    """Serve the SPA for unknown non-API paths (client-side routing)."""
    path = request.url.path
    if path.startswith(("/api", "/auth", "/i/", "/worker")) or path == "/health":
        return JSONResponse({"detail": "not found"}, status_code=404)
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "frontend not built"}, status_code=404)


@contextlib.asynccontextmanager
async def lifespan(_app):
    await init_pool()
    log.info("Lunel Console %s started", version.version())
    yield
    await close_pool()
    log.info("Lunel Console stopped")


app.router.lifespan_context = lifespan

if (FRONTEND_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
