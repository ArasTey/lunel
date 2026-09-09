"""End-to-end pipeline test (requires local PostgreSQL + the stack booted via
deploy/scripts/dev.sh). Boots a Core instance through the real Console
pipeline and relays a VLESS request through the public gateway path."""
import asyncio
import json
import os
import sys
import threading
import http.server
import urllib.request
from pathlib import Path

DSN = os.environ.get("LUNEL_TEST_DSN", "postgresql://admin:lunel@127.0.0.1:5432/lunel")
CONSOLE = os.environ.get("LUNEL_TEST_CONSOLE", "http://127.0.0.1:8080")
WORKER = os.environ.get("LUNEL_TEST_WORKER", "http://127.0.0.1:9100")
WORKER_TOKEN = os.environ.get("LUNEL_WORKER_TOKEN", "dev-worker-token-0123456789abcdef")


async def test_deploy_and_relay():
    import asyncpg
    import secrets
    import websockets

    pool = await asyncpg.create_pool(DSN)
    user_id = str((await pool.fetchrow(
        "INSERT INTO users (github_id, login, name) VALUES ($1,$2,$3) RETURNING id",
        secrets.randbelow(10**9), f"e2e{secrets.randbelow(10**6)}", "E2E"))["id"])
    inst_id = str((await pool.fetchrow(
        """INSERT INTO instances (user_id, name, slug, region, status, provider, core_api_token)
           VALUES ($1,'E2E','e2e','local','stopped','local',$2) RETURNING id""",
        user_id, secrets.token_urlsafe(24)))["id"])
    await pool.execute("INSERT INTO instance_configs (instance_id, protocol) VALUES ($1,'vless-ws')", inst_id)
    token = secrets.token_urlsafe(18)
    await pool.execute("INSERT INTO domains (instance_id, domain, kind) VALUES ($1,$2,'path')", inst_id, token)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "console/api"))
    os.environ.setdefault("LUNEL_DATABASE_URL", DSN)
    os.environ.setdefault("LUNEL_WORKER_TOKEN", WORKER_TOKEN)
    os.environ.setdefault("LUNEL_LOCAL_WORKER_URL", WORKER)
    from lunel_console.services import deployments

    dep_id = await deployments.deploy_instance(pool, inst_id)
    for _ in range(60):
        await asyncio.sleep(1)
        row = await pool.fetchrow("SELECT status, error FROM deployments WHERE id=$1", dep_id)
        if row["status"] in ("running", "failed"):
            break
    assert row["status"] == "running", f"deployment did not reach running: {row['error']}"

    # relay a VLESS request through the public gateway
    core_token = await pool.fetchval("SELECT core_api_token FROM instances WHERE id=$1", inst_id)
    req = urllib.request.Request(
        f"{CONSOLE}/i/{token}/core/api/links",
        data=json.dumps({"label": "t", "protocol": "vless-ws"}).encode(),
        headers={"Authorization": f"Bearer {core_token}", "Content-Type": "application/json"},
        method="POST")
    uuid = json.loads(urllib.request.urlopen(req).read())["uuid"]

    srv = http.server.HTTPServer(("127.0.0.1", 18599), _Hello)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    raw = bytes.fromhex(uuid.replace("-", ""))
    hdr = (b"\x00" + raw + b"\x00\x01" + (18599).to_bytes(2, "big") + b"\x01"
           + bytes([127, 0, 0, 1]) + b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    async with websockets.connect(f"{CONSOLE.replace('http', 'ws', 1)}/i/{token}/ws/{uuid}") as ws:
        await ws.send(hdr)
        resp = bytearray(await asyncio.wait_for(ws.recv(), timeout=10))
        # headers may arrive in the first frame; body may follow in the next
        if b"HELLO-LUNEL" not in resp:
            resp += bytearray(await asyncio.wait_for(ws.recv(), timeout=10))
    assert resp[:2] == b"\x00\x00", f"missing prefix: {bytes(resp[:20])!r}"
    assert b"HELLO-LUNEL" in resp[2:], f"payload missing: {bytes(resp[2:120])!r}"
    srv.shutdown()
    await pool.close()
    print("E2E OK: deploy -> running -> gateway relay verified")


class _Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"HELLO-LUNEL")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    asyncio.run(test_deploy_and_relay())
