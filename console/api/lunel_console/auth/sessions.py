"""Session management: opaque tokens, HttpOnly cookies, hashed storage,
CSRF protection, and a strict per-user session count cap.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import HTTPException, Request

from ..config import settings

SESSION_COOKIE = "lunel_session"
SESSION_TTL = timedelta(days=30)
MAX_SESSIONS_PER_USER = 100


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(pool: asyncpg.Pool, user_id: str, request: Request) -> str:
    token = secrets.token_urlsafe(32)
    await pool.execute(
        """
        INSERT INTO sessions (id, user_id, created_at, expires_at, ip, user_agent)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        _hash_token(token),
        user_id,
        datetime.now(timezone.utc),
        (datetime.now(timezone.utc) + SESSION_TTL),
        request.client.host if request.client else None,
        (request.headers.get("user-agent") or "")[:200],
    )
    # Cap the number of active sessions per user.
    await pool.execute(
        """
        DELETE FROM sessions
        WHERE user_id = $1 AND id NOT IN (
            SELECT id FROM sessions WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2
        )
        """,
        user_id,
        MAX_SESSIONS_PER_USER,
    )
    return token


async def get_session_user(pool: asyncpg.Pool, request: Request) -> asyncpg.Record | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = await pool.fetchrow(
        """
        SELECT u.id, u.github_id, u.login, u.name, u.email, u.avatar_url,
               u.is_admin, u.is_disabled, s.expires_at
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.id = $1 AND s.expires_at > $2
        """,
        _hash_token(token),
        datetime.now(timezone.utc),
    )
    if row is None:
        return None
    if row["is_disabled"]:
        return None
    return row


async def destroy_session(pool: asyncpg.Pool, request: Request) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await pool.execute("DELETE FROM sessions WHERE id = $1", _hash_token(token))


async def destroy_all_sessions(pool: asyncpg.Pool, user_id: str) -> None:
    await pool.execute("DELETE FROM sessions WHERE user_id = $1", user_id)


def require_user(user: asyncpg.Record | None) -> asyncpg.Record:
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def require_admin(user: asyncpg.Record | None) -> asyncpg.Record:
    user = require_user(user)
    if not user["is_admin"]:
        raise HTTPException(status_code=403, detail="admin access required")
    return user


def set_session_cookie(response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def check_csrf(request: Request, user: asyncpg.Record | None) -> None:
    """Synchronizer-token CSRF defense for state-changing requests.

    The session token itself is the CSRF secret; mutations must repeat it in
    the X-Lunel-CSRF header. This works because the cookie is HttpOnly and
    SameSite=Lax, so a cross-site attacker cannot read or replay it.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    if user is None:
        return
    cookie_token = request.cookies.get(SESSION_COOKIE) or ""
    header_token = request.headers.get("x-lunel-csrf") or ""
    if not header_token or not secrets.compare_digest(header_token, cookie_token):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


def csrf_token(request: Request) -> str:
    """Value the frontend must send in X-Lunel-CSRF (equal to the cookie)."""
    return request.cookies.get(SESSION_COOKIE) or ""


def now_ts() -> float:
    return time.time()
