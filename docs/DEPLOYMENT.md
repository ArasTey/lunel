# Deploying Lunel

Two supported targets:

1. **Lucity** (lucity.cloud) — the primary path. One project, two services,
   one-click PostgreSQL. The console's generated domain is the **only public
   endpoint** (that is how Lucity works), and every instance rides it under a
   private `/i/<token>` path with automatic TLS.
2. **Self-hosted Docker** — full control, optional wildcard-domain edge.

---

## 1. Deploying on Lucity

### Prerequisites

- A Lucity account with GitHub connected (Sign in → connect GitHub).
- This repository pushed to GitHub.
- A GitHub OAuth App: Settings → Developer settings → OAuth Apps → **New**.
  - Homepage: your future console URL (fill in after first deploy; update later).
  - Callback: `https://<console-domain>/auth/callback`
  - Keep the client secret for step 4.

### Step-by-step

**1. Create the project** — Lucity dashboard → New Project → name it `lunel`.
A `development` environment is created automatically.

**2. Provision PostgreSQL** — In the project, add the one-click **PostgreSQL**
database. Lucity wires connection credentials as environment variables; note
the `DATABASE_URL` it injects.

**3. Add the `console` service**

- Source: this GitHub repository.
- Root directory / context path: `console/api`
- Port: `8080`
- Start command (if you want it explicit): `python -m lunel_console`
- Generate a domain in the service settings → the console URL
  `https://console-<env>.<workload-domain>` (WebSocket + TLS automatic).

**4. Set the console's environment variables**

| Variable | Value |
|---|---|
| `LUNEL_DATABASE_URL` | Lucity's `DATABASE_URL` value (postgres://… or postgresql://…) |
| `LUNEL_SECRET_KEY` | `openssl rand -hex 32` |
| `LUNEL_WORKER_TOKEN` | `openssl rand -hex 32` |
| `LUNEL_GITHUB_CLIENT_ID` / `LUNEL_GITHUB_CLIENT_SECRET` | From your OAuth App |
| `LUNEL_PUBLIC_URL` | `https://console-<env>.<workload-domain>` (the generated domain) |
| `LUNEL_COOKIE_SECURE` | `1` |
| `LUNEL_ADMIN_GITHUB_LOGIN` | your GitHub login (bootstrap admin) |

**5. Add the `worker` service**

- Source: same repository. Root directory: `worker`
- Port: `9100` — **do NOT attach a domain.** Services without domains are
  internal-only, exactly what we want.
- Environment:

| Variable | Value |
|---|---|
| `LUNEL_WORKER_TOKEN` | same value as the console's |
| `LUNEL_CONSOLE_URL` | `http://worker` is not needed; heartbeats target the console: `http://console:9100`'s sibling — use `http://console:8080` |
| `LUNEL_NODE_ID` | `local` |
| `LUNEL_NODE_REGION` | e.g. `eu-central` |
| `LUNEL_CORE_PYTHON` / `LUNEL_CORE_CWD` | not needed on Lucity — railpack builds `core/`… see the note below |

> **Core runtime on Lucity:** the worker launches instances with the
> **process driver** (container-in-container is not possible on Kubernetes
> PaaS platforms). The cleanest setup is to build the Core package into the
> worker's image: add `core/` next to `worker/` in the worker service's
> context (set the service's root directory to the repository root and start
> command `python -m lunel_worker` with `LUNEL_CORE_PYTHON=python` and
> `LUNEL_CORE_CWD=core`). The Dockerfile in `deploy/docker/worker.Dockerfile`
> shows exactly this layout; railpack supports a `Dockerfile` if you prefer
> to point the service at `deploy/docker/worker.Dockerfile`.

**6. Deploy both services** — push to GitHub (or hit Deploy). Watch the build
logs; both services are plain Python with `requirements.txt`.

**7. Verify** — open the console URL → sign in with GitHub → Create Instance →
Deploy. The instance page shows the private endpoint
`https://console-<env>.<workload-domain>/i/<token>` — import the generated
share link into your client. WebSocket relay works through the platform
gateway automatically.

**8. First admin** — the first login matching `LUNEL_ADMIN_GITHUB_LOGIN`
becomes admin.

### Scaling & multiple nodes on Lucity

Every Lunel "region" is one worker service. Add `worker-eu`, `worker-us`, …
(internal-only), then create `workers` rows via heartbeat automatically; the
scheduler prefers the region the user picked in the wizard.

---

## 2. Self-hosting with Docker

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

### Optional: Railway provider

When `LUNEL_RAILWAY_TOKEN` + project/environment are configured, the Console
deploys each instance as its own Railway service with a generated public
domain (verified against the current Railway GraphQL API: `serviceCreate`,
`serviceInstanceUpdate`, `serviceDomainCreate`, `serviceInstanceDeployV2`,
`deploymentRedeploy`/`deploymentStop`, `deploymentLogs`). Image source is
`LUNEL_CORE_IMAGE` (default `ghcr.io/lunelsh/lunel-core:latest` — build and
push it once).

---

## Environment variables reference

### Console

| Variable | Default | Purpose |
|---|---|---|
| `LUNEL_DATABASE_URL` | `postgres://lunel:lunel@127.0.0.1:5432/lunel` | PostgreSQL DSN |
| `LUNEL_SECRET_KEY` | — (required, ≥32 chars) | Session hashing context |
| `LUNEL_WORKER_TOKEN` | — (required) | Shared secret with workers |
| `LUNEL_GITHUB_CLIENT_ID/SECRET` | — | OAuth |
| `LUNEL_PUBLIC_URL` | `http://127.0.0.1:8080` | Canonical console origin (OAuth redirect, endpoint URLs) |
| `LUNEL_ADMIN_GITHUB_LOGIN` | — | Bootstrap admin |
| `LUNEL_COOKIE_SECURE` | `0` | Set `1` behind HTTPS |
| `LUNEL_LOCAL_WORKER_URL` | `http://127.0.0.1:9100` | Default worker API |
| `LUNEL_DOMAIN_ROOT` | `lunel.app` | Informational for provider domains |

### Worker

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
| `PORT` | `8000` | Listen port |
| `LUNEL_CORE_API_TOKEN` | — | Management API bearer (required for it to be enabled) |
| `LUNEL_STATE_PATH` | `/data/state.json` | Persistence |
| `LUNEL_LOG_LEVEL` / `LUNEL_LOG_JSON` | `info` / `0` | Logging |
| `LUNEL_VERSION` / `LUNEL_BUILD` / `LUNEL_COMMIT` | `1.0.0`/`dev`/`unknown` | Version report |
