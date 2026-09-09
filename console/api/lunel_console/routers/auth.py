"""Auth routes: GitHub OAuth, first-run setup, password login, sessions."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ..auth import github, password as password_auth, sessions
from ..config import settings
from ..db import get_pool
from ..security.ratelimit import RULES, client_ip, limiter

router = APIRouter(prefix="/auth", tags=["auth"])


def _limiter_rule_auth():
    return RULES["auth"]


@router.get("/status")
async def auth_status(request: Request):
    """Drives the login screen: which auth paths exist, and whether the
    first-run admin setup is still available."""
    pool = get_pool(request)
    users_exist = await pool.fetchval("SELECT COUNT(*) FROM users")
    return {
        "oauth": bool(settings.github_client_id and settings.github_client_secret),
        "password_login": True,
        "needs_setup": users_exist == 0,
    }


@router.get("/login")
async def login_redirect(request: Request):
    """Kick off GitHub OAuth. Rate limited per IP."""
    limiter.check(f"auth:{client_ip(request)}", _limiter_rule_auth())
    if not settings.github_client_id or not settings.github_client_secret:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured")
    state = secrets.token_urlsafe(24)
    pool = get_pool(request)
    await pool.execute(
        "INSERT INTO oauth_states (state, created_at) VALUES ($1, $2)",
        state, datetime.now(timezone.utc),
    )
    await pool.execute(
        "DELETE FROM oauth_states WHERE created_at < $1",
        (datetime.now(timezone.utc) - timedelta(hours=1)),
    )
    return RedirectResponse(github.authorize_url(state))


@router.get("/callback")
async def oauth_callback(request: Request, code: str = "", state: str = ""):
    limiter.check(f"auth:{client_ip(request)}", _limiter_rule_auth())
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

    resp = RedirectResponse("/", status_code=302)
    sessions.set_session_cookie(resp, token)
    return resp


@router.post("/setup")
async def setup_first_admin(request: Request):
    """First-run bootstrap (zero-config deployments): the first visitor
    creates the admin account. Disabled forever once any user exists."""
    limiter.check(f"auth:{client_ip(request)}", _limiter_rule_auth())
    pool = get_pool(request)
    users_exist = await pool.fetchval("SELECT COUNT(*) FROM users")
    if users_exist:
        raise HTTPException(status_code=409, detail="setup already completed")
    body = await request.json()
    name = str(body.get("name") or "").strip()
    pwd = str(body.get("password") or "")
    password_auth.validate_new_account(pwd, name)

    user_id = str((await pool.fetchrow(
        """
        INSERT INTO users (id, login, name, is_admin, password_hash, created_at)
        VALUES ($1, $2, $3, TRUE, $4, $5)
        RETURNING id
        """,
        secrets.token_hex(16), name, name,
        password_auth.hash_password(pwd), datetime.now(timezone.utc),
    ))["id"])
    token = await sessions.create_session(pool, user_id, request)
    resp = JSONResponse({"ok": True})
    sessions.set_session_cookie(resp, token)
    return resp


@router.post("/login-password")
async def login_password(request: Request):
    limiter.check(f"auth:{client_ip(request)}", _limiter_rule_auth())
    pool = get_pool(request)
    body = await request.json()
    name = str(body.get("name") or "").strip()
    pwd = str(body.get("password") or "")
    row = await pool.fetchrow(
        "SELECT id, is_disabled, password_hash FROM users WHERE login = $1", name
    )
    if row is None or not password_auth.verify_password(pwd, row["password_hash"]):
        raise HTTPException(status_code=401, detail="invalid name or password")
    if row["is_disabled"]:
        raise HTTPException(status_code=403, detail="account disabled")
    token = await sessions.create_session(pool, row["id"], request)
    resp = JSONResponse({"ok": True})
    sessions.set_session_cookie(resp, token)
    return resp


@router.post("/change-password")
async def change_password(request: Request):
    pool = get_pool(request)
    user = await sessions.get_session_user(pool, request)
    sessions.require_user(user)
    sessions.check_csrf(request, user)
    body = await request.json()
    current = str(body.get("current_password") or "")
    new = str(body.get("new_password") or "")
    if len(new) < 8:
        raise HTTPException(status_code=400, detail="new password must be at least 8 characters")
    row = await pool.fetchrow("SELECT password_hash FROM users WHERE id = $1", user["id"])
    if not password_auth.verify_password(current, row["password_hash"]):
        raise HTTPException(status_code=400, detail="current password is incorrect")
    await pool.execute(
        "UPDATE users SET password_hash = $2 WHERE id = $1",
        user["id"], password_auth.hash_password(new),
    )
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request):
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
            "is_admin": bool(user["is_admin"]),
        },
        "csrf_token": sessions.csrf_token(request),
    }
