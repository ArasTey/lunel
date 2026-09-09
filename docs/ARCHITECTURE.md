# Lunel Architecture

## Overview

Lunel is a control-plane / data-plane system with three components:

```
                        ┌────────────────────────────────────────────┐
                        │                LUNEL CONSOLE               │
   Browser ────────────▶│  frontend (SPA) ── Console API (FastAPI)   │
                        │        PostgreSQL      │      │            │
                        └────────────────────────┼──────┼────────────┘
                             GitHub OAuth        │      │ heartbeat /
                             sessions            │ deploy│ metrics
                                                 ▼      ▼
                        ┌────────────────────────────────────────────┐
                        │                 LUNEL WORKER               │
                        │  scheduler-facing API · drivers · ws-proxy │
                        └───────────────┬────────────────────────────┘
                                        │ launch / stop / proxy
                                        ▼
                        ┌────────────────────────────────────────────┐
                        │  LUNEL CORE instances (isolated per user)  │
                        │  VLESS / Trojan / Shadowsocks over WS+xHTTP│
                        └───────────────┬────────────────────────────┘
                                        │ relay
                                        ▼
                                  destination hosts
```

Public traffic from proxy clients takes one hop:

```
client ──▶ https://<console>/i/<endpoint-token>/ws/<uuid> ──▶ gateway ──▶
             worker ws-proxy ──▶ core /ws/<uuid> ──▶ destination
```

The gateway authenticates by endpoint token (un guessable, rotatable,
per-instance), so no port mapping or per-instance DNS is required on
platforms exposing a single HTTP endpoint. On wildcard-DNS self-hosted
setups the same path is additionally reachable as a real hostname via the
bundled Caddy.

## Lunel Core

Derived from RVG's relay engine (wire-compatible), restructured:

| Module | Responsibility |
|---|---|
| `relay/base.py` | `RelayContext` (explicit dependency object, no panel globals), adaptive `QuotaGate` (EWMA batched quota accounting), socket tuning, pump helpers |
| `relay/vless.py` | VLESS header parse/build, `/ws/{uuid}` tunnel (credential check on first frame) |
| `relay/trojan.py` | Trojan SHA-224 handshake, `/trojan-ws` tunnel |
| `relay/shadowsocks.py` | EVP_BytesToKey + HKDF-SHA1 + streaming AEAD, `/ss-ws`, link identification by successful decryption |
| `relay/xhttp.py` | **One** xHTTP engine (RVG had two 95%-duplicated ones) serving `/xhttp-siz10/*` (VLESS) and `/txhttp-siz10/*` (Trojan): packet-up with ordered seq replay, stream-up with AIMD adaptive drain, queue-fed downlink, idle reaper |
| `state.py` | `LinkStore` / `ConnectionTracker` / `RuntimeStats` with explicit locks; atomic JSON persistence with debounced saves (corrupt files are quarantined, never crash boot) |
| `app.py` | Route assembly; management API guarded by `LUNEL_CORE_API_TOKEN` bearer auth |

Security properties relative to RVG:

1. Sessions keyed `(uuid, session_id)` — closes the cross-link session attachment hole.
2. `MAX_SESSIONS_GLOBAL` / `MAX_SESSIONS_PER_LINK` / seq-buffer caps / body caps.
3. UUID credential match verified in the first WS frame for VLESS.
4. All errors go through the redacting logger; ring buffer keeps the last 500 lines for the Console log viewer.

Core exposes exactly two kinds of endpoints:

- **Public/unauthenticated:** `/health`, `/ready`, `/version` and the protocol transports (protocol credentials are the authentication).
- **Management** (`/core/api/*`): bearer-token only. The token is generated per instance by the Console, handed to the Worker at launch, and never returned by any Console API.

## Lunel Console

FastAPI + asyncpg. Migrations are plain SQL applied in order and tracked in
`schema_migrations`.

Entities: `users`, `sessions`, `instances`, `instance_configs`, `deployments`,
`deployment_logs`, `domains`, `metrics`, `activity_events`, `workers`,
`oauth_states`.

Key flows:

- **Auth** — GitHub OAuth code flow with single-use server-side state;
  opaque session tokens (SHA-256 hashed at rest), HttpOnly + SameSite=Lax
  cookies; mutations additionally require the `X-Lunel-CSRF` header (the
  session token itself — unreadable to other origins by construction).
- **Deploy pipeline** (`services/deployments.py`) — background task per
  deployment, real transitions only:
  `QUEUED → PREPARING → BUILDING → STARTING → HEALTH_CHECK → RUNNING|FAILED`.
  Failure cleans up the half-launched instance on the worker.
- **Gateway** (`services/gateway.py`) — endpoint-token lookup → worker HTTP
  proxy for management/data paths; frame-level WS relay (client ⇄ gateway ⇄
  worker `ws-proxy` ⇄ Core) because HTTP clients cannot pass an Upgrade
  through.
- **Providers** — `local` (Worker) is default; `railway` (per-instance
  service + generated domain) activates automatically when Railway
  credentials are present. Lucity uses `local` with the console itself
  running as the single public service.

## Lunel Worker

- **Drivers** share one interface: `DockerDriver` (production: `--cpus`,
  `--memory`, `--pids-limit`, `--cap-drop ALL`, `no-new-privileges`,
  read-only rootfs, non-root UID, private network, loopback-only port
  publish) and `ProcessDriver` (dev: `RLIMIT_AS`/`RLIMIT_CPU`/`RLIMIT_FSIZE`,
  own session, per-instance data dir, own venv).
- **Heartbeat** posts node CPU/RAM/disk/instance-count/capacity to the
  Console (`/api/internal/heartbeat`); the Console marks stale workers
  offline and the admin panel can disable them.
- **Auth** — single shared `LUNEL_WORKER_TOKEN`; the worker never sees the
  Docker socket mounted into instances, and instances never see the worker
  token.

## Frontend

Vanilla ES modules (no framework, no build step, no CDN): hash router,
design-system CSS (dark-first, moon-silver accent), API client with CSRF
header, live log terminal with search/pause/download, polling dashboards
that accelerate only while states are transitioning. Works on mobile via a
dedicated top bar + bottom navigation.

## Versioning & updates

`/version` on Core, Worker and Console returns `{name, version, build,
commit}` (env: `LUNEL_VERSION`, `LUNEL_BUILD`, `LUNEL_COMMIT`). The Console
can compare against its own and trigger **redeploys** — Core images are
versioned tags, so update = redeploy on a new tag, rollback = redeploy on
the previous one. Instance configuration (DB rows) is untouched by both.
