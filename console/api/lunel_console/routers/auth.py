"""Auth routes: GitHub OAuth + session cookie + CSRF exposure."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..auth import github, sessions
from ..config import settings
from ..db import get_pool
from ..security.ratelimit import client_ip, limiter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login_redirect(request: Request):
    """Kick off GitHub OAuth. Rate limited per IP."""
    limiter.check(f"auth:{client_ip(request)}", limiter_rule_auth())
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
    state = secrets.token_urlsafe(24)
    pool = get_pool(request)
    await pool.execute(
        "INSERT INTO oauth_states (state) VALUES ($1)", state
    )
    # opportunistic cleanup of stale states (older than 1h)
    await pool.execute("DELETE FROM oauth_states WHERE created_at < now() - interval '1 hour'")
    return RedirectResponse(github.authorize_url(state))


def limiter_rule_auth():
    from ..security.ratelimit import RULES

    return RULES["auth"]


@router.get("/callback")
async def oauth_callback(request: Request, code: str = "", state: str = ""):
    limiter.check(f"auth:{client_ip(request)}", limiter_rule_auth())
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code/state")
    pool = get_pool(request)
    row = await pool.fetchrow("DELETE FROM oauth_states WHERE state = $1 RETURNING state", state)
    if row is None:
        raise HTTPException(status_code=400, detail="invalid or expired OAuth state")

    access_token = await github.exchange_code(code)
    identity = await github.fetch_identity(access_token)
    user = await github.upsert_user(pool, identity)
    token = await sessions.create_session(pool, user["id"], request)

    redirect_target = "/"
    resp = RedirectResponse(redirect_target, status_code=302)
    sessions.set_session_cookie(resp, token)
    return resp


@router.post("/logout")
async def logout(request: Request, user: asyncpg.Record | None = Depends(lambda r: None)):
    pool = get_pool(request)
    current = await sessions.get_session_user(pool, request)
    if current is not None:
        await sessions.destroy_session(pool, request)
    resp = JSONResponse({"ok": True})
    sessions.clear_session_cookie(resp)
    return resp


@router.get("/me")
async def me(request: Request):
    pool = get_pool(request)
    user = await sessions.get_session_user(pool, request)
    if user is None:
        return {"authenticated": False}
    return {
        "authenticated": True,
        "user": {
            "id": str(user["id"]),
            "login": user["login"],
            "name": user["name"],
            "avatar_url": user["avatar_url"],
            "is_admin": user["is_admin"],
        },
        "csrf_token": sessions.csrf_token(request),
    }
