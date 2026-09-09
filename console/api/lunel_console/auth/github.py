"""GitHub OAuth (authorization-code flow) with CSRF-protected state.

Only the minimum account fields are stored. The client secret never leaves
the server; tokens are used once and discarded (no GitHub API persistence).
"""
from __future__ import annotations

import httpx
from fastapi import HTTPException

from ..config import settings

GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


def authorize_url(state: str, redirect_path: str = "/dashboard") -> str:
    from urllib.parse import quote

    redirect_uri = f"{settings.public_url.rstrip('/')}/auth/callback"
    return (
        f"{GITHUB_AUTHORIZE_URL}?client_id={settings.github_client_id}"
        f"&redirect_uri={quote(redirect_uri)}"
        f"&scope=read:user%20user:email"
        f"&state={quote(state)}"
        f"&allow_signup=true"
    )


async def exchange_code(code: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            GITHUB_TOKEN_URL,
            json={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
                "redirect_uri": f"{settings.public_url.rstrip('/')}/auth/callback",
            },
            headers={"Accept": "application/json"},
        )
    if resp.status_code != 200 or "access_token" not in resp.json():
        raise HTTPException(status_code=502, detail="GitHub OAuth exchange failed")
    return resp.json()["access_token"]


async def fetch_identity(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        user_resp = await client.get(
            GITHUB_USER_URL,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        if user_resp.status_code != 200:
            raise HTTPException(status_code=502, detail="GitHub user fetch failed")
        profile = user_resp.json()
        email = profile.get("email")
        if not email:
            emails_resp = await client.get(
                GITHUB_EMAILS_URL,
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            if emails_resp.status_code == 200:
                primary = next((e for e in emails_resp.json() if e.get("primary")), None)
                email = primary["email"] if primary else None
    return {
        "github_id": int(profile["id"]),
        "login": profile["login"],
        "name": profile.get("name"),
        "email": email,
        "avatar_url": profile.get("avatar_url"),
    }


async def upsert_user(pool, identity: dict):
    row = await pool.fetchrow(
        """
        INSERT INTO users (github_id, login, name, email, avatar_url, last_login_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (github_id) DO UPDATE
          SET login = EXCLUDED.login,
              name = EXCLUDED.name,
              email = EXCLUDED.email,
              avatar_url = EXCLUDED.avatar_url,
              last_login_at = now()
        RETURNING id, is_admin, is_disabled
        """,
        identity["github_id"], identity["login"], identity["name"],
        identity["email"], identity["avatar_url"],
    )
    if row["is_disabled"]:
        raise HTTPException(status_code=403, detail="account disabled")
    # Bootstrap: configured admin login gets the admin flag on first login.
    if settings.admin_github_login and identity["login"] == settings.admin_github_login:
        if not row["is_admin"]:
            row = await pool.fetchrow(
                "UPDATE users SET is_admin = TRUE WHERE id = $1 RETURNING id, is_admin, is_disabled",
                row["id"],
            )
    return row
