"""Instance endpoint gateway.

On platforms that expose a single public HTTP endpoint per deployment
(e.g. Lucity's workload domain with Gateway API HTTPRoutes), every Lunel
instance is reached through the Console's public URL under a private
endpoint token:

    https://<console-host>/i/<endpoint-token>/<core-path>

The gateway authenticates by endpoint token (un guessable, rotatable),
strips the prefix, and proxies HTTP and WebSocket traffic to the instance's
Lunel Core through the Worker. On self-hosted deployments with wildcard DNS
the same instances can additionally be exposed as real hostnames via the
bundled Caddy — both modes share this proxy path.

WebSocket proxying is implemented frame-by-frame (client ⇄ gateway ⇄ core)
because the standard HTTP client stack cannot pass an Upgrade through.
"""
from __future__ import annotations

import asyncio
import httpx
import websockets
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse

from ..db import get_pool
from ..logging import get

log = get("network", "lunel.console.gateway")

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
    "authorization",  # replaced with the worker token below
}


FRIENDLY_404 = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lunel</title>
<style>body{{background:#0a0c10;color:#e7ebf3;font-family:-apple-system,Segoe UI,Roboto,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}}
.c{{max-width:420px;text-align:center;padding:28px;border:1px solid #1e2430;border-radius:12px;background:#12151c}}
h2{{margin:0 0 8px}}p{{color:#9aa4b8;font-size:13.5px;line-height:1.55}}
code{{background:#10131a;border:1px solid #2a3242;border-radius:6px;padding:1px 6px;font-size:12px}}</style></head>
<body><div class="c"><h2>{title}</h2><p>{body}</p></div></body></html>"""


def _page(title: str, body: str, status: int = 200) -> "HTMLResponse":
    from fastapi.responses import HTMLResponse

    return HTMLResponse(FRIENDLY_404.format(title=title, body=body), status_code=status)


import json as _json_mod  # noqa: E402

def _sub_html_page(title: str, configs: list, host: str, sub_path: str,
                   qr_path: str = "") -> str:
    """Premium subscription page: QRs inline, copy buttons, app links,
    EN/FA toggle. Browsers get it; clients are UA-sniffed to raw data."""
    import html as _html
    import io

    import qrcode
    import qrcode.image.svg

    esc = _html.escape

    def qr_svg(text: str) -> str:
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage,
                          box_size=11, border=1)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode()

    PROTO_META = {
        "vless-ws": ("VLESS", "WebSocket", "#6f9bff"),
        "trojan-ws": ("Trojan", "WebSocket", "#ef6b73"),
        "shadowsocks": ("Shadowsocks", "AEAD", "#4ecb95"),
        "xhttp-packet-up": ("xHTTP", "packet-up", "#e3b341"),
        "xhttp-stream-up": ("xHTTP", "stream-up", "#e3b341"),
    }
    cards = ""
    for i, c in enumerate(configs):
        pname, transport, color = PROTO_META.get(c["protocol"], (c["protocol"], "", "#6f9bff"))
        qr = qr_svg(c["share_url"])
        cards += f'''
        <div class="cfg" style="--pc:{color}">
          <div class="ch"><div><span class="pn">{esc(pname)}</span><span class="tr">{esc(transport)}</span></div><span class="chip">{esc(c["protocol"])}</span></div>
          <div class="u" id="u{i}">{esc(c["share_url"])}</div>
          <div class="row">
            <button onclick="cp(\'u{i}\')">Copy <span class="en">config</span><span class="fa" hidden>کانفیگ</span></button>
            <button class="g" onclick="tg(\'q{i}\',this)">QR</button>
          </div>
          <div class="qr" id="q{i}">{qr}</div>
        </div>'''

    sub_url = f"https://{host}{sub_path}"
    sub_qr = qr_svg(sub_url)
    sb_url = f"https://{host}{sub_path}?fmt=singbox&host={host}"
    cl_url = f"https://{host}{sub_path}?fmt=clash&host={host}"

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
*{{box-sizing:border-box}}
body{{background:#07090d;color:#e7ebf3;font-family:-apple-system,'Segoe UI',Roboto,'Vazirmatn',sans-serif;margin:0;padding:0 14px 70px;
background-image:radial-gradient(1000px 460px at 50% -140px,rgba(111,155,255,.14),transparent),radial-gradient(700px 300px at 85% 110%,rgba(78,203,149,.07),transparent)}}
.w{{max-width:600px;margin:0 auto}}
.top{{display:flex;align-items:center;gap:10px;padding:26px 4px 4px}}
.moon{{width:34px;height:34px;color:#d8e0ee}}
h1{{font-size:22px;margin:0;letter-spacing:.3px}}
.sub2{{color:#9aa4b8;font-size:12.5px}}
.f{{margin-left:auto;font-size:10px;letter-spacing:1.5px;font-weight:800;color:#4ecb95;border:1px solid rgba(78,203,149,.45);border-radius:999px;padding:3px 11px}}
.hero{{text-align:center;padding:10px 0 22px}}
.hero h2{{margin:0 0 6px;font-size:17px}}
.hero p{{margin:0;color:#9aa4b8;font-size:13px}}
.card{{background:linear-gradient(180deg,#12151c,#10131a);border:1px solid #1e2430;border-radius:14px;padding:16px;margin-bottom:14px;box-shadow:0 12px 32px rgba(0,0,0,.35)}}
.card h3{{margin:0 0 4px;font-size:14px}}
.lbl{{color:#9aa4b8;font-size:12.5px;margin:0 0 10px}}
.subu{{display:flex;gap:8px;align-items:stretch}}
.subu .u{{flex:1;font-family:ui-monospace,monospace;font-size:11px;background:#0a0c10;border:1px solid #1e2430;border-radius:8px;padding:9px 10px;word-break:break-all}}
.btn{{padding:8px 14px;border-radius:8px;border:1px solid #d8e0ee;background:#d8e0ee;color:#0b0d11;font-weight:650;font-size:12.5px;cursor:pointer}}
.btn.g{{background:#171b24;border-color:#2a3242;color:#e7ebf3}}
.fmts{{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}}
.fmt{{font-size:11px;color:#9aa4b8;border:1px solid #2a3242;border-radius:999px;padding:3px 11px;text-decoration:none}}
.fmt:hover{{color:#e7ebf3}}
.cfg{{background:#10131a;border:1px solid #1e2430;border-left:3px solid var(--pc);border-radius:12px;padding:13px 14px;margin-bottom:11px}}
.ch{{display:flex;justify-content:space-between;align-items:center;gap:8px}}
.pn{{font-weight:700;font-size:13.5px}}
.tr{{color:#5d6678;font-size:11px;margin-left:7px}}
.chip{{font-size:10px;color:#9aa4b8;border:1px solid #2a3242;border-radius:999px;padding:1px 8px;font-family:monospace}}
.u{{font-family:ui-monospace,monospace;font-size:10.5px;color:#9aa4b8;background:#0a0c10;border:1px solid #1e2430;border-radius:8px;padding:7px 9px;margin:9px 0;word-break:break-all;max-height:64px;overflow:auto}}
.row{{display:flex;gap:7px}}
.btn{{padding:8px 14px;border-radius:8px;border:1px solid #d8e0ee;background:#d8e0ee;color:#0b0d11;font-weight:650;font-size:12.5px;cursor:pointer}}
.btn.g{{background:#171b24;border-color:#2a3242;color:#e7ebf3}}
.qr{{display:none;margin-top:11px;text-align:center}}
.qr svg{{width:216px;height:216px;background:#fff;border-radius:10px}}
.apps{{display:flex;flex-wrap:wrap;gap:7px;margin-top:9px}}
.apps a{{font-size:11.5px;color:#6f9bff;text-decoration:none;border:1px solid #2a3242;border-radius:999px;padding:4px 12px}}
.apps a:hover{{border-color:#6f9bff}}
.ft{{text-align:center;color:#5d6678;font-size:11.5px;margin-top:26px}}
.ft a{{color:#6f9bff;text-decoration:none}}
.fa{{display:none}}
body.fa .en{{display:none}}
body.fa .fa{{display:inline}}
#lang{{position:fixed;top:14px;right:14px;z-index:9;background:#12151c;border:1px solid #2a3242;color:#9aa4b8;border-radius:999px;padding:4px 12px;font-size:11px;cursor:pointer}}
</style></head><body>
<button id="lang" onclick="document.body.classList.toggle('fa');this.textContent=document.body.classList.contains('fa')?'EN':'فارسی'">فارسی</button>
<div class="w">
<div class="top"><svg class="moon" viewBox="0 0 32 32" fill="none"><path d="M16 2.5a13.5 13.5 0 1 0 13.06 17.02 11 11 0 0 1-14.58-14.58A13.6 13.6 0 0 1 16 2.5Z" fill="currentColor"/><path d="M4 29.5h24" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
<div><h1>Lunel</h1><div class="sub2">{esc(title)}</div></div><span class="f">&#9679; FREE</span></div>
<div class="hero"><h2 class="en">Your configs are ready</h2><h2 class="fa" hidden>کانفیگ‌های شما آماده است</h2>
<p class="en">Import the subscription into your client, or copy each config individually.</p>
<p class="fa" hidden>سابسکریپشن را وارد کلاینت کنید یا هر کانفیگ را جدا کپی کنید.</p></div>
<div class="card">
<h3 class="en">Subscription — all protocols</h3><h3 class="fa" hidden>سابسکریپشن — همه پروتکل‌ها</h3>
<p class="lbl en">One URL, every config, auto-updates. Add it under Subscriptions in your client.</p>
<p class="lbl fa" hidden>یک لینک برای همه کانفیگ‌ها — در بخش Subscriptions اپ وارد کنید.</p>
<div class="subu"><div class="u" id="subu">{esc(sub_url)}</div><button class="btn" onclick="cp('subu')"><span class="en">Copy</span><span class="fa" hidden>کپی</span></button></div>
<div class="fmts">
<a class="fmt" href="{esc(sb_url)}" target="_blank" rel="noopener">sing-box JSON</a>
<a class="fmt" href="{esc(cl_url)}" target="_blank" rel="noopener">Clash Meta YAML</a>
<a class="fmt" href="{esc(sub_url)}" target="_blank" rel="noopener">v2ray base64</a>
</div>
<div class="qr" style="display:block;margin-top:12px">{sub_qr}</div>
<div style="color:#5d6678;font-size:10.5px;text-align:center">Scan the subscription with your client</div>
</div>
<div class="card"><h3 class="en">Individual configs</h3><h3 class="fa" hidden>کانفیگ‌های جداگانه</h3>{cards}</div>
<div class="card"><h3 class="en">Need a client app?</h3><h3 class="fa" hidden>کلاینت موردنیاز</h3>
<div class="apps">
<a href="https://github.com/MatsuriDayo/v2rayNG/releases" target="_blank" rel="noopener">v2rayNG</a>
<a href="https://github.com/MatsuriDayo/nekoray/releases" target="_blank" rel="noopener">NekoBox</a>
<a href="https://apps.apple.com/app/streisand/id6490569503" target="_blank" rel="noopener">Streisand</a>
<a href="https://github.com/ArasTey/ArasClient/releases" target="_blank" rel="noopener">ArasClient</a>
<a href="https://github.com/Happ-proxy/happ-desktop/releases" target="_blank" rel="noopener">Happ</a>
<a href="https://github.com/SagerNet/sing-box/releases" target="_blank" rel="noopener">sing-box</a>
</div></div>
<p class="ft">Powered by <a href="https://github.com/ArasTey/lunel" target="_blank" rel="noopener">Lunel</a> &#183; <a href="https://t.me/imArasTey" target="_blank" rel="noopener">@imArasTey</a></p>
</div>
<script>
function cp(id){{navigator.clipboard.writeText(document.getElementById(id).textContent.trim()).then(function(){{
var t=document.createElement('div');t.textContent='Copied \u2713';t.style.cssText='position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#171b24;border:1px solid #2a3242;color:#4ecb95;padding:8px 18px;border-radius:999px;font-size:13px;z-index:99';document.body.appendChild(t);setTimeout(function(){{t.remove()}},1800)}})}}
function tg(id,btn){{var b=document.getElementById(id);var open=b.style.display!=='block';b.style.display=open?'block':'none';btn.textContent=open?'Hide':'QR'}}
</script></body></html>"""

def _singbox_outbound(url: str) -> dict:
    """vless:// / trojan:// URI -> sing-box outbound. Shadowsocks links pass
    through parsed minimally; unsupported schemes are skipped by caller."""
    import base64 as _b64u
    from urllib.parse import urlparse, parse_qs, unquote

    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    tag = unquote(u.fragment) or "lunel"
    common = {"tag": tag}
    if u.scheme in ("vless", "trojan"):
        inner = {
            "server": u.hostname or "",
            "server_port": u.port or 443,
            "uuid": u.username or "" if u.scheme == "vless" else None,
            "password": u.username or "" if u.scheme == "trojan" else None,
            "tls": {
                "enabled": q.get("security") == "tls",
                "server_name": q.get("sni") or u.hostname or "",
                "utls": {"enabled": True, "fingerprint": q.get("fp", "chrome")} if q.get("fp") else None,
            },
            "transport": {
                "type": "ws",
                "path": q.get("path", "/"),
                "headers": {"Host": q.get("host") or u.hostname or ""},
            } if q.get("type") == "ws" else None,
        }
        common["type"] = u.scheme
        out = {k: v for k, v in inner.items() if v is not None}
        tls = out.get("tls") or {}
        if tls.get("utls") is None:
            tls.pop("utls", None)
        if out.get("transport") is None:
            out.pop("transport", None)
        out.update(common)
        return out
    if u.scheme == "ss":
        userinfo = u.username or ""
        pad = "=" * (-len(userinfo) % 4)
        try:
            method, password = _b64u.b64decode(userinfo + pad).decode().split(":", 1)
        except Exception:
            method, password = "aes-256-gcm", ""
        return {"type": "shadowsocks", "tag": tag, "server": u.hostname or "",
                "server_port": u.port or 443, "method": method, "password": password}
    return {"type": u.scheme, "tag": tag}


def _clash_proxy(url: str) -> dict | None:
    """vless/trojan URI -> Clash Meta proxy map (vless needs Meta)."""
    from urllib.parse import urlparse, parse_qs, unquote

    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    name = unquote(u.fragment) or "lunel"
    if u.scheme == "vless":
        return {"name": name, "type": "vless", "server": u.hostname or "",
                "port": u.port or 443, "uuid": u.username or "",
                "udp": True, "tls": q.get("security") == "tls",
                "servername": q.get("sni") or u.hostname or "",
                "client-fingerprint": q.get("fp", "chrome"),
                "network": "ws", "ws-opts": {"path": q.get("path", "/"),
                "headers": {"Host": q.get("host") or u.hostname or ""}}}
    if u.scheme == "trojan":
        return {"name": name, "type": "trojan", "server": u.hostname or "",
                "port": u.port or 443, "password": u.username or "",
                "udp": True, "sni": q.get("sni") or u.hostname or "",
                "client-fingerprint": q.get("fp", "chrome"),
                "network": "ws", "ws-opts": {"path": q.get("path", "/"),
                "headers": {"Host": q.get("host") or u.hostname or ""}}}
    if u.scheme == "ss":
        import base64 as _b64u

        pad = "=" * (-len(u.username or "") % 4)
        try:
            method, password = _b64u.b64decode((u.username or "") + pad).decode().split(":", 1)
        except Exception:
            return None
        return {"name": name, "type": "ss", "server": u.hostname or "",
                "port": u.port or 443, "cipher": method, "password": password}
    return None


def _clash_quote(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"'


def _clash_inline(p: dict) -> str:
    import json as _json

    return _json.dumps(p, ensure_ascii=False)


router = APIRouter(include_in_schema=False)


async def _resolve_endpoint(request: Request, token: str) -> dict | None:
    """endpoint token -> {instance_id, worker_url, status}"""
    pool = get_pool(request)
    from ..config import settings as _s
    from ..security.token_codec import decode_token

    log.info("resolve enter: token[:20]=%s", token[:20])
    instance_id = decode_token(token, _s.secret_key)
    log.info("resolve: token[:16]=%s decoded=%s", token[:16], instance_id)
    if instance_id is not None:
        row = await pool.fetchrow(
            """
            SELECT i.id, i.status,
                   (SELECT d.domain FROM domains d WHERE d.instance_id = i.id
                     AND d.is_active = TRUE AND d.kind = 'path'
                     ORDER BY d.created_at DESC LIMIT 1) AS endpoint_token,
                   (SELECT dep.node_id FROM deployments dep WHERE dep.instance_id = i.id
                     ORDER BY dep.started_at DESC LIMIT 1) AS node_id
            FROM instances i WHERE i.id = $1
            """,
            instance_id,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT i.id, i.status,
                   (SELECT d.domain FROM domains d WHERE d.instance_id = i.id
                     AND d.is_active = TRUE AND d.kind = 'path'
                     ORDER BY d.created_at DESC LIMIT 1) AS endpoint_token,
                   (SELECT dep.node_id FROM deployments dep WHERE dep.instance_id = i.id
                     ORDER BY dep.started_at DESC LIMIT 1) AS node_id
            FROM instances i
            WHERE i.id IN (SELECT instance_id FROM domains
                            WHERE kind = 'path' AND domain = $1 AND is_active = TRUE)
            """,
            token,
        )
    if row is not None and row["endpoint_token"] is None:
        row = None
    if row is None or row["status"] != "running":
        return None
    from ..services.workers import worker_url_for

    return {
        "instance_id": str(row["id"]),
        "worker_url": worker_url_for(row["node_id"] or "local"),
        "upstream": f"/worker/api/instances/{row['id']}/proxy",
    }


@router.get("/i/{token}")
async def instance_status_page(token: str, request: Request):
    """Browser-friendly view of a proxy endpoint (the path itself is for
    proxy clients, not people)."""
    target = await _resolve_endpoint(request, token)
    if target is None:
        return _page(
            "Endpoint not found",
            "This endpoint doesn't exist or its instance was removed. "
            "If you recently redeployed Lunel without a persistent volume, "
            "create a new instance in the panel and copy its fresh config "
            "from the <b>Config</b> tab.",
            status=404,
        )
    return _page(
        "This endpoint is live",
        "This address is the private transport path for your proxy client — "
        "there is no web page here. Open the Lunel panel, choose your "
        "instance, open the <b>Config</b> tab and copy the "
        "<code>vless://</code> link into your client (v2rayNG, NekoBox, "
        "Streisand, …).",
    )


@router.post("/i/{token}/api/qr")
async def instance_qr_public(token: str, request: Request):
    """QR (SVG) for the subscription page — authorized by the endpoint token."""
    import io

    import qrcode
    import qrcode.image.svg
    from fastapi.responses import Response as _Response

    target = await _resolve_endpoint(request, token)
    if target is None:
        raise HTTPException(status_code=404, detail="unknown endpoint")
    body = await request.json()
    text = str(body.get("text") or "")[:4096]
    if not text:
        raise HTTPException(status_code=400, detail="text required")
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return _Response(content=buf.getvalue(), media_type="image/svg+xml")


@router.get("/i/{token}/sub")
async def instance_subscription(token: str, request: Request):
    """Subscription: ALL protocols of this instance. Auth = endpoint token.
    Formats via ?fmt=: singbox | clash | (default) base64 v2ray list.
    Content host: ?host=, panel-announced host, or request host."""
    import base64 as _b64

    import httpx as _httpx

    from ..config import settings as _settings

    fmt = (request.query_params.get("fmt") or "").strip().lower()

    target = await _resolve_endpoint(request, token)
    if target is None:
        return _page(
            "Endpoint not found",
            "This subscription doesn't exist or its instance was removed. "
            "Create a new instance in the Lunel panel and copy its "
            "subscription URL from the Config tab.",
            status=404,
        )
    pool = get_pool(request)
    inst = await pool.fetchrow(
        "SELECT name, public_host, status FROM instances WHERE id = $1", target["instance_id"]
    )
    if inst is None or inst["status"] != "running":
        return _page("Instance not running",
                     "The subscription will work once the instance is running.", status=503)
    host = (request.query_params.get("host")
            or inst["public_host"]
            or (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
            or request.headers.get("host") or "").split(":")[0]
    if not host:
        return _page("Missing host", "Append ?host=<your-domain> to this URL.", status=400)
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{target['worker_url'].rstrip('/')}{target['upstream']}/core/api/share",
                json={"host": host, "path_prefix": f"/i/{token}", "uuids": []},
                headers={"Authorization": f"Bearer {_settings.worker_token}",
                         "Content-Type": "application/json"},
            )
            resp.raise_for_status()
            configs = [c for c in resp.json().get("links", []) if c.get("share_url")]
            links = [c["share_url"] for c in configs]
    except Exception as exc:
        return _page("Unavailable", f"Could not read the instance configs: {str(exc)[:160]}",
                     status=502)
    title = f"Lunel \u00b7 {inst['name']}"
    from fastapi.responses import Response as _Response

    # ── Browser detection: HTML page for people, raw payload for clients ──
    # Client apps (v2rayNG, NekoBox, sing-box, Clash, Streisand…) send UA
    # fragments that don't look like a browser. Explicit ?fmt= always wins.
    ua = (request.headers.get("user-agent") or "").lower()
    client_markers = ("v2ray", "neko", "sing-box", "singbox", "sfa", "sfi",
                      "clash", "mihomo", "stash", "flclash", "streisand",
                      "happ", "karing", "shadowrocket", "aras")
    looks_like_browser = "mozilla" in ua and not any(m in ua for m in client_markers)

    if looks_like_browser and not fmt:
        from fastapi.responses import HTMLResponse

        return HTMLResponse(_sub_html_page(title, configs, host, f"/i/{token}/sub",
                                           qr_path=f"/i/{token}/api/qr"))

    def _headers(extra: dict | None = None) -> dict:
        h = {
            "profile-title": "base64:" + _b64.b64encode(title.encode()).decode(),
            "subscription-userinfo": "upload=0; download=0; total=0; expire=0",
            "profile-update-interval": "24",
            "profile-web-page-url": f"{request.url.scheme}://{request.headers.get('host', host)}",
            "support-url": "https://t.me/imArasTey",
        }
        if extra:
            h.update(extra)
        return h

    # Format negotiation:
    #   ?fmt=singbox  -> sing-box JSON (Outbounds)
    #   ?fmt=clash    -> Clash YAML (proxies)
    #   default       -> base64 v2ray list (v2rayNG, NekoBox, Streisand, ArasClient)
    if fmt in ("singbox", "sing-box", "sb"):
        import json as _json

        outbounds = [_singbox_outbound(u) for u in links]
        payload = _json.dumps({"outbounds": outbounds}, ensure_ascii=False, indent=2)
        return _Response(content=payload, media_type="application/json",
                         headers=_headers({"subscription-userinfo": "upload=0; download=0; total=0; expire=0"}))

    if fmt in ("clash", "clash-meta", "yaml"):
        proxies = [_clash_proxy(u) for u in links]
        proxies = [p for p in proxies if p]
        names = [p["name"] for p in proxies]
        payload = (
            "port: 7890\nsocks-port: 7891\nallow-lan: false\nmode: rule\nlog-level: warning\n"
            "proxies:\n"
            + "\n".join("  - " + _clash_inline(p) for p in proxies)
            + "\nproxy-groups:\n  - name: Lunel\n    type: select\n    proxies:\n"
            + "".join(f"      - {_clash_quote(n)}\n" for n in names)
            + "rules:\n  - MATCH,Lunel\n"
        )
        return _Response(content=payload, media_type="text/yaml", headers=_headers())

    body = _b64.b64encode("\n".join(links).encode()).decode()
    return _Response(content=body, media_type="text/plain", headers=_headers())


@router.api_route("/i/{token}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"])
async def instance_http_gateway(token: str, path: str, request: Request):
    target = await _resolve_endpoint(request, token)
    if target is None:
        return _page(
            "Endpoint not found",
            "This endpoint doesn't exist or its instance is not running. "
            "Check the panel — if the instance is Running, copy the fresh "
            "config from its <b>Config</b> tab.",
            status=404,
        )

    worker_url = target["worker_url"].rstrip("/")
    url = f"{worker_url}{target['upstream']}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = [(k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP]
    headers.append(("X-Lunel-Endpoint", token))
    from ..config import settings as _cfg

    headers.append(("Authorization", f"Bearer {_cfg.worker_token}"))

    client = httpx.AsyncClient(timeout=None)
    try:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            upstream_req = client.build_request(
                request.method, url, headers=headers, params=None,
            )
            upstream_resp = await client.send(upstream_req, stream=True)
            return StreamingResponse(
                upstream_resp.aiter_raw(),
                status_code=upstream_resp.status_code,
                headers={k: v for k, v in upstream_resp.headers.items()
                         if k.lower() not in HOP_BY_HOP},
                background=_close_client(client, upstream_resp),
            )

        body = await request.body()
        upstream_resp = await client.request(request.method, url, headers=headers, content=body)
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            headers={k: v for k, v in upstream_resp.headers.items()
                     if k.lower() not in HOP_BY_HOP},
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="instance upstream unavailable")


def _close_client(client: httpx.AsyncClient, resp):
    from starlette.background import BackgroundTask

    async def _cleanup() -> None:
        await resp.aclose()
        await client.aclose()

    return BackgroundTask(_cleanup)


@router.websocket("/i/{token}/{path:path}")
async def instance_ws_gateway(ws: WebSocket, token: str, path: str):
    """Frame-level WebSocket relay into the instance's Lunel Core.

    Accept the client up front (so failures produce proper close codes, not
    Starlette's HTTP 403 rejection), then open the upstream through the
    worker's ws-proxy and pump frames in both directions.
    """
    await ws.accept()
    try:
        target = await _resolve_endpoint(ws, token)
    except Exception as _exc:
        import traceback as _tb

        log.error("WS resolve failed: %s | %s", _exc, _tb.format_exc()[-400:])
        target = None
    if target is None:
        await ws.close(code=1008, reason="unknown or inactive instance endpoint")
        return

    # Build the upstream ws URL through the worker's websocket proxy
    # (separate route from the HTTP /proxy path).
    worker_ws = target["worker_url"].replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
    from ..config import settings as _settings

    upstream_url = (
        f"{worker_ws}/worker/api/instances/{target['instance_id']}/ws-proxy/{path}"
        f"?token={_settings.worker_token}"
    )

    # Forward selected client headers so Core sees the real client IP etc.
    client_headers = {}
    for key in ("x-forwarded-for", "x-real-ip", "user-agent"):
        val = ws.headers.get(key)
        if val:
            client_headers[key] = val
    client_headers["x-lunel-endpoint"] = token

    try:
        async with websockets.connect(
            upstream_url,
            additional_headers=client_headers,  # type: ignore[arg-type]
            max_size=None,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
        ) as upstream:
            async def client_to_upstream() -> None:
                try:
                    while True:
                        msg = await ws.receive()
                        if msg["type"] == "websocket.disconnect":
                            return
                        data = msg.get("bytes")
                        if data is not None:
                            await upstream.send(data)
                        else:
                            text = msg.get("text")
                            if text is not None:
                                await upstream.send(text)
                except (WebSocketDisconnect, Exception):
                    return

            async def upstream_to_client() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, (bytes, bytearray)):
                            await ws.send_bytes(bytes(message))
                        else:
                            await ws.send_text(message)
                except Exception:
                    return

            done, pending = await asyncio.wait(
                {
                    asyncio.create_task(client_to_upstream()),
                    asyncio.create_task(upstream_to_client()),
                },
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
    except (websockets.exceptions.WebSocketException, OSError) as exc:
        log.info("gateway ws upstream failed: %s", type(exc).__name__)
        await ws.close(code=1014, reason="upstream unavailable")
        return
    finally:
        try:
            await ws.close()
        except Exception:
            pass
