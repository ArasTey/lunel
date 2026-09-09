# Lunel

**Lunel** is a multi-protocol proxy platform: deploy, monitor, and manage isolated
proxy instances (Lunel Core) from a single web console (Lunel Console) — without
ever touching a server.

Derived from the RVG Gateway relay engine, refactored into a clean product
architecture: real deployments, real health checks, real isolation, real
endpoints. No simulated states, no fake buttons.

```
GitHub  →  Lunel Console  →  Create Instance  →  Deploy  →  Running  →  Endpoint  →  Manage
```

---

## What Lunel is

| Part | What it is |
|---|---|
| **Lunel Core** (`core/`) | The proxy runtime. Relays **VLESS** (WebSocket + xHTTP), **Trojan** (WebSocket + xHTTP) and **Shadowsocks AEAD** over WebSocket, with per-link quotas, expiry and enable/disable — all wire-compatible with RVG links. |
| **Lunel Console** (`console/`) | The web control plane. GitHub OAuth login, dashboard, 6-step deploy wizard, live logs/connections/metrics, networking & domain management, admin panel — backed by PostgreSQL. |
| **Lunel Worker** (`worker/`) | The node agent. Runs Core instances in isolated containers (Docker driver) or rlimit-separated processes (dev driver), reports heartbeats/metrics, and reverse-proxies traffic into instances. |


### Security fixes carried into the refactor

- **xHTTP session hijack** — sessions are now keyed by `(link, session_id)`; one
  link can never attach to another link's session stream.
- **Memory-DoS limits** — packet-up `seq_buf` caps, request-body caps, global and
  per-link session caps.
- **Unified quota engine** — one adaptive batched QuotaGate for all transports.
- **Secret redaction** — logs never carry credentials, tokens or UUID keys.

---

## Quick start (fork and go)

Deploy the repository root as **one service** on any platform that gives you
a PostgreSQL database and a public domain (Lucity, Railway, Render, …):

1. Fork this repo → add a service from the fork **root** with start command
   `python main.py` → generate a domain → deploy.
2. Open your domain → sign in with the built-in account **admin / admin** →
   **Create Instance** → Deploy.

Zero required variables: embedded SQLite storage and the default admin
account (`admin` / `admin` — change it in **Admin → System** on first login)
are automatic. Optional: attach a PostgreSQL database (auto-detected via
`DATABASE_URL`) and GitHub OAuth (`LUNEL_GITHUB_CLIENT_ID` / `..._SECRET`).
Full details in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

### Local development

Requires Python 3.11+ and a local PostgreSQL.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
createdb lunel
LUNEL_DATABASE_URL=postgresql://admin:lunel@127.0.0.1:5432/lunel \
LUNEL_GITHUB_CLIENT_ID=... LUNEL_GITHUB_CLIENT_SECRET=... \
.venv/bin/python main.py
```

Open **http://127.0.0.1:8080**. (Component-style development with separate
venvs: `deploy/scripts/dev.sh`.)

---

## Repository layout

```
lunel/
├── core/                      # Lunel Core (proxy runtime)
│   ├── lunel_core/
│   │   ├── app.py             #   FastAPI assembly: health, transports, management API
│   │   ├── config.py          #   env + TOML + CLI configuration
│   │   ├── state.py           #   links, connections, stats, atomic persistence
│   │   ├── links.py           #   share-link generation (RVG-compatible URLs)
│   │   ├── relay/             #   vless / trojan / shadowsocks / xhttp engine
│   │   └── __main__.py        #   python -m lunel_core
│   ├── Dockerfile
│   └── requirements.txt
├── console/
│   ├── api/lunel_console/     # Console API (FastAPI + PostgreSQL)
│   │   ├── auth/              #   GitHub OAuth + sessions + CSRF
│   │   ├── routers/           #   instances, domains, admin, internal
│   │   ├── services/          #   deployments, gateway, workers, domains, railway
│   │   └── db.py              #   migrations + pool
│   └── frontend/              # dark-first SPA (vanilla ES modules, no build step)
├── worker/lunel_worker/       # node agent (drivers, heartbeat, ws-proxy)
├── deploy/
│   ├── docker/                # Dockerfiles + docker-compose stack
│   ├── proxy/                 # optional Caddy edge (wildcard TLS)
│   └── scripts/dev.sh         # dev launcher
├── docs/                      # ARCHITECTURE, API, SECURITY, DEPLOYMENT, …
└── tests/                     # protocol + pipeline tests
```

## Documentation

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, request flow, data model
- [API.md](docs/API.md) — Console + Core + Worker HTTP API reference
- [SECURITY.md](docs/SECURITY.md) — security model, threat decisions, reporting
- [DEPLOYMENT.md](docs/DEPLOYMENT.md) — **Lucity walkthrough**, Docker self-hosting
- [INSTALLATION.md](docs/INSTALLATION.md) — prerequisites and setup
- [DEVELOPMENT.md](docs/DEVELOPMENT.md) — running tests, code layout conventions

## License

Lunel derives from RVG Gateway by codebox (arvin341az-glitch) with the original
relay engine preserved under compatible terms; the Lunel console/worker/deploy
code is MIT. See [LICENSE](LICENSE).
