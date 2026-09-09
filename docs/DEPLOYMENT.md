# Deploying Lunel

Three supported targets:

1. **One-click (Lucity / Railway-style)** — deploy the repository root as a
   single service. The unified entrypoint (`main.py`) runs the Console, the
   Worker, and Core-instance management in one process tree. This is the
   RVG-style flow: fork → deploy → sign in → Create Instance.
2. **Lucity multi-service** — console (public domain) + worker
   (internal-only) as separate services.
3. **Self-hosted Docker** — full control, optional wildcard-domain edge.

---

## 1. One-click deploy (recommended, zero required variables)

Lunel runs out of the box with **no environment variables at all**: with no
database configured it uses embedded SQLite (persisted under `/data`), and
with no GitHub OAuth configured the first visitor creates the admin account
via the built-in setup screen.

### Lucity

1. **Fork** this repository to your GitHub account.
2. **Add a service** from your fork:
   - Source: your fork, root directory `/` (repository root)
   - Port: leave the detected port / set `8080`; start command `python main.py`
   - **Generate a domain** in the service settings → this URL is the whole
     platform (console UI, API, and all instance endpoints under `/i/<token>`,
     WebSocket + automatic TLS included).
3. **Deploy.** Open your domain → sign in with the built-in account
   **admin / admin** → **Create Instance** → Deploy → the instance page shows
   a ready endpoint (`https://<your-domain>/i/<token>`) → import the generated
   link into v2rayNG / NekoBox / Streisand.

> Change the default password right after first login (Admin → System →
> Change your password). Set `LUNEL_DEFAULT_ADMIN=0` to disable seeding.

That's the whole deployment. Optional hardening once it runs:

| Upgrade | How |
|---|---|
| PostgreSQL instead of SQLite | Provision a database in the project, then on the service add a **database ref** variable: key `DATABASE_URL`, database `lunel`, key `uri`. Redeploy. (Lucity requires this explicit ref — see its docs; Lunel auto-detects `DATABASE_URL` and all `PG*` variants.) |
| GitHub sign-in instead of password | Create a GitHub OAuth App (callback `https://<your-domain>/auth/callback`), set `LUNEL_GITHUB_CLIENT_ID` / `LUNEL_GITHUB_CLIENT_SECRET` as service variables. |
| Public-domain metadata | Set `LUNEL_PUBLIC_URL=https://<your-domain>` and `LUNEL_COOKIE_SECURE=1`. |

Data note: SQLite persists in `/data/lunel.db`; attach a Lucity volume to
`/data` so it survives redeploys, or switch to PostgreSQL as above.

> **Instance isolation note:** on managed platforms the unified service uses
> the **process driver** (OS rlimits + per-instance data dirs). Container-level
> isolation (Docker driver) applies in self-hosted mode, or when the platform
> supports DinD-sidecars.

### Railway (optional per-instance-domains mode)

Set `LUNEL_RAILWAY_TOKEN`, and project/environment IDs (auto-injected when
the console itself runs on Railway). Each instance is then deployed as its
own Railway service with a generated public domain — verified against the
current Railway GraphQL API. Without those variables, Railway uses the same
single-service behavior as above.

---

## 2. Lucity multi-service (separate worker node)

For larger deployments, split the console and worker:

| Service | Root directory | Port | Domain | Notes |
|---|---|---|---|---|
| `console` | `console/api` | 8080 | **attach** (public) | env vars as in section 1 + `LUNEL_LOCAL_WORKER_URL=http://worker:9100` |
| `worker` | `worker` | 9100 | **none** (internal-only) | `LUNEL_WORKER_TOKEN` shared with console; `LUNEL_CONSOLE_URL=http://console:8080`; set `LUNEL_CORE_PYTHON=python`, `LUNEL_CORE_CWD=core` with root-directory context containing `core/` |

Services without domains are internal-only on Lucity — exactly what the
worker should be. The console remains the only public endpoint.

---

## 3. Self-hosted Docker

```bash
export LUNEL_PG_PASSWORD=$(openssl rand -hex 16)
export LUNEL_SECRET_KEY=$(openssl rand -hex 32)
export LUNEL_WORKER_TOKEN=$(openssl rand -hex 32)
export LUNEL_PUBLIC_URL=https://lunel.example.com
export LUNEL_GITHUB_CLIENT_ID=...
export LUNEL_GITHUB_CLIENT_SECRET=...
export LUNEL_ADMIN_GITHUB_LOGIN=yourlogin
export LUNEL_COOKIE_SECURE=1

cd deploy/docker && docker compose up -d --build
```

- Console: `http://127.0.0.1:8080` (put your own TLS proxy in front, or
  uncomment the `caddy` service for automatic Let's Encrypt).
- Worker: uses the **process driver** by default inside its container.
  For real container isolation enable the Docker driver:
  uncomment the docker.sock mount (read-only, worker only) and set
  `LUNEL_WORKER_DRIVER=docker`, then build the Core image:
  `docker build -t lunel/core:latest ../../core`.
- Optional wildcard endpoints: point `*.lunel.example.com` at the host and
  uncomment the Caddy service (`deploy/proxy/Caddyfile.template`).

---

## Environment variables reference

### Console / unified service

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8080` | Public listen port (platform-injected) |
| `LUNEL_DATABASE_URL` | falls back to `DATABASE_URL` | PostgreSQL DSN |
| `LUNEL_SECRET_KEY` | auto-generated + persisted | Session hashing context |
| `LUNEL_WORKER_TOKEN` | auto-generated (unified) / required (split) | Shared secret with workers |
| `LUNEL_GITHUB_CLIENT_ID/SECRET` | — | OAuth login |
| `LUNEL_PUBLIC_URL` | `http://127.0.0.1:$PORT` | Canonical console origin (OAuth redirect, endpoint URLs) |
| `LUNEL_ADMIN_GITHUB_LOGIN` | — | Bootstrap admin |
| `LUNEL_COOKIE_SECURE` | `0` | Set `1` behind HTTPS |
| `LUNEL_LOCAL_WORKER_URL` | auto (unified) / `http://127.0.0.1:9100` | Default worker API |
| `LUNEL_DOMAIN_ROOT` | `lunel.app` | Informational for provider domains |

### Worker (split deployments)

| Variable | Default | Purpose |
|---|---|---|
| `LUNEL_WORKER_TOKEN` | — (required) | Shared secret |
| `LUNEL_CONSOLE_URL` | — | Heartbeat target (optional) |
| `LUNEL_NODE_ID` / `LUNEL_NODE_REGION` | `local` | Scheduler identity |
| `LUNEL_WORKER_DRIVER` | auto (`docker` if available, else `process`) | Isolation driver |
| `LUNEL_WORKER_DATA` | `/var/lib/lunel/instances` | Instance data root |
| `LUNEL_WORKER_PORT_START/END` | `19000-19999` | Port allocation range |
| `LUNEL_CORE_PYTHON` / `LUNEL_CORE_CWD` | `.venv/bin/python` / — | How to launch Core (process driver) |
| `LUNEL_NODE_CAPACITY` | `20` | Max instances reported to scheduler |

### Core

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8000` | Listen port (allocated automatically by the worker) |
| `LUNEL_CORE_API_TOKEN` | — | Management API bearer (required for it to be enabled) |
| `LUNEL_STATE_PATH` | `/data/state.json` | Persistence |
| `LUNEL_LOG_LEVEL` / `LUNEL_LOG_JSON` | `info` / `0` | Logging |
| `LUNEL_VERSION` / `LUNEL_BUILD` / `LUNEL_COMMIT` | `1.0.0`/`dev`/`unknown` | Version report |
