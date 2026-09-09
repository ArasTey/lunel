"""PostgreSQL schema migrations for Lunel Console (applied on startup).

Migrations are plain SQL executed in order and tracked in schema_migrations.
"""
from __future__ import annotations

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = settings.database_url
        # Normalize Railway/Heroku-style URLs to asyncpg requirements.
        if dsn.startswith("postgres://"):
            dsn = dsn.replace("postgres://", "postgresql://", 1)
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10, command_timeout=30)
        await migrate(_pool)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool(request) -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool not initialised")
    return _pool


async def migrate(pool: asyncpg.Pool) -> None:
    await pool.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
    )
    applied = {r["name"] for r in await pool.fetch("SELECT name FROM schema_migrations")}
    for name, sql in MIGRATIONS:
        if name in applied:
            continue
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (name) VALUES ($1)", name)


MIGRATIONS: list[tuple[str, str]] = [
    (
        "0001_init",
        """
        CREATE TABLE IF NOT EXISTS users (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            github_id     BIGINT UNIQUE NOT NULL,
            login         TEXT NOT NULL,
            name          TEXT,
            email         TEXT,
            avatar_url    TEXT,
            is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
            is_disabled   BOOLEAN NOT NULL DEFAULT FALSE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id         TEXT PRIMARY KEY,
            user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            ip         TEXT,
            user_agent TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

        CREATE TABLE IF NOT EXISTS instances (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            slug        TEXT NOT NULL,
            region      TEXT NOT NULL DEFAULT 'local',
            status      TEXT NOT NULL DEFAULT 'stopped',
                -- queued|preparing|building|starting|health_check|running|
                -- failed|stopping|stopped|deleted
            provider    TEXT,            -- 'railway' | 'local' | NULL (auto)
            provider_ref TEXT,           -- railway service id / node instance ref
            core_api_token TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_active_at TIMESTAMPTZ,
            UNIQUE (user_id, slug)
        );
        CREATE INDEX IF NOT EXISTS idx_instances_user ON instances(user_id);
        CREATE INDEX IF NOT EXISTS idx_instances_status ON instances(status);

        CREATE TABLE IF NOT EXISTS instance_configs (
            instance_id  UUID PRIMARY KEY REFERENCES instances(id) ON DELETE CASCADE,
            protocol     TEXT NOT NULL DEFAULT 'vless-ws',
            cpu_limit    REAL NOT NULL DEFAULT 0.5,
            memory_mb    INT NOT NULL DEFAULT 256,
            max_processes INT NOT NULL DEFAULT 128,
            link_quota_bytes BIGINT NOT NULL DEFAULT 0,
            core_version TEXT NOT NULL DEFAULT 'latest',
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS workers (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            node_id     TEXT UNIQUE NOT NULL,
            region      TEXT NOT NULL DEFAULT 'local',
            driver      TEXT NOT NULL DEFAULT 'process',
            status      TEXT NOT NULL DEFAULT 'unknown',   -- online|offline|disabled
            enabled     BOOLEAN NOT NULL DEFAULT TRUE,
            cpu_percent REAL,
            mem_used_mb INT,
            mem_total_mb INT,
            disk_used_gb REAL,
            disk_total_gb REAL,
            instances   INT DEFAULT 0,
            capacity    INT DEFAULT 20,
            last_heartbeat TIMESTAMPTZ,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS deployments (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instance_id UUID NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
            version     INT NOT NULL,
            core_version TEXT NOT NULL DEFAULT 'latest',
            status      TEXT NOT NULL DEFAULT 'queued',
                -- queued|preparing|building|starting|health_check|running|failed|stopping|stopped
            error       TEXT,
            node_id     TEXT,
            started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ,
            duration_ms INT
        );
        CREATE INDEX IF NOT EXISTS idx_deployments_instance ON deployments(instance_id);

        CREATE TABLE IF NOT EXISTS deployment_logs (
            id            BIGSERIAL PRIMARY KEY,
            deployment_id UUID NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
            ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
            level         TEXT NOT NULL DEFAULT 'info',
            message       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_deployment_logs_dep ON deployment_logs(deployment_id);

        CREATE TABLE IF NOT EXISTS domains (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            instance_id UUID NOT NULL REFERENCES instances(id) ON DELETE CASCADE,
            domain      TEXT UNIQUE NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'http',      -- http | tcp
            is_custom   BOOLEAN NOT NULL DEFAULT FALSE,
            is_active   BOOLEAN NOT NULL DEFAULT TRUE,
            provider_ref TEXT,                             -- railway domain id
            tls         BOOLEAN NOT NULL DEFAULT TRUE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_domains_instance ON domains(instance_id);

        CREATE TABLE IF NOT EXISTS metrics (
            id          BIGSERIAL PRIMARY KEY,
            instance_id UUID NOT NULL,
            ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
            cpu_percent REAL,
            mem_mb      REAL,
            connections INT,
            total_bytes BIGINT
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_instance_ts ON metrics(instance_id, ts DESC);

        CREATE TABLE IF NOT EXISTS activity_events (
            id          BIGSERIAL PRIMARY KEY,
            user_id     UUID,
            instance_id UUID,
            kind        TEXT NOT NULL,
            level       TEXT NOT NULL DEFAULT 'info',
            message     TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_events(user_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_activity_instance ON activity_events(instance_id, created_at DESC);
        """,
    ),
    (
        "0002_oauth_states",
        """
        CREATE TABLE IF NOT EXISTS oauth_states (
            state      TEXT PRIMARY KEY,
            redirect   TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """,
    ),
]
