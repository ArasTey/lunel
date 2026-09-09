"""Configuration for the Lunel Console API (env-driven, secrets never logged)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    # Database DSN: PostgreSQL (postgres://… or postgresql://…), sqlite:///path,
    # or empty (→ embedded SQLite fallback chosen by db.init_pool).
    database_url: str = os.environ.get("LUNEL_DATABASE_URL", "")
    # GitHub OAuth app credentials
    github_client_id: str = os.environ.get("LUNEL_GITHUB_CLIENT_ID", "")
    github_client_secret: str = os.environ.get("LUNEL_GITHUB_CLIENT_SECRET", "")
    # External base URL of the console (for OAuth redirect)
    public_url: str = os.environ.get("LUNEL_PUBLIC_URL", "http://127.0.0.1:8080")
    # Session signing/encryption key
    secret_key: str = os.environ.get("LUNEL_SECRET_KEY", "")
    # Token the Console uses to talk to workers
    worker_token: str = os.environ.get("LUNEL_WORKER_TOKEN", "")
    # Token workers use for heartbeats (defaults to worker_token)
    heartbeat_token: str = os.environ.get("LUNEL_WORKER_HEARTBEAT_TOKEN", "")
    # First GitHub user to log in becomes admin (bootstrap)
    admin_github_login: str = os.environ.get("LUNEL_ADMIN_GITHUB_LOGIN", "")
    # Domain root for generated instance endpoints
    domain_root: str = os.environ.get("LUNEL_DOMAIN_ROOT", "lunel.app")
    # Edge proxy base (public host that fronts instances)
    edge_base: str = os.environ.get("LUNEL_EDGE_BASE", "")
    # Cookie flags
    cookie_secure: bool = os.environ.get("LUNEL_COOKIE_SECURE", "0") == "1"
    # Default local worker (single-node dev deployments)
    default_worker_node: str = os.environ.get("LUNEL_DEFAULT_WORKER", "local")
    # Worker API base for the default/local worker
    local_worker_url: str = os.environ.get("LUNEL_LOCAL_WORKER_URL", "http://127.0.0.1:9100")

    def validate(self) -> list[str]:
        problems = []
        if not self.secret_key or len(self.secret_key) < 32:
            problems.append("LUNEL_SECRET_KEY must be set (>= 32 chars)")
        if not self.worker_token:
            problems.append("LUNEL_WORKER_TOKEN must be set")
        if not self.github_client_id or not self.github_client_secret:
            problems.append("LUNEL_GITHUB_CLIENT_ID / LUNEL_GITHUB_CLIENT_SECRET must be set")
        return problems


settings = Settings()
