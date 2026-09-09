"""Instance drivers for the Lunel Worker.

Two drivers share one interface:

* ``DockerDriver``   — production. Runs Lunel Core in an isolated container:
  CPU/memory/PID limits, read-only root filesystem, dropped capabilities,
  no-new-privileges, non-root user, private bridge network. The Docker socket
  is used only by the Worker itself; it is never mounted into Core containers.
* ``ProcessDriver``  — development/testing (no Docker available). Runs Core as
  a subprocess with OS resource limits (RLIMIT_AS / RLIMIT_CPU / RLIMIT_FSIZE)
  in its own session and a per-instance data directory. Isolation is weaker
  than container isolation and must not be used in production.

Selection: ``LUNEL_WORKER_DRIVER=docker|process`` (default: docker if the
docker CLI is available, else process).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from .logging import get

log = get("runtime", "lunel.worker.driver")


class DriverError(RuntimeError):
    pass


@dataclass
class LaunchSpec:
    instance_id: str
    deployment_id: str
    core_version: str
    api_token: str
    public_host: str = ""
    cpu_limit: float = 0.5          # docker --cpus
    memory_mb: int = 256            # docker --memory / RLIMIT_AS
    max_processes: int = 128        # docker --pids-limit
    port: int = 0                   # 0 = auto-allocate


@dataclass
class InstanceHandle:
    instance_id: str
    driver: str
    reference: str                  # container name / pid
    port: int
    started_at: float = field(default_factory=time.time)
    meta: dict = field(default_factory=dict)


def port_is_free(port: int) -> bool:
    # No SO_REUSEADDR here: it makes the probe succeed on macOS even while
    # another process is actively listening, causing launch-time bind failures.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class PortAllocator:
    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end
        self.used: set[int] = set()

    def allocate(self, preferred: int | None = None) -> int:
        candidates = []
        if preferred and self.start <= preferred <= self.end:
            candidates.append(preferred)
        candidates.extend(range(self.start, self.end + 1))
        for port in candidates:
            if port in self.used:
                continue
            if port_is_free(port):
                self.used.add(port)
                return port
        raise DriverError("no free ports in allocation range")

    def release(self, port: int | None) -> None:
        if port:
            self.used.discard(port)


class BaseDriver:
    name = "base"

    def __init__(self, ports: PortAllocator, data_root: Path):
        self.ports = ports
        self.data_root = data_root
        self.handles: dict[str, InstanceHandle] = {}

    async def launch(self, spec: LaunchSpec) -> InstanceHandle:  # pragma: no cover
        raise NotImplementedError

    async def stop(self, instance_id: str, timeout: int = 10) -> None:  # pragma: no cover
        raise NotImplementedError

    async def remove(self, instance_id: str, timeout: int = 10) -> None:  # pragma: no cover
        raise NotImplementedError

    async def status(self, instance_id: str) -> dict:  # pragma: no cover
        raise NotImplementedError

    async def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        return []

    def is_known(self, instance_id: str) -> bool:
        return instance_id in self.handles

    def known_instances(self) -> list[InstanceHandle]:
        return list(self.handles.values())


# ---------------------------------------------------------------------------
# Docker driver
# ---------------------------------------------------------------------------
class DockerDriver(BaseDriver):
    name = "docker"

    def __init__(self, ports: PortAllocator, data_root: Path, network: str = "lunel"):
        super().__init__(ports, data_root)
        self.network = network
        if shutil.which("docker") is None:
            raise DriverError("docker CLI not found")
        self._ensure_network()

    def _ensure_network(self) -> None:
        result = subprocess.run(
            ["docker", "network", "inspect", self.network],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            subprocess.run(
                ["docker", "network", "create", "--driver", "bridge", self.network],
                capture_output=True, text=True, timeout=30, check=True,
            )
            log.info("created docker network %s", self.network)

    async def _run(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise DriverError(f"docker {' '.join(args[:2])} timed out")
        if proc.returncode != 0:
            raise DriverError(stderr.decode(errors="replace").strip()[:500] or "docker command failed")
        return stdout.decode(errors="replace")

    async def launch(self, spec: LaunchSpec) -> InstanceHandle:
        port = self.ports.allocate(spec.port or None)
        name = f"lunel-inst-{spec.instance_id[:12]}"
        image = f"lunel/core:{spec.core_version}"
        data_dir = self.data_root / spec.instance_id
        data_dir.mkdir(parents=True, exist_ok=True)
        try:
            await self._run(
                "run", "-d",
                "--name", name,
                "--label", "lunel.instance=" + spec.instance_id,
                "--label", "lunel.managed-by=lunel-worker",
                # resource limits
                "--cpus", str(spec.cpu_limit),
                "--memory", f"{spec.memory_mb}m",
                "--pids-limit", str(spec.max_processes),
                # isolation hardening
                "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--read-only",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                "--user", "65532:65532",
                "--network", self.network,
                # expose core port only on loopback of the worker host
                "-p", f"127.0.0.1:{port}:8000",
                "-e", f"LUNEL_CORE_API_TOKEN={spec.api_token}",
                "-e", f"LUNEL_PUBLIC_HOST={spec.public_host}",
                "-e", "LUNEL_STATE_PATH=/data/state.json",
                "-v", f"{data_dir}:/data",
                image,
            )
        except DriverError:
            self.ports.release(port)
            raise
        handle = InstanceHandle(
            instance_id=spec.instance_id, driver="docker", reference=name, port=port,
            meta={"deployment_id": spec.deployment_id, "image": image, "api_token": spec.api_token},
        )
        self.handles[spec.instance_id] = handle
        log.info("docker launch %s -> %s (port %d)", spec.instance_id[:12], name, port)
        return handle

    async def stop(self, instance_id: str, timeout: int = 10) -> None:
        handle = self.handles.get(instance_id)
        if not handle:
            raise DriverError("instance not known to worker")
        await self._run("stop", "-t", str(timeout), handle.reference, timeout=timeout + 20)

    async def remove(self, instance_id: str, timeout: int = 10) -> None:
        handle = self.handles.pop(instance_id, None)
        if not handle:
            return
        try:
            await self._run("rm", "-f", handle.reference, timeout=30)
        finally:
            self.ports.release(handle.port)

    async def status(self, instance_id: str) -> dict:
        handle = self.handles.get(instance_id)
        if not handle:
            return {"running": False, "known": False}
        out = await self._run("inspect", "--format", "{{json .State}}", handle.reference)
        state = json.loads(out.strip())
        return {
            "running": bool(state.get("Running")),
            "known": True,
            "exit_code": state.get("ExitCode"),
            "started_at": state.get("StartedAt"),
            "port": handle.port,
            "reference": handle.reference,
        }

    async def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        handle = self.handles.get(instance_id)
        if not handle:
            return []
        out = await self._run("logs", "--tail", str(tail), handle.reference, timeout=20)
        return out.splitlines()


# ---------------------------------------------------------------------------
# Process driver (development only)
# ---------------------------------------------------------------------------
class ProcessDriver(BaseDriver):
    name = "process"

    def __init__(self, ports: PortAllocator, data_root: Path, core_cmd: list[str] | None = None):
        super().__init__(ports, data_root)
        # Default: run Core with its own venv so its dependencies (cryptography
        # etc.) resolve independently of the worker's environment.
        self.core_cmd = core_cmd or [
            os.environ.get("LUNEL_CORE_PYTHON", ".venv/bin/python"),
            "-m", "lunel_core",
        ]
        self.core_cwd = os.environ.get("LUNEL_CORE_CWD", "")

    def _limits(self, spec: LaunchSpec):
        import resource

        def apply() -> None:  # runs in the child before exec
            mem_bytes = spec.memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
                resource.setrlimit(resource.RLIMIT_CPU, (60 * 60, 60 * 60 + 60))
                resource.setrlimit(resource.RLIMIT_FSIZE, (512 * 1024 * 1024, 512 * 1024 * 1024))
                try:
                    resource.setrlimit(resource.RLIMIT_NPROC, (spec.max_processes, spec.max_processes))
                except (ValueError, OSError):
                    pass  # macOS maps NPROC globally; skip rather than fail
            except (ValueError, OSError):
                pass
            os.umask(0o077)

        return apply

    async def launch(self, spec: LaunchSpec) -> InstanceHandle:
        port = self.ports.allocate(spec.port or None)
        data_dir = self.data_root / spec.instance_id
        data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({
            "LUNEL_CORE_API_TOKEN": spec.api_token,
            "LUNEL_STATE_PATH": str(data_dir / "state.json"),
            "LUNEL_PUBLIC_HOST": spec.public_host,
            "LUNEL_LOG_JSON": "1",
            "PORT": str(port),
        })
        env.pop("PYTHONPATH", None)  # never leak worker deps into Core

        out_path = data_dir / "core.log"
        out_fh = out_path.open("ab", buffering=0)
        proc = await asyncio.create_subprocess_exec(
            *self.core_cmd, "--port", str(port),
            stdout=out_fh, stderr=subprocess.STDOUT,
            env=env, preexec_fn=self._limits(spec), start_new_session=True,
            cwd=self.core_cwd or None,
        )
        handle = InstanceHandle(
            instance_id=spec.instance_id, driver="process", reference=str(proc.pid), port=port,
            meta={"deployment_id": spec.deployment_id, "pid": proc.pid, "log_file": str(out_path),
                  "api_token": spec.api_token},
        )
        self.handles[spec.instance_id] = handle
        log.info("process launch %s -> pid %d (port %d)", spec.instance_id[:12], proc.pid, port)
        return handle

    async def stop(self, instance_id: str, timeout: int = 10) -> None:
        handle = self.handles.get(instance_id)
        if not handle:
            raise DriverError("instance not known to worker")
        pid = int(handle.reference)
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        for _ in range(timeout * 10):
            await asyncio.sleep(0.1)
            if not self._pid_alive(pid):
                return
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    async def remove(self, instance_id: str, timeout: int = 10) -> None:
        handle = self.handles.pop(instance_id, None)
        if not handle:
            return
        try:
            await self.stop(instance_id, timeout)
        except DriverError:
            pass
        finally:
            self.ports.release(handle.port)
            data_dir = self.data_root / instance_id
            shutil.rmtree(data_dir, ignore_errors=True)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    async def status(self, instance_id: str) -> dict:
        handle = self.handles.get(instance_id)
        if not handle:
            return {"running": False, "known": False}
        alive = self._pid_alive(int(handle.reference))
        return {
            "running": alive, "known": True,
            "pid": int(handle.reference),
            "port": handle.port, "reference": handle.reference,
            "started_at": handle.started_at,
        }

    async def logs(self, instance_id: str, tail: int = 200) -> list[str]:
        handle = self.handles.get(instance_id)
        if not handle:
            return []
        path = Path(handle.meta.get("log_file", ""))
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-tail:]


def select_driver(data_root: Path) -> tuple[BaseDriver, str]:
    ports = PortAllocator(
        int(os.environ.get("LUNEL_WORKER_PORT_START", "19000")),
        int(os.environ.get("LUNEL_WORKER_PORT_END", "19999")),
    )
    chosen = os.environ.get("LUNEL_WORKER_DRIVER", "").strip().lower()
    if not chosen:
        chosen = "docker" if shutil.which("docker") else "process"
    if chosen == "docker":
        try:
            return DockerDriver(ports, data_root), "docker"
        except DriverError as exc:
            log.warning("docker driver unavailable (%s); falling back to process driver", exc)
            chosen = "process"
    if chosen == "process":
        return ProcessDriver(ports, data_root), "process"
    raise DriverError(f"unknown driver: {chosen}")
