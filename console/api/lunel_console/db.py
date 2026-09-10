"""Database layer for the Lunel Console.

Two backends behind one facade:

* PostgreSQL (asyncpg) — the production backend, used whenever a DSN is
  configured (platform-injected DATABASE_URL / PG* variables).
* SQLite (aiosqlite) — the zero-config fallback so a fresh deployment runs
  with no variables at all (fork → deploy → sign in). Same schema, same
  queries, real persistence.

Queries are written in a dialect-neutral subset (no now(), LATERAL, or
EXTRACT; timestamps passed as ISO-8601 UTC strings; UUIDs generated in
Python). The facade translates ``$N`` placeholders for SQLite and parses
ISO datetime strings back into ``datetime`` objects on read.
"""
from __future__ import annotations

import asyncio
import os
import re
import secrets
import ssl as ssl_module
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg

from .config import settings
from .logging import get

log = get("runtime", "lunel.console.db")

_pool: asyncpg.Pool | None = None
_sqlite: "_SqliteDatabase | None" = None
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class _Row(dict):
    """dict with attribute-style access; strings that look like ISO datetimes
    are parsed on construction so router code can call .isoformat()."""

    def __init__(self, mapping):
        super().__init__(mapping)  # accepts zip/pairs/kwargs
        for key, value in list(self.items()):
            if isinstance(value, str) and ISO_RE.match(value):
                try:
                    self[key] = datetime.fromisoformat(value)
                except ValueError:
                    pass


class _SqliteDatabase:
    """aiosqlite-backed facade matching the asyncpg call surface."""

    mode = "sqlite"

    def __init__(self, path: str):
        p = path.removeprefix("sqlite://")
        # sqlite:///abs/path → /abs/path (three slashes); sqlite://rel → rel
        self._path = p if p.startswith("/") else "/" + p.lstrip("/")
        self._conn = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        import aiosqlite

        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = None
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=10000")
        await self._conn.commit()
        log.info("sqlite database at %s", self._path)

    async def conn_executescript(self, script: str) -> None:
        import aiosqlite

        await self._conn.executescript(script)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # -- $N → ? translation -------------------------------------------------
    @staticmethod
    def _translate(query: str, args: tuple) -> tuple[str, list]:
        positions: list[int] = []

        def _sub(match: re.Match) -> str:
            positions.append(int(match.group(1)))
            return "?"

        query = re.sub(r"\$(\d+)", _sub, query)
        try:
            ordered = [
                a.isoformat() if isinstance(a, datetime) else a
                for a in (args[n - 1] for n in positions)
            ]
        except IndexError as exc:
            raise RuntimeError(f"missing query parameter in: {query[:120]}") from exc
        return query, ordered

    def _mkrow(self, cursor, row: tuple) -> _Row:
        cols = [d[0] for d in cursor.description or []]
        return _Row(zip(cols, row))

    async def fetch(self, query: str, *args) -> list[_Row]:
        q, a = self._translate(query, args)
        async with self._lock:
            cur = await self._conn.execute(q, a)
            rows = [self._mkrow(cur, r) for r in await cur.fetchall()]
            await self._conn.commit()
            await cur.close()
        return rows

    async def fetchrow(self, query: str, *args) -> _Row | None:
        q, a = self._translate(query, args)
        async with self._lock:
            cur = await self._conn.execute(q, a)
            r = await cur.fetchone()
            out = self._mkrow(cur, r) if r is not None else None
            # Commit after every statement: this facade also serves INSERT …
            # RETURNING writes, and SQLite autocommit requires it.
            await self._conn.commit()
            await cur.close()
        return out

    async def fetchval(self, query: str, *args):
        q, a = self._translate(query, args)
        async with self._lock:
            cur = await self._conn.execute(q, a)
            r = await cur.fetchone()
            await self._conn.commit()
            await cur.close()
        return r[0] if r is not None else None

    async def execute(self, query: str, *args) -> str:
        q, a = self._translate(query, args)
        async with self._lock:
            cur = await self._conn.execute(q, a)
            await self._conn.commit()
            status = f"OK {cur.rowcount}"
            await cur.close()
        return status

    def transaction(self):  # unused; executes autocommit per statement
        raise NotImplementedError


class _PostgresDatabase:
    mode = "postgres"

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def close(self) -> None:
        await self._pool.close()

    async def fetch(self, query: str, *args) -> list:
        return await self._pool.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        return await self._pool.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        return await self._pool.fetchval(query, *args)

    async def execute(self, query: str, *args) -> str:
        return await self._pool.execute(query, *args)

    def transaction(self):
        return self._pool.acquire()


db: "_PostgresDatabase | _SqliteDatabase | None" = None


def get_pool(request) -> "_PostgresDatabase | _SqliteDatabase":
    """Backwards-compatible accessor used by routers (``get_pool(request)``)."""
    if db is None:
        raise RuntimeError("database not initialised")
    return db


# ---------------------------------------------------------------------------
# DSN handling (PostgreSQL mode)
# ---------------------------------------------------------------------------
def _normalize_dsn(dsn: str) -> tuple[str, ssl_module.SSLContext | None]:
    """Normalize a DSN for asyncpg and translate `sslmode=` into an SSL context.

    asyncpg does not parse `sslmode` from the query string; platforms like
    Railway/Supabase/Neon commonly append it.
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


def _mask_dsn(dsn: str) -> str:
    try:
        parts = urlsplit(dsn)
        host = parts.hostname or "?"
        port = f":{parts.port}" if parts.port else ""
        return f"{host}{port}{parts.path or ''}"
    except ValueError:
        return "<unparseable dsn>"


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


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    github_id INTEGER UNIQUE,
    login TEXT NOT NULL,
    name TEXT,
    email TEXT,
    avatar_url TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_disabled INTEGER NOT NULL DEFAULT 0,
    password_hash TEXT,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    ip TEXT,
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT 'local',
    status TEXT NOT NULL DEFAULT 'stopped',
    provider TEXT,
    provider_ref TEXT,
    core_api_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_active_at TEXT,
    public_host TEXT,
    UNIQUE (user_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_instances_user ON instances(user_id);
CREATE INDEX IF NOT EXISTS idx_instances_status ON instances(status);
CREATE TABLE IF NOT EXISTS instance_configs (
    instance_id TEXT PRIMARY KEY,
    protocol TEXT NOT NULL DEFAULT 'vless-ws',
    cpu_limit REAL NOT NULL DEFAULT 0.5,
    memory_mb INTEGER NOT NULL DEFAULT 256,
    max_processes INTEGER NOT NULL DEFAULT 128,
    link_quota_bytes INTEGER NOT NULL DEFAULT 0,
    core_version TEXT NOT NULL DEFAULT 'latest',
    protocols TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
    id TEXT PRIMARY KEY,
    node_id TEXT UNIQUE NOT NULL,
    region TEXT NOT NULL DEFAULT 'local',
    driver TEXT NOT NULL DEFAULT 'process',
    status TEXT NOT NULL DEFAULT 'unknown',
    enabled INTEGER NOT NULL DEFAULT 1,
    cpu_percent REAL,
    mem_used_mb INTEGER,
    mem_total_mb INTEGER,
    disk_used_gb REAL,
    disk_total_gb REAL,
    instances INTEGER DEFAULT 0,
    capacity INTEGER DEFAULT 20,
    last_heartbeat TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployments (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    core_version TEXT NOT NULL DEFAULT 'latest',
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    node_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_deployments_instance ON deployments(instance_id);
CREATE TABLE IF NOT EXISTS deployment_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deployment_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deployment_logs_dep ON deployment_logs(deployment_id);
CREATE TABLE IF NOT EXISTS domains (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    domain TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL DEFAULT 'http',
    is_custom INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    provider_ref TEXT,
    tls INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domains_instance ON domains(instance_id);
CREATE TABLE IF NOT EXISTS instance_links (
    id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    link_uuid TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_instance_links_instance ON instance_links(instance_id);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    cpu_percent REAL,
    mem_mb REAL,
    connections INTEGER,
    total_bytes INTEGER
);
CREATE INDEX IF NOT EXISTS idx_metrics_instance_ts ON metrics(instance_id, ts DESC);
CREATE TABLE IF NOT EXISTS activity_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    instance_id TEXT,
    kind TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_instance ON activity_events(instance_id, created_at DESC);
CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    redirect TEXT,
    created_at TEXT NOT NULL
);
"""

POSTGRES_MIGRATIONS = None  # imported lazily below to reuse the SQL list


async def init_pool() -> None:
    """Initialise the database facade. Selects SQLite when the DSN says so,
    or when no PostgreSQL URL is configured at all (zero-config mode)."""
    global db, _sqlite
    if db is not None:
        return
    dsn = (settings.database_url or "").strip()
    if dsn.startswith("sqlite://"):
        _sqlite = _SqliteDatabase(dsn)
        await _sqlite.connect()
        await _sqlite.conn_executescript(SQLITE_SCHEMA)
        db = _sqlite
        await _seed_default_admin()
        log.info("Lunel Console database: embedded SQLite (%s)", _mask_sqlite(dsn))
        return

    if not dsn:
        # Zero-config fallback: embedded SQLite in the data directory.
        from pathlib import Path

        base = None
        for cand in (Path("/data"), Path.cwd() / ".lunel-data"):
            try:
                cand.mkdir(parents=True, exist_ok=True)
                (cand / ".probe").write_text("ok")
                (cand / ".probe").unlink()
                base = cand
                break
            except OSError:
                continue
        base = base or Path("/tmp/lunel-data")
        base.mkdir(parents=True, exist_ok=True)
        sqlite_dsn = f"sqlite:///{base / 'lunel.db'}"
        _sqlite = _SqliteDatabase(sqlite_dsn)
        await _sqlite.connect()
        await _sqlite.conn_executescript(SQLITE_SCHEMA)
        db = _sqlite
        await _seed_default_admin()
        log.warning(
            "no PostgreSQL configured — using embedded SQLite at %s "
            "(attach a PostgreSQL database and set DATABASE_URL for production scale)",
            base / "lunel.db",
        )
        return

    # PostgreSQL mode
    normalized, ssl_ctx = _normalize_dsn(dsn)
    pool = await _connect_with_retry(normalized, ssl_ctx)
    await migrate(pool)
    db = _PostgresDatabase(pool)
    await _seed_default_admin()
    log.info("Lunel Console database: PostgreSQL at %s", _mask_dsn(normalized))


def _mask_sqlite(dsn: str) -> str:
    p = dsn.removeprefix("sqlite://")
    return p if p.startswith("/") else "/" + p.lstrip("/")


async def _seed_default_admin() -> None:
    """Zero-config bootstrap: if no users exist, create admin/admin so the
    panel is usable immediately. Change the password in Admin → System."""
    from .auth.password import hash_password

    assert db is not None
    count = await db.fetchval("SELECT COUNT(*) FROM users")
    if count:
        return
    from datetime import datetime, timezone

    await db.execute(
        "INSERT INTO users (id, login, name, is_admin, password_hash, created_at) "
        "VALUES ($1, 'admin', 'Administrator', 1, $2, $3)",
        secrets.token_hex(16), hash_password("admin"), datetime.now(timezone.utc),
    )
    log.warning("seeded default account admin/admin — change the password "
                "in Admin → System after first login")


async def close_db() -> None:
    global db, _sqlite
    if db is not None:
        await db.close()
    db = None
    _sqlite = None


async def migrate(pool: asyncpg.Pool) -> None:
    from .migrations_pg import MIGRATIONS

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
