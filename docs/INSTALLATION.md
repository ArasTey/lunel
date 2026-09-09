# Installation

## Requirements

| Component | Version |
|---|---|
| Python | 3.11+ (3.12 recommended; 3.14 tested) |
| PostgreSQL | 14+ |
| Node.js | not required (frontend is dependency-free) |
| Docker | optional — enables the container isolation driver |

## Steps

1. **Clone and create databases**

   ```bash
   git clone <your-fork> lunel && cd lunel
   createdb lunel
   ```

2. **Install dependencies** (three venvs: core, worker, console)

   ```bash
   (cd core         && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
   (cd worker       && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
   (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
   ```

   Migrations run automatically on first Console start.

3. **GitHub OAuth App** — callback `http://127.0.0.1:8080/auth/callback`
   (or your public URL). Set `LUNEL_GITHUB_CLIENT_ID` / `..._SECRET`.

4. **Run** — see `deploy/scripts/dev.sh`, or set the environment variables
   from `docs/DEPLOYMENT.md` and start each service:

   ```bash
   # console
   (cd console/api && .venv/bin/python -m lunel_console)         # :8080
   # worker
   LUNEL_CORE_PYTHON=$PWD/core/.venv/bin/python \
   LUNEL_CORE_CWD=$PWD/core \
   (cd worker && .venv/bin/python -m lunel_worker)               # :9100
   ```

5. **Verify** — `curl http://127.0.0.1:8080/health` and
   `curl http://127.0.0.1:9100/health` both return `{"status":"ok", ...}`.
