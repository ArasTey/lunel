"""Lunel — unified service entrypoint (fork-and-go deployment).

One deployable unit contains the whole platform:

    python main.py

  * Lunel Console API + frontend on ``$PORT`` (default 8080) — the public
    endpoint on the platform's domain (WebSocket capable)
  * an embedded Lunel Worker on an internal loopback port
  * Lunel Core instances as isolated child processes (process driver, OS
    resource limits) on the same node

Zero-config defaults:
  * ``DATABASE_URL`` (auto-injected by Lucity/Railway-style platforms) is
    used when ``LUNEL_DATABASE_URL`` is not set
  * ``LUNEL_SECRET_KEY`` auto-generates and persists to a 0600 file on
    first boot
  * the internal worker token auto-generates per boot (console and worker
    share one process tree; set ``LUNEL_WORKER_TOKEN`` explicitly when
    running split deployments)

Explicitly required for login: ``LUNEL_GITHUB_CLIENT_ID`` and
``LUNEL_GITHUB_CLIENT_SECRET`` (plus ``LUNEL_PUBLIC_URL`` matching the
platform domain so the OAuth callback resolves).
"""
from __future__ import annotations

import atexit
import os
import secrets
import signal
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _writable_dir(candidates: list[Path | None]) -> Path | None:
    for cand in candidates:
        if cand is None:
            continue
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".write-probe"
            probe.write_text("ok")
            probe.unlink()
            return cand
        except OSError:
            continue
    return None


def ensure_secret_key() -> str:
    """Return a usable session secret, persisting a generated one if needed."""
    val = os.environ.get("LUNEL_SECRET_KEY", "").strip()
    if len(val) >= 32:
        return val
    base = _writable_dir([Path("/data"), ROOT / ".lunel-data"])
    if base is not None:
        key_file = base / ".lunel_secret_key"
        try:
            if key_file.exists():
                val = key_file.read_text(encoding="utf-8").strip()
            if len(val) < 32:
                val = secrets.token_urlsafe(48)
                key_file.write_text(val, encoding="utf-8")
            os.chmod(key_file, 0o600)
            print(f"[lunel] LUNEL_SECRET_KEY not set — persisted generated key to {key_file}",
                  file=sys.stderr)
            return val
        except OSError:
            pass
    print("[lunel] WARNING: LUNEL_SECRET_KEY not set and not persistable — "
          "using an ephemeral key (sessions reset on restart)", file=sys.stderr)
    return secrets.token_urlsafe(48)


def pick_free_port(start: int, end: int | None = None) -> int:
    end = end if end is not None else start + 99
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no free internal port in range {start}-{end}")


def main() -> int:
    # ---- configuration defaults (zero-config friendly) --------------------
    port = int(os.environ.get("PORT", os.environ.get("LUNEL_CONSOLE_PORT", "8080")))
    if not os.environ.get("LUNEL_DATABASE_URL") and os.environ.get("DATABASE_URL"):
        os.environ["LUNEL_DATABASE_URL"] = os.environ["DATABASE_URL"]
        print("[lunel] using DATABASE_URL from the platform for PostgreSQL", file=sys.stderr)
    os.environ["LUNEL_SECRET_KEY"] = ensure_secret_key()
    os.environ.setdefault("LUNEL_WORKER_TOKEN", secrets.token_urlsafe(32))
    os.environ.setdefault("LUNEL_PUBLIC_URL", f"http://127.0.0.1:{port}")
    os.environ.setdefault("LUNEL_NODE_ID", "local")
    os.environ.setdefault("LUNEL_NODE_REGION", "local")

    data_root = _writable_dir([
        Path(os.environ["LUNEL_WORKER_DATA"]) if os.environ.get("LUNEL_WORKER_DATA") else None,
        Path("/data/instances"),
        ROOT / ".lunel-data" / "instances",
    ]) or Path("/tmp/lunel-instances")
    os.environ["LUNEL_WORKER_DATA"] = str(data_root)

    worker_port = pick_free_port(9100)
    os.environ["LUNEL_LOCAL_WORKER_URL"] = f"http://127.0.0.1:{worker_port}"

    # Core runs with the same interpreter/venv (single root requirements.txt).
    os.environ.setdefault("LUNEL_CORE_PYTHON", sys.executable)
    os.environ.setdefault("LUNEL_CORE_CWD", str(ROOT / "core"))

    # ---- embedded worker ---------------------------------------------------
    worker_env = os.environ.copy()
    worker_env["LUNEL_WORKER_HOST"] = "127.0.0.1"
    worker_env["LUNEL_WORKER_PORT"] = str(worker_port)
    worker_env["LUNEL_CONSOLE_URL"] = f"http://127.0.0.1:{port}"  # heartbeat target
    worker = subprocess.Popen(
        [sys.executable, "-m", "lunel_worker"],
        cwd=str(ROOT / "worker"),
        env=worker_env,
    )
    atexit.register(_stop_worker, worker)

    def _terminate(_num, _frame):
        _stop_worker(worker)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _terminate)

    # ---- console (foreground process) --------------------------------------
    sys.path.insert(0, str(ROOT / "console" / "api"))
    import uvicorn

    print(f"[lunel] console : {os.environ['LUNEL_PUBLIC_URL']} (listening on 0.0.0.0:{port})")
    print(f"[lunel] worker  : internal on 127.0.0.1:{worker_port} "
          f"(process driver, data={data_root})")
    print("[lunel] GitHub OAuth login requires LUNEL_GITHUB_CLIENT_ID / "
          "LUNEL_GITHUB_CLIENT_SECRET and LUNEL_PUBLIC_URL set to the public domain")
    try:
        uvicorn.run(
            "lunel_console.main:app",
            host="0.0.0.0",
            port=port,
            log_level=os.environ.get("LUNEL_LOG_LEVEL", "info"),
        )
    finally:
        _stop_worker(worker)
    return 0


def _stop_worker(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
