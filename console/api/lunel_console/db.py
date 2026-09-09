"""PostgreSQL schema migrations for Lunel Console (applied on startup).

Migrations are plain SQL executed in order and tracked in schema_migrations.
"""
from __future__ import annotations

import asyncio
import ssl as ssl_module
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from .config import settings
from .logging import get

log = get("runtime", "lunel.console.db")

_pool: asyncpg.Pool | None = None


def _normalize_dsn(dsn: str) -> tuple[str, ssl_module.SSLContext | None]:
    """Normalize a DSN for asyncpg and translate `sslmode=` into an SSL context.

    asyncpg does not parse `sslmode` from the query string; platforms like
    Railway/Supabase/Neon commonly append it. Returns (dsn_without_sslmode,
    ssl_context_or_None).
    """
    parts = urlsplit(dsn)
    if parts.scheme == "postgres":
        parts = parts._replace(scheme="postgresql")
    query = dict(parse_qsl(parts.query))
    sslmode = (query.pop("sslmode", "") or query.pop("ssl", "")).lower()
    dsn2 = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    ctx: ssl_module.SSLContext | None = None
    if sslmode in ("require", "prefer", "verify-ca", "verify-full"):
        ctx = ssl_module.create_default_context()
        if sslmode in ("require", "prefer", "verify-ca"):
            # 'require' means encrypt without certificate verification.
            ctx.check_hostname = False
            ctx.verify_mode = ssl_module.CERT_NONE
    return dsn2, ctx


async def _connect_with_retry(dsn: str, ssl_ctx: ssl_module.SSLContext | None) -> asyncpg.Pool:
    """Connect, tolerating the platform start-up race where the database is
    still provisioning. Auth/config errors fail immediately with a clear
    message; connection errors retry for ~90 seconds."""
    last_error: Exception | None = None
    for attempt in range(1, 31):
        try:
            return await asyncpg.create_pool(
                dsn, min_size=2, max_size=10, command_timeout=30, ssl=ssl_ctx
            )
        except asyncpg.exceptions.InvalidPasswordError as exc:
            raise RuntimeError(
                "PostgreSQL rejected the credentials in LUNEL_DATABASE_URL / DATABASE_URL. "
                "Check the database's user/password variables."
            ) from exc
        except asyncpg.exceptions.InvalidCatalogNameError as exc:
            raise RuntimeError(
                "PostgreSQL database (the name in the DSN) does not exist yet. "
                "Check the database name in DATABASE_URL."
            ) from exc
        except (OSError, asyncpg.PostgresError) as exc:
            last_error = exc
            if attempt in (1, 5, 15, 30):
                log.warning("database not reachable (attempt %d/30): %s — retrying…",
                            attempt, type(exc).__name__)
            await asyncio.sleep(3)
    host_hint = _mask_dsn(dsn)
    raise RuntimeError(
        f"Could not reach PostgreSQL at {host_hint} after 90s of retries "
        f"({type(last_error).__name__}: {last_error}). Check that the database "
        "service is running and its variables are wired to this service."
    )


def _mask_dsn(dsn: str) -> str:
    from urllib.parse import urlsplit

    try:
        parts = urlsplit(dsn)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}{parts.path or ''}"
    except ValueError:
        return "<unparseable dsn>"


async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn, ssl_ctx = _normalize_dsn(settings.database_url)
        _pool = await _connect_with_retry(dsn, ssl_ctx)
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
