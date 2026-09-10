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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import version
from .db import close_db, init_pool
from .logging import get, setup_logging
from .panel import router as panel_router
from .routers import admin, auth, domains, instances, internal
from .security.ratelimit import RULES, client_ip, limiter
from .services.gateway import router as gateway_router

setup_logging()
log = get("runtime", "lunel.console")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

app = FastAPI(title="Lunel Console", docs_url=None, redoc_url=None,
              version=version.version())

app.include_router(panel_router)
app.include_router(auth.router)
app.include_router(instances.router)
app.include_router(domains.router)
app.include_router(admin.router)
app.include_router(internal.router)
app.include_router(gateway_router)


@app.get("/", include_in_schema=False)
async def panel_home():
    from .panel import PAGE

    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Lunel Console", "version": version.version()}


@app.get("/ready")
async def ready():
    from .db import db

    return {"ready": db is not None, "backend": getattr(db, "mode", None)}


@app.get("/version")
async def version_endpoint():
    return version.info()


@app.exception_handler(404)
async def spa_fallback(request, exc):
    """Serve the self-contained panel for unknown browser paths."""
    path = request.url.path
    if path.startswith(("/api", "/auth", "/i/", "/worker")) or path == "/health":
        return JSONResponse({"detail": "not found"}, status_code=404)
    from .panel import PAGE

    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})


@contextlib.asynccontextmanager
async def lifespan(_app):
    await init_pool()
    log.info("Lunel Console %s started", version.version())
    yield
    await close_db()
    log.info("Lunel Console stopped")


app.router.lifespan_context = lifespan

if (FRONTEND_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")
