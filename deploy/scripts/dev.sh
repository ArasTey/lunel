#!/usr/bin/env bash
# Lunel — local development launcher.
# Starts PostgreSQL check, Console API, Worker, and (optionally) a Core instance.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# Defaults (override via env)
export LUNEL_DATABASE_URL="${LUNEL_DATABASE_URL:-postgresql://admin:lunel@127.0.0.1:5432/lunel}"
export LUNEL_SECRET_KEY="${LUNEL_SECRET_KEY:-dev-secret-key-0123456789abcdef0123456789abcdef}"
export LUNEL_WORKER_TOKEN="${LUNEL_WORKER_TOKEN:-dev-worker-token-0123456789abcdef}"
export LUNEL_GITHUB_CLIENT_ID="${LUNEL_GITHUB_CLIENT_ID:-}"
export LUNEL_GITHUB_CLIENT_SECRET="${LUNEL_GITHUB_CLIENT_SECRET:-}"
export LUNEL_PUBLIC_URL="${LUNEL_PUBLIC_URL:-http://127.0.0.1:8080}"
export LUNEL_LOCAL_WORKER_URL="${LUNEL_LOCAL_WORKER_URL:-http://127.0.0.1:9100}"
export LUNEL_WORKER_DATA="${LUNEL_WORKER_DATA:-/tmp/lunel-dev/instances}"
export PYTHONUNBUFFERED=1

say() { printf '\033[1;36mlunel-dev\033[0m %s\n' "$1"; }

cleanup() {
  say "shutting down…"
  [ -n "${CONSOLE_PID:-}" ] && kill "$CONSOLE_PID" 2>/dev/null || true
  [ -n "${WORKER_PID:-}" ] && kill "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT

say "starting Console API on :8080"
(cd "$ROOT/console/api" && . .venv/bin/activate && python -m lunel_console) &
CONSOLE_PID=$!

say "starting Worker on :9100 (process driver, dev isolation)"
(cd "$ROOT/worker" && . .venv/bin/activate && \
  PYTHONPATH="$ROOT/core" LUNEL_WORKER_DRIVER="${LUNEL_WORKER_DRIVER:-process}" \
  python -m lunel_worker) &
WORKER_PID=$!

say "console  → http://127.0.0.1:8080"
say "worker   → http://127.0.0.1:9100"
say "press Ctrl+C to stop"
wait
