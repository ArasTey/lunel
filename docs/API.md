# Lunel API

All Console routes are session-authenticated (GitHub OAuth cookie). Mutating
requests require the `X-Lunel-CSRF` header set to the session cookie value
(the frontend does this automatically). Worker routes use the shared
`LUNEL_WORKER_TOKEN` bearer. Core management routes use the per-instance
`LUNEL_CORE_API_TOKEN` bearer.

## Console API

### Auth

| Method | Path | Description |
|---|---|---|
| GET | `/auth/login` | Start GitHub OAuth (rate-limited per IP) |
| GET | `/auth/callback?code&state` | OAuth callback, sets session cookie |
| POST | `/auth/logout` | Destroy session |
| GET | `/auth/me` | `{authenticated, user, csrf_token}` |

### Instances

| Method | Path | Description |
|---|---|---|
| GET | `/api/instances` | List own instances (name, status, endpoint, region, counts) |
| POST | `/api/instances` | Create. Body: `{name, region, config:{protocol, cpu_limit, memory_mb, core_version}}` |
| GET | `/api/instances/:id` | Detail: config, domains, latest deployment |
| DELETE | `/api/instances/:id` | Tombstone + best-effort provider cleanup |
| POST | `/api/instances/:id/deploy` | Queue deployment → `{deployment_id}` |
| POST | `/api/instances/:id/restart` | Restart running instance |
| POST | `/api/instances/:id/stop` | Stop instance |
| POST | `/api/instances/:id/redeploy` | New deployment version of same instance |
| GET | `/api/instances/:id/status` | Live status + Core health probe (via worker) |
| GET | `/api/instances/:id/logs?tail=` | Recent Core log lines |
| GET | `/api/instances/:id/metrics` | CPU / memory / connections / traffic |
| GET | `/api/instances/:id/deployments` | Deployment history (id, version, status, duration) |
| GET | `/api/instances/:id/deployments/:depId/logs` | Pipeline logs for one deployment |
| GET | `/api/instances/:id/activity` | Instance activity feed |
| GET | `/api/activity` | User-wide activity feed |

Protocols: `vless-ws`, `trojan-ws`, `shadowsocks`, `xhttp-packet-up`,
`xhttp-stream-up`.

### Domains & endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/instances/:id/domains` | Endpoints: `kind=path` (console URL + token) and `kind=http` (provider hostname) |
| POST | `/api/instances/:id/domains` | **Regenerate**: rotates the path token and (when supported) the provider hostname |
| DELETE | `/api/instances/:id/domains/:domainId` | Deactivate a provider/custom domain (path endpoints can only be rotated) |

### Admin (`is_admin` required; audited)

| Method | Path | Description |
|---|---|---|
| GET | `/api/admin/overview` | Users / instances / running / failed / deployments-24h / workers-online |
| GET | `/api/admin/users` · PATCH `/api/admin/users/:id` | List; `{is_admin, is_disabled}` (disabling kills sessions) |
| GET | `/api/admin/instances` | All instances with owner |
| POST | `/api/admin/instances/:id/actions/{restart\|stop\|redeploy}` | Act on any instance |
| DELETE | `/api/admin/instances/:id` | Delete any instance |
| GET | `/api/admin/workers` · PATCH `/api/admin/workers/:id` | List node health; `{enabled}` |
| GET | `/api/admin/deployments` | Recent deployments platform-wide |
| GET | `/api/admin/system` | DB/provider/OAuth configuration status |

### Internal (worker token)

| Method | Path | Description |
|---|---|---|
| POST | `/api/internal/heartbeat` | Node metrics upsert; marks stale workers offline |

### Public instance gateway

| Method | Path | Description |
|---|---|---|
| ANY | `/i/{endpoint-token}/{core-path}` | HTTP proxy into the instance's Core |
| WS | `/i/{endpoint-token}/{core-path}` | Frame-level WebSocket relay into Core |

## Worker API (worker token)

| Method | Path | Description |
|---|---|---|
| POST | `/worker/api/instances/:id/launch` | Launch Core (limits + isolation flags) → `{port, driver}` |
| POST | `/worker/api/instances/:id/stop` · `/restart` · `/remove` | Lifecycle |
| GET | `/worker/api/instances/:id/status` | Driver status + Core health probe |
| GET | `/worker/api/instances/:id/logs?tail=` | Core logs |
| ANY | `/worker/api/instances/:id/proxy/{path}` | HTTP proxy into Core (token-translated) |
| WS | `/worker/api/instances/:id/ws-proxy/{path}?token=` | WebSocket relay into Core |
| GET | `/worker/api/metrics` | Node metrics |
| GET | `/health`, `/ready` | Unauthenticated health |

## Lunel Core API

Public: `GET /health`, `GET /ready`, `GET /version` — and the protocol
transports `/ws/{uuid}`, `/trojan-ws`, `/ss-ws`, `/xhttp-siz10/*`,
`/txhttp-siz10/*` (RVG-compatible paths).

Management (bearer `LUNEL_CORE_API_TOKEN`):

| Method | Path | Description |
|---|---|---|
| GET | `/core/api/stats` | Traffic counters, hourly buckets, link totals, recent errors |
| GET | `/core/api/connections` | Live connections grouped by IP |
| GET | `/core/api/logs?limit=` | Redacted runtime log ring |
| GET | `/core/api/metrics` | Process CPU/memory |
| GET/POST | `/core/api/links` | List (no secrets) / create link |
| PATCH/DELETE | `/core/api/links/{uuid}` | Update (label/quota/active/reset) / delete |
| POST | `/core/api/state/flush` | Force persistence |
